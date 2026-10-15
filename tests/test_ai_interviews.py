import hashlib
import json
import os
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import (AutoTaskRun, AutoTaskRunLog, Base, ProblemGroup, ProblemSet,
                ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail, ProblemSubjective)
from auto_task import store
from auto_task.entities import AiFile, AiFileLink, AiResource, AiSetting, TABLES, TaskJob
from server import auto_task_ai
from server import auto_task_interviews


@compiles(LONGTEXT, "sqlite")
def sqlite_text(*_, **kwargs):
    return "TEXT"


@compiles(BIGINT, "sqlite")
@compiles(TINYINT, "sqlite")
def sqlite_integer(*_, **kwargs):
    return "INTEGER"


STUDENT = {"userId": 42, "username": "student", "roles": ["user"], "groups": []}
TEACHER = {"userId": 7, "username": "teacher", "roles": ["admin"], "groups": []}


@pytest.fixture
def interview_client(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [AutoTaskRun.__table__, AutoTaskRunLog.__table__, ProblemGroup.__table__, ProblemSet.__table__,
              ProblemSetAnswerSheet.__table__, ProblemSetAnswerSheetDetail.__table__, ProblemSubjective.__table__, *TABLES]
    Base.metadata.create_all(engine, tables=tables)
    monkeypatch.setattr(store, "_factory", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setenv("AUTOLAB_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOLAB_AI_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTOLAB_AI_WAKE", "0")
    async def user_id(username):
        return 42 if username == "student" else None
    monkeypatch.setattr(auto_task_interviews, "getUserId", user_id)
    with store.transaction() as session:
        session.add(AiSetting(id="runtime", data='{"max_concurrency":2,"per_user_concurrency":1}'))
    identity = {"value": TEACHER}
    app = FastAPI()
    app.include_router(auto_task_ai.router)
    app.include_router(auto_task_interviews.router, prefix="/auto-task/ai")
    app.dependency_overrides[auto_task_ai.cover_header] = lambda: identity["value"]
    yield TestClient(app), identity
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def invitation_payload(**changes):
    value = {
        "operation_key": "invite-1",
        "student_id": "42",
        "student_username": "student",
        "title": "Lab acceptance",
        "question": "Explain this implementation",
        "code": "print('ok')",
        "rubric": [{"name": "correctness", "max_score": 10}],
        "required_tracks": ["video"],
    }
    value.update(changes)
    return value


def create_invitation(client, **changes):
    response = client.post("/auto-task/ai/interviews/invite", json=invitation_payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_invitation_replay_and_assigned_student_visibility(interview_client):
    client, identity = interview_client
    created = create_invitation(client)
    replay = create_invitation(client)
    assert replay["id"] == created["id"]
    assert replay["replay"] is True

    identity["value"] = STUDENT
    result = client.post("/auto-task/ai/interviews/list", json={"scope": "assigned"})
    assert result.status_code == 200, result.text
    row = result.json()["data"]["rows"][0]
    assert row["role"] == "student"
    assert row["data"]["native_source_bound"] is False
    assert "native_source" not in row["data"]
    assert row["data"]["evidence_policy"]["visibility"] == "assigned_student_and_authorized_teachers"


def test_list_can_filter_teacher_review_terminal_states(interview_client):
    client, identity = interview_client
    created = create_invitation(client)
    for state in ("approved", "rejected", "adopted", "failed"):
        with store.transaction() as session:
            row = session.query(AiResource).get(created["id"])
            data = json.loads(row.data)
            data["status"] = "completed"
            row.data, row.state = store.dumps(data), state
        result = client.post("/auto-task/ai/interviews/list", json={"scope": "managed", "status": state})
        assert result.json()["data"]["totalNum"] == 1
        assert result.json()["data"]["rows"][0]["state"] == state
        assert client.post("/auto-task/ai/interviews/list", json={"status": "running"}).json()["data"]["totalNum"] == 0


def test_failed_worker_is_visible_in_interview_list(interview_client):
    client, identity = interview_client
    created = create_invitation(client)
    with store.transaction() as session:
        row = session.query(AiResource).get(created["id"])
        data = json.loads(row.data)
        data["status"] = "running"
        row.data = store.dumps(data)
        job = store.submit(session, TEACHER, "interview.advance", {}, "failure", row)
        job.status = "failed"
    result = client.post("/auto-task/ai/interviews/list", json={"status": "failed"}).json()["data"]
    assert result["totalNum"] == 1
    assert result["rows"][0]["effective_status"] == "failed"


def test_native_invitation_rejects_mismatched_student_identity(interview_client):
    client, _ = interview_client
    with store.transaction() as session:
        session.add(ProblemSet(psid=1, name="lab", type=1, groupInfo='[{"gid":1}]', config="{}", username="teacher", groupId=1, tag="test"))
        session.add(ProblemGroup(gid=1, name="subjective", type=1, problemInfo='[{"pid":1,"score":10}]', username="teacher"))
        session.add(ProblemSubjective(pid=1, type=2, config='{"judgeConfig":[{"name":"correctness","score":10}]}', description="Explain", username="teacher"))
        session.add(ProblemSetAnswerSheet(asid=1, psid=1, username="student"))
        session.flush()
        session.add(ProblemSetAnswerSheetDetail(asd_id=1, asid=1, gid=1, pid=1, answer='["report"]'))
    response = client.post("/auto-task/ai/interviews/invite", json=invitation_payload(
        operation_key="mismatched-student", student_id="99", psid=1, rubric=[],
        native_source={"gid": 1, "pid": 1, "student_username": "student"},
    ))
    assert response.status_code == 409


def test_media_manifest_blocks_missing_segments_and_freezes_answer_evidence(interview_client):
    client, identity = interview_client
    created = create_invitation(client)
    resource_id = created["id"]
    sha = hashlib.sha256(b"video").hexdigest()
    with store.transaction() as session:
        row = session.query(AiResource).get(resource_id)
        data = json.loads(row.data)
        data.update(status="awaiting_answer", turns=[{"question": "Why?", "answer": None}])
        row.data, row.version = store.dumps(data), row.version + 1
        for index in range(2):
            file_id = "clip-" + str(index)
            session.add(AiFile(id=file_id, owner_id="42", resource_id=resource_id, filename=file_id + ".webm",
                               media_type="video/webm", path=file_id + ".webm", sha256=sha, size=5))
            session.add(AiFileLink(file_id=file_id, resource_id=resource_id))
    identity["value"] = STUDENT
    first = client.post(f"/auto-task/ai/interviews/{resource_id}/media/attach", json={
        "file_id": "clip-0", "turn_sequence": 0, "track": "video", "segment_index": 0,
        "segment_count": 2, "started_ms": 0, "duration_ms": 5000, "sha256": sha,
    })
    assert first.status_code == 200, first.text
    assert first.json()["data"]["manifest"]["groups"][0]["missing_segments"] == [1]
    oversized = client.post(f"/auto-task/ai/interviews/{resource_id}/media/attach", json={
        "file_id": "clip-1", "turn_sequence": 0, "track": "video", "segment_index": 1,
        "segment_count": 2, "started_ms": 5000, "duration_ms": 61000, "sha256": sha,
    })
    assert oversized.status_code == 400
    blocked = client.post(f"/auto-task/ai/interviews/{resource_id}/responses", json={
        "operation_key": "answer-1", "sequence": 0, "text": "Because it is linear.", "file_ids": ["clip-0"],
    })
    assert blocked.status_code == 409
    second = client.post(f"/auto-task/ai/interviews/{resource_id}/media/attach", json={
        "file_id": "clip-1", "turn_sequence": 0, "track": "video", "segment_index": 1,
        "segment_count": 2, "started_ms": 5000, "duration_ms": 5000, "sha256": sha,
    })
    assert second.json()["data"]["manifest"]["complete"] is True
    answer = client.post(f"/auto-task/ai/interviews/{resource_id}/responses", json={
        "operation_key": "answer-1", "sequence": 0, "text": "Because it is linear.",
        "file_ids": ["clip-0", "clip-1"],
    })
    assert answer.status_code == 200, answer.text
    assert client.get("/auto-task/ai/jobs/" + answer.json()["data"]["taskId"]).status_code == 403
    session_view = client.get(f"/auto-task/ai/interviews/{resource_id}/session")
    assert session_view.json()["data"]["job"]["status"] == "pending"
    with store.transaction() as session:
        turn = json.loads(session.query(AiResource).get(resource_id).data)["turns"][0]
        assert turn["answer_evidence"]["text_sha256"] == store.digest("Because it is linear.")
        assert [item["id"] for item in turn["answer_evidence"]["files"]] == ["clip-0", "clip-1"]


def test_expiration_is_persisted_and_teacher_renews_as_new_invitation(interview_client):
    client, identity = interview_client
    created = create_invitation(client)
    with store.transaction() as session:
        row = session.query(AiResource).get(created["id"])
        data = json.loads(row.data)
        data["expires_at"] = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
        row.data, row.version = store.dumps(data), row.version + 1
    identity["value"] = STUDENT
    session_result = client.get(f"/auto-task/ai/interviews/{created['id']}/session")
    assert session_result.status_code == 200
    expired = session_result.json()["data"]
    assert expired["effective_status"] == "expired"
    identity["value"] = TEACHER
    renewed = client.post(f"/auto-task/ai/interviews/{created['id']}/renew", json={
        "operation_key": "renew-1", "version": expired["version"], "expires_in_hours": 24,
    })
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["data"]["id"] != created["id"]
    assert renewed.json()["data"]["data"]["renewed_from"] == created["id"]


def test_teacher_review_adopts_only_the_bound_native_answer(interview_client):
    client, _ = interview_client
    with store.transaction() as session:
        session.add(ProblemSet(psid=1, name="lab", type=1, groupInfo='[{"gid":1}]', config="{}", username="teacher", groupId=1, tag="test"))
        session.add(ProblemGroup(gid=1, name="subjective", type=1, problemInfo='[{"pid":1,"score":10}]', username="teacher"))
        session.add(ProblemSubjective(pid=1, type=2, config='{"judgeConfig":[{"name":"correctness","score":10}]}', description="Explain", username="teacher"))
        session.add(ProblemSetAnswerSheet(asid=1, psid=1, username="student"))
        session.flush()
        session.add(ProblemSetAnswerSheetDetail(asd_id=1, asid=1, gid=1, pid=1, answer='["report"]'))
    created = create_invitation(client, psid=1, rubric=[], native_source={"gid": 1, "pid": 1, "student_username": "student"})
    candidate = {
        "question": "", "focus": "done", "complete": True, "summary": "Evidence checked",
        "score": 80, "confidence": 0.9,
        "criteria": [{"name": "correctness", "max_score": 10, "score": 8, "evidence": "turn 1"}],
        "previous_answer_evaluation": "", "source_type": "report", "source_start_line": 1,
        "source_end_line": 1, "integrity_conclusion": "pass",
    }
    with store.transaction() as session:
        row = session.query(AiResource).get(created["id"])
        job = store.submit(session, TEACHER, "interview.advance", {"resource_version": row.version}, "candidate", row)
        job.status = "success"
        job.result = store.dumps(candidate)
        job.finished_at = datetime.utcnow()
        data = json.loads(row.data)
        data.update(status="completed", candidate=candidate)
        row.data, row.version, row.state = store.dumps(data), row.version + 1, "review"
        version = row.version
    reviewed = client.post(f"/auto-task/ai/interviews/{created['id']}/teacher-review", json={
        "version": version, "decision": "approve", "notes": "Checked replay and report",
        "criteria": [{"name": "correctness", "score": 9}],
    })
    assert reviewed.status_code == 200, reviewed.text
    adopted = client.post(f"/auto-task/ai/interviews/{created['id']}/adopt", json={
        "version": reviewed.json()["data"]["version"], "reason": "Final teacher decision",
    })
    assert adopted.status_code == 200, adopted.text
    with store.transaction() as session:
        detail = session.query(ProblemSetAnswerSheetDetail).get(1)
        assert json.loads(detail.judgeLog) == [{"jScore": 9.0, "name": "correctness", "score": 10.0}]
        assert detail.judgeLock_username == "teacher"
    assert client.post(f"/auto-task/ai/interviews/{created['id']}/teacher-review", json={
        "version": adopted.json()["data"]["version"], "decision": "reject", "notes": "late change",
        "criteria": [],
    }).status_code == 409
