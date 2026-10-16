"""Normal contributor intake must remain usable after native persistence."""
import json

from auto_task import store
from auto_task.collection_access import acl_data, get_acl
from auto_task.entities import AiResource, TaskJob
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
            return {"passed": True, "issues": [], "corrected_answer": ""}
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
