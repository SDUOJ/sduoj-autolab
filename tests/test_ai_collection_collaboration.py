"""Collection collaboration authorization and revocation tests."""

import json

import pytest

from db import ojCourse
from auto_task import store
from auto_task.collection_access import acl_data, get_acl
from auto_task.entities import AiResource, TaskJob
from auto_task.worker import run_once
from server import auto_task_ai as api
from tests.test_ai_backend import ADMIN, USER, client, database

CONTRIBUTOR = {"userId": 8, "username": "collaborator", "roles": ["user"], "groups": [10]}
OTHER = {"userId": 9, "username": "other", "roles": ["user"], "groups": [10]}


@pytest.fixture
def collaboration_client(client, database):
    ojCourse.__table__.create(database, checkfirst=True)
    from server.auto_task_collaboration import router
    client.app.include_router(router, prefix="/auto-task/ai")
    yield client
    ojCourse.__table__.drop(database, checkfirst=True)


def collection(owner="7", course_id=None):
    with store.transaction() as session:
        row = AiResource(id="shared", kind="collection", owner_id=owner,
                         owner_username="teacher" if owner == "7" else "student",
                         course_id=course_id, data=store.dumps({"title": "Shared", "question_ids": []}),
                         search_text="Shared", state="ready")
        session.add(row)
    return row


def as_user(client, user):
    client.app.dependency_overrides[api.cover_header] = lambda: user


def invite_and_accept(client, role="contributor", course_id=None, target=CONTRIBUTOR):
    as_user(client, ADMIN)
    response = client.post("/auto-task/ai/collection-collaboration/shared/members", json={
        "subject_id": str(target["userId"]), "username": target["username"],
        "role": role, "course_id": course_id})
    assert response.status_code == 200, response.text
    as_user(client, target)
    response = client.post("/auto-task/ai/collection-collaboration/shared/accept")
    assert response.status_code == 200, response.text


def test_invitation_is_bound_to_stable_oj_identity_and_course_scope(collaboration_client):
    client = collaboration_client
    with store.transaction() as session:
        session.add(ojCourse(course_id=1, course_name="Data Structures", group_id=10,
                             tag="授课", c_ids="[]", creator_username="teacher",
                             manager_groups="[]", ext_config="{}"))
    collection(course_id=1)
    as_user(client, ADMIN)
    wrong_scope = client.post("/auto-task/ai/collection-collaboration/shared/members", json={
        "subject_id": "8", "username": "collaborator", "role": "contributor"})
    assert wrong_scope.status_code == 400
    invited = client.post("/auto-task/ai/collection-collaboration/shared/members", json={
        "subject_id": "8", "username": "collaborator", "role": "contributor", "course_id": 1})
    assert invited.status_code == 200

    as_user(client, CONTRIBUTOR)
    assert client.get("/auto-task/ai/collection-collaboration/list").json()["data"][0]["role"] == "pending"
    as_user(client, {**CONTRIBUTOR, "username": "wrong"})
    assert client.post("/auto-task/ai/collection-collaboration/shared/accept").status_code == 409
    as_user(client, CONTRIBUTOR)
    assert client.post("/auto-task/ai/collection-collaboration/shared/accept").status_code == 200
    detail = client.get("/auto-task/ai/collection-collaboration/shared").json()["data"]
    assert detail["role"] == "contributor"
    assert detail["members"][0]["subject_id"] == "8"

    as_user(client, {**CONTRIBUTOR, "groups": []})
    assert client.get("/auto-task/ai/collection-collaboration/shared").status_code == 403


def test_owner_assigns_work_and_contributor_reserves_own_idempotent_batch(collaboration_client):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    as_user(client, ADMIN)
    response = client.post("/auto-task/ai/collection-collaboration/shared/assignments", json={
        "assignee_id": "8", "title": "第 1-10 题", "description": "第一章"})
    assert response.status_code == 200, response.text
    assignment = response.json()["data"]["assignment"]

    as_user(client, CONTRIBUTOR)
    body = {"collection_id": "shared", "title": "Batch", "text": "1. FIFO?",
            "batch_id": "chapter-1", "assignment_id": assignment["id"]}
    first = client.post("/auto-task/ai/question-workflows/intake", json=body)
    assert first.status_code == 200, first.text
    again = client.post("/auto-task/ai/question-workflows/intake", json=body)
    assert again.status_code == 200 and again.json()["data"]["replay"]
    with store.transaction() as session:
        job = session.query(TaskJob).get(first.json()["data"]["taskId"])
        grant = json.loads(job.payload)["collaboration_grant"]
        assert grant == {"collection_id": "shared", "subject_id": "8",
                         "assignment_id": assignment["id"]}
        state = acl_data(get_acl(session, session.query(AiResource).get("shared")))
        reservation = state["batch_reservations"][0]
        assert reservation["subject_id"] == "8" and reservation["task_id"] == job.id

    invite_and_accept(client, target=OTHER)
    as_user(client, OTHER)
    conflict = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Other", "text": "Different",
        "batch_id": "chapter-1"})
    assert conflict.status_code == 409


