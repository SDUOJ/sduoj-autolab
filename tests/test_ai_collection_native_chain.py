"""Normal contributor intake must remain usable after native persistence."""
import json

import pytest
from fastapi import HTTPException

from auto_task import store
from auto_task.collection_access import acl_data, get_acl
from auto_task.entities import AiResource, TaskJob
from auto_task.access import authorize_execution
from auto_task.resources import get_resource
from auto_task.worker import run_once
from db import ProblemGroup, ProblemSubjective
from tests.test_ai_backend import ADMIN
from tests.test_ai_collection_collaboration import (
    CONTRIBUTOR, as_user, collection, collaboration_client, database, client, invite_and_accept,
)


def model_fixture(monkeypatch):
    calls = []
    question = {"question_type": "short_answer", "language": "zh", "original": "FIFO?",
                "zh": "FIFO?", "options": [], "answer": "", "explanation": "", "tags": []}

    def ask(self, prompt, schema, images=()):
        calls.append(schema["title"])
        if schema["title"] == "Moderation":
            return {"decision": "normal", "reason": ""}
        if schema["title"] == "Extraction":
            return {"questions": [dict(question)]}
        if schema["title"] == "Verification":
            return {"passed": True, "evidence": ["Definition checked"], "corrected_answer": "", "explanation": "Consistent"}
        return {"answer": "FIFO", "explanation": "Definition", "tags": []}

    monkeypatch.setattr("auto_task.runtime.Context.ask", ask)
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "1")
    return calls


def test_assigned_native_intake_validates_open_grant_before_marking_it_submitted(
        collaboration_client, monkeypatch):
    c = collaboration_client
    model_fixture(monkeypatch)
    collection()
    invite_and_accept(c)
    as_user(c, ADMIN)
    assigned = c.post("/auto-task/ai/collection-collaboration/shared/assignments", json={
        "assignee_id": "8", "title": "Chapter one"})
    assert assigned.status_code == 200
    assignment_id = assigned.json()["data"]["assignment"]["id"]
    as_user(c, CONTRIBUTOR)
    response = c.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Chapter one", "text": "FIFO?",
        "batch_id": "assigned-native", "assignment_id": assignment_id})
    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    assert run_once()
    with store.transaction() as session:
        job = session.query(TaskJob).get(task_id)
        assert job.status == "success", job.error
        assert session.query(ProblemGroup).count() == session.query(ProblemSubjective).count() == 1
        parent = session.query(AiResource).get("shared")
        data = json.loads(parent.data)
        assert data["native_batches"]["assigned-native"]
        assignment = next(item for item in acl_data(get_acl(session, parent))["assignments"]
                          if item["id"] == assignment_id)
        assert assignment["status"] == "submitted"


def native_intake(c, monkeypatch, assigned=False, auto_solve=False, auto_verify=False):
    calls = model_fixture(monkeypatch)
    collection()
    invite_and_accept(c)
    assignment_id = None
    if assigned:
        as_user(c, ADMIN)
        response = c.post("/auto-task/ai/collection-collaboration/shared/assignments", json={
            "assignee_id": "8", "title": "Chapter one"})
        assert response.status_code == 200
        assignment_id = response.json()["data"]["assignment"]["id"]
    as_user(c, CONTRIBUTOR)
    response = c.post("/auto-task/ai/question-workflows/intake", json={
        "collection_id": "shared", "title": "Chapter one", "text": "FIFO?",
        "batch_id": "native-chain", "assignment_id": assignment_id,
        "auto_solve": auto_solve, "auto_verify": auto_verify})
    assert response.status_code == 200
    assert run_once()
    with store.transaction() as session:
        job = session.query(TaskJob).get(response.json()["data"]["taskId"])
        assert job.status == "success", job.error
        question_id = json.loads(job.result)["question_ids"][0]
    return question_id, calls