def test_viewer_cannot_submit_or_edit_collection(collaboration_client):
    client = collaboration_client
    collection()
    invite_and_accept(client, role="viewer")
    response = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "text": "Question", "title": "Denied"})
    assert response.status_code == 403
    assert client.get("/auto-task/ai/question-workflows/collections/shared/manifest").status_code == 200
    assert client.post("/auto-task/ai/question-workflows/collections/shared/items/1/detach").status_code == 403


def test_revocation_blocks_worker_before_any_model_call_and_hides_private_questions(collaboration_client, monkeypatch):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    with store.transaction() as session:
        session.add(AiResource(id="private-question", kind="question", owner_id="7",
                               owner_username="teacher", data=store.dumps({
                                   "question_type": "short_answer", "zh": "Teacher private"}),
                               search_text="Teacher private", state="ready"))
        session.add(AiResource(id="shared-question", kind="question", owner_id="7",
                               owner_username="teacher", parent_id="shared", data=store.dumps({
                                   "question_type": "short_answer", "zh": "Shared question"}),
                               search_text="Shared question", state="ready"))
        session.add(AiResource(id="own-question", kind="question", owner_id="7",
                               owner_username="teacher", parent_id="shared", data=store.dumps({
                                   "question_type": "short_answer", "zh": "Own question",
                                   "submitted_by": "8"}), search_text="Own question", state="ready"))
        collection_row = session.query(AiResource).get("shared")
        collection_row.data = store.dumps({"title": "Shared", "question_ids": [
            "shared-question", "own-question", "private-question"], "items": [
                {"number": 1, "question_id": "shared-question", "status": "active", "batch_id": "owner"},
                {"number": 2, "question_id": "own-question", "status": "active", "batch_id": "mine"},
                {"number": 3, "question_id": "private-question", "status": "active", "batch_id": "owner"}],
            "batches": [], "next_question_number": 4})
    as_user(client, CONTRIBUTOR)
    assert client.get("/auto-task/ai/resources/shared-question").status_code == 200
    assert client.post("/auto-task/ai/resources/shared-question/update", json={
        "version": 1, "data": {"zh": "unauthorized edit"}}).status_code == 403
    assert client.post("/auto-task/ai/resources/own-question/update", json={
        "version": 1, "data": {"zh": "own edit"}}).status_code == 200
    assert client.get("/auto-task/ai/resources/private-question").status_code == 403
    listed = client.get("/auto-task/ai/question-workflows/collections/shared/questions").json()["data"]
    assert {item["resource"]["id"] for item in listed["rows"]} == {"shared-question", "own-question"}
    assert listed["unavailable_numbers"] == [3]
    submitted = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Revoked", "text": "FIFO", "batch_id": "revoked"})
    assert submitted.status_code == 200, submitted.text

    as_user(client, ADMIN)
    assert client.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke").status_code == 200
    called = []
    monkeypatch.setattr("auto_task.runtime.Context.ask", lambda *args, **kwargs: called.append(True))
    assert run_once()
    assert called == []
    with store.transaction() as session:
        assert session.query(TaskJob).get(submitted.json()["data"]["taskId"]).status == "failed"
    as_user(client, CONTRIBUTOR)
    assert client.get("/auto-task/ai/collection-collaboration/shared").status_code == 403


def test_non_owner_cannot_manage_members_or_assignments(collaboration_client):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    as_user(client, CONTRIBUTOR)
    assert client.post("/auto-task/ai/collection-collaboration/shared/members", json={
        "subject_id": "9", "username": "other", "role": "viewer"}).status_code == 403
    assert client.post("/auto-task/ai/collection-collaboration/shared/assignments", json={
        "assignee_id": "8", "title": "self assigned"}).status_code == 403