@pytest.mark.parametrize("assigned", [False, True])
def test_native_contributor_reads_follows_up_and_completes_automatic_solve_verify_chain(
        collaboration_client, monkeypatch, assigned):
    c = collaboration_client
    question_id, calls = native_intake(c, monkeypatch, assigned, auto_solve=True, auto_verify=True)
    listed = c.get("/auto-task/ai/question-workflows/collections/shared/questions").json()["data"]
    assert listed["unavailable_numbers"] == []
    assert [item["resource"]["id"] for item in listed["rows"]] == [question_id]
    assert c.get("/auto-task/ai/resources/" + question_id).status_code == 200
    assert run_once()  # solve
    assert run_once()  # verify
    with store.transaction() as session:
        jobs = session.query(TaskJob).order_by(TaskJob.created_at).all()
        assert [job.task_type for job in jobs] == ["question.extract", "question.solve", "question.verify"]
        assert all(job.status == "success" for job in jobs), [(job.task_type, job.error) for job in jobs]
        assert json.loads(jobs[1].payload)["collaboration_grant"]["assignment_id"] is None
        native = session.query(ProblemSubjective).one()
        assert json.loads(native.config)["judgeConfig"][0]["answer"] == "FIFO"
    assert "Extraction" in calls and "Answer" in calls and "Verification" in calls
    followup = c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "manual-followup"})
    assert followup.status_code == 200, followup.text
    assert run_once()
    with store.transaction() as session:
        assert session.query(TaskJob).get(followup.json()["data"]["taskId"]).status == "success"
        row = session.query(AiResource).get(question_id)
        native = json.loads(row.data)["native"]
        # Collection permission never grants access to the entire native group API.
        group_id = native["group_id"]
    from tests.test_native_bank import bank_client
    assert bank_client(CONTRIBUTOR).get("/question-bank/groups/%s/questions" % group_id).status_code == 403


@pytest.mark.parametrize("invalid_binding", [
    "no-parent", "different-source", "different-owner", "different-scope",
    "unregistered-group", "removed-native-pid", "detached-item", "other-teacher-group",
])
def test_collection_grant_never_unlocks_unregistered_or_mismatched_native_bindings(
        collaboration_client, monkeypatch, invalid_binding):
    c = collaboration_client
    question_id, _ = native_intake(c, monkeypatch)
    with store.transaction() as session:
        row = session.query(AiResource).get(question_id)
        data = json.loads(row.data)
        parent = session.query(AiResource).get("shared")
        manifest = json.loads(parent.data)
        group = session.query(ProblemGroup).get(data["native"]["group_id"])
        if invalid_binding == "no-parent":
            row.parent_id = None
        elif invalid_binding == "different-source":
            data["source_collection"] = "another-collection"
        elif invalid_binding == "different-owner":
            row.owner_id = "42"
        elif invalid_binding == "different-scope":
            row.psid = 99
        elif invalid_binding == "unregistered-group":
            manifest["native_batches"] = {}
        elif invalid_binding == "removed-native-pid":
            group.problemInfo = "[]"
        elif invalid_binding == "detached-item":
            manifest["items"][0]["status"] = "detached"
        else:
            # Even another group created by the same teacher is outside this grant.
            other = ProblemGroup(name="Unrelated private group", type=1,
                                 problemInfo=group.problemInfo, username="teacher")
            session.add(other); session.flush()
            data["native"]["group_id"] = other.gid
        row.data, parent.data = store.dumps(data), store.dumps(manifest)
    assert c.get("/auto-task/ai/resources/" + question_id).status_code == 403
    assert c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "mismatched-followup"}).status_code == 403


def test_native_grant_honors_viewer_and_contributor_write_boundaries(collaboration_client, monkeypatch):
    c = collaboration_client
    question_id, _ = native_intake(c, monkeypatch)
    with store.transaction() as session:
        acl = get_acl(session, session.query(AiResource).get("shared"))
        data = acl_data(acl)
        data["members"][0]["role"] = "viewer"
        acl.data = store.dumps(data)
    assert c.get("/auto-task/ai/resources/" + question_id).status_code == 200
    assert c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "viewer-followup"}).status_code == 403
    with store.transaction() as session:
        acl = get_acl(session, session.query(AiResource).get("shared"))
        data = acl_data(acl)
        data["members"][0]["role"] = "contributor"
        acl.data = store.dumps(data)
        row = session.query(AiResource).get(question_id)
        value = json.loads(row.data); value["submitted_by"] = "someone-else"
        row.data = store.dumps(value)
    assert c.get("/auto-task/ai/resources/" + question_id).status_code == 200
    assert c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "another-contributor-followup"}).status_code == 403


def test_revocation_after_native_binding_blocks_reads_writes_and_already_queued_chain(
        collaboration_client, monkeypatch):
    c = collaboration_client
    question_id, calls = native_intake(c, monkeypatch, auto_solve=True)
    with store.transaction() as session:
        row = session.query(TaskJob).filter_by(task_type="question.solve").one()
        job = {column.name: getattr(row, column.name) for column in TaskJob.__table__.columns}
    authorize_execution(job)
    as_user(c, ADMIN)
    assert c.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke").status_code == 200
    as_user(c, CONTRIBUTOR)
    assert c.get("/auto-task/ai/resources/" + question_id).status_code == 403
    assert c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "revoked-followup"}).status_code == 403
    with pytest.raises(store.LeaseLost):
        authorize_execution(job)
    calls.clear()
    assert run_once()
    assert calls == []
    with store.transaction() as session:
        assert json.loads(session.query(ProblemSubjective).one().config)["judgeConfig"][0]["answer"] == ""


def test_owner_role_revocation_blocks_delegated_native_writes(collaboration_client, monkeypatch):
    c = collaboration_client
    question_id, _ = native_intake(c, monkeypatch)
    from auto_task import access
    fetch = access.fetch_actor
    monkeypatch.setattr(access, "fetch_actor", lambda subject, fresh=False:
                        {**ADMIN, "roles": ["user"]} if str(subject) == "7" else fetch(subject, fresh))
    assert c.post("/auto-task/ai/question-workflows/questions/" + question_id + "/followup", json={
        "task_type": "question.solve", "operation_key": "revoked-owner"}).status_code == 403
    with store.transaction() as session:
        assert session.query(TaskJob).filter_by(task_type="question.solve").count() == 0


def test_native_binding_and_batch_registry_remain_server_owned(collaboration_client, monkeypatch):
    c = collaboration_client
    question_id, _ = native_intake(c, monkeypatch)
    view = c.get("/auto-task/ai/resources/" + question_id).json()["data"]
    assert c.post("/auto-task/ai/resources/" + question_id + "/update", json={
        "version": view["version"], "data": {"native": view["data"]["native"]}}).status_code == 400
    as_user(c, ADMIN)
    parent = c.get("/auto-task/ai/resources/shared").json()["data"]
    assert c.post("/auto-task/ai/resources/shared/update", json={
        "version": parent["version"], "data": {"native_batches": {}}}).status_code == 400


def test_result_transaction_rechecks_a_revoked_native_collection_grant(collaboration_client, monkeypatch):
    c = collaboration_client
    question_id, _ = native_intake(c, monkeypatch, auto_solve=True)
    with store.transaction() as session:
        row = session.query(TaskJob).filter_by(task_type="question.solve").one()
        job_id = row.id
        job = {column.name: getattr(row, column.name) for column in TaskJob.__table__.columns}
    authorize_execution(job)
    as_user(c, ADMIN)
    assert c.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke").status_code == 200
    from auto_task.handlers import apply_result
    with pytest.raises(HTTPException) as denied:
        with store.transaction() as session:
            row = session.query(AiResource).get(question_id)
            apply_result(session, session.query(TaskJob).get(job_id),
                         {"answer": "Stale candidate", "explanation": "", "tags": []}, row.version)
    assert denied.value.status_code == 403
    with store.transaction() as session:
        assert json.loads(session.query(ProblemSubjective).one().config)["judgeConfig"][0]["answer"] == ""