def test_collaboration_views_hide_other_members_private_batch_reservations(collaboration_client):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    as_user(client, CONTRIBUTOR)
    own = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Mine", "text": "Q1", "batch_id": "mine"})
    assert own.status_code == 200
    invite_and_accept(client, target=OTHER)
    as_user(client, OTHER)
    other = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Other", "text": "Q2", "batch_id": "other"})
    assert other.status_code == 200
    assert client.get("/auto-task/ai/jobs/" + own.json()["data"]["taskId"]).status_code == 403
    assert client.get("/auto-task/ai/jobs/" + other.json()["data"]["taskId"]).status_code == 200
    view = client.get("/auto-task/ai/collection-collaboration/shared").json()["data"]
    assert [item["batch_id"] for item in view["batch_reservations"]] == ["other"]

    as_user(client, ADMIN)
    assert client.get("/auto-task/ai/jobs/" + own.json()["data"]["taskId"]).status_code == 200
    owner_view = client.get("/auto-task/ai/collection-collaboration/shared").json()["data"]
    assert {item["batch_id"] for item in owner_view["batch_reservations"]} == {"mine", "other"}


def test_revoking_one_assignment_invalidates_only_that_workers_grant(collaboration_client, monkeypatch):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    as_user(client, ADMIN)
    assignment = client.post("/auto-task/ai/collection-collaboration/shared/assignments", json={
        "assignee_id": "8", "title": "Assigned"}).json()["data"]["assignment"]
    as_user(client, CONTRIBUTOR)
    submitted = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Assigned", "text": "Q",
        "batch_id": "assigned", "assignment_id": assignment["id"]})
    assert submitted.status_code == 200
    with store.transaction() as session:
        collection_row = session.query(AiResource).get("shared")
        acl = get_acl(session, collection_row)
        data = acl_data(acl)
        data["assignments"][0]["status"] = "cancelled"
        acl.data, acl.version = store.dumps(data), acl.version + 1
    called = []
    monkeypatch.setattr("auto_task.runtime.Context.ask", lambda *args, **kwargs: called.append(True))
    assert run_once()
    assert called == []
    with store.transaction() as session:
        assert session.query(TaskJob).get(submitted.json()["data"]["taskId"]).status == "failed"


def test_revocation_also_stops_already_chained_question_tasks(collaboration_client, monkeypatch):
    client = collaboration_client
    collection()
    invite_and_accept(client)
    calls = []
    question = {"question_type": "short_answer", "language": "zh", "original": "FIFO?",
                "zh": "FIFO?", "options": [], "answer": "", "explanation": "", "tags": []}
    def ask(self, prompt, schema, images=()):
        calls.append(schema["title"])
        if schema["title"] == "Moderation":
            return {"decision": "normal", "reason": ""}
        if schema["title"] == "Extraction":
            return {"questions": [question]}
        return {"answer": "FIFO", "explanation": "definition", "tags": []}
    monkeypatch.setattr("auto_task.runtime.Context.ask", ask)
    as_user(client, CONTRIBUTOR)
    submitted = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Chain", "text": "FIFO",
        "batch_id": "chain", "auto_solve": True})
    assert submitted.status_code == 200
    assert run_once()
    with store.transaction() as session:
        solve = session.query(TaskJob).filter_by(task_type="question.solve").one()
        assert solve.status == "pending"
        assert json.loads(solve.payload)["collaboration_grant"]["subject_id"] == "8"
    calls.clear()
    as_user(client, ADMIN)
    revoked = client.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke")
    assert revoked.status_code == 200, revoked.text
    assert run_once()
    assert calls == []
    with store.transaction() as session:
        assert session.query(TaskJob).filter_by(task_type="question.solve").one().status == "failed"


def test_fresh_identity_allows_only_current_contributor_intake_grant(collaboration_client, monkeypatch):
    from auto_task.access import authorize_execution
    client = collaboration_client
    collection()
    invite_and_accept(client)
    response = client.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Authorized", "text": "FIFO?", "batch_id": "fresh-grant"})
    assert response.status_code == 200, response.text
    with store.transaction() as session:
        row = session.query(TaskJob).get(response.json()["data"]["taskId"])
        job = {column.name: getattr(row, column.name) for column in TaskJob.__table__.columns}
        assert json.loads(job["payload"])["native_bank_owner"] == "7"
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "1")
    authorize_execution(job)
    forged = {**job, "payload": store.dumps({**json.loads(job["payload"]), "collaboration_grant": {
        "collection_id": "other-collection", "subject_id": "8"}})}
    with pytest.raises(store.LeaseLost):
        authorize_execution(forged)
    as_user(client, ADMIN)
    assert client.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke").status_code == 200
    with pytest.raises(store.LeaseLost):
        authorize_execution(job)
