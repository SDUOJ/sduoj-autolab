import json
import os
from datetime import datetime
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import (AutoTaskRun, AutoTaskRunLog, Base, ProblemGroup, ProblemObjective, ProblemSet,
                ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail, ProblemSubjective)
from auto_task import store
from auto_task.entities import AiPlagiarismSnapshot, AiPlagiarismSubmission, AiResource, AiRevision, AiSetting, TABLES, TaskJob
from server import auto_task_ai as api
from server import auto_task_native as native


@compiles(LONGTEXT, "sqlite")
def _sqlite_text(*_, **__):
    return "TEXT"


@compiles(BIGINT, "sqlite")
@compiles(TINYINT, "sqlite")
def _sqlite_integer(*_, **__):
    return "INTEGER"


@pytest.fixture
def database(monkeypatch, tmp_path):
    test_url = os.environ.get("AUTOLAB_AI_TEST_DATABASE_URL")
    if test_url:
        from sqlalchemy.engine.url import make_url

        url = make_url(test_url)
        assert url.host in ("127.0.0.1", "localhost") and url.database == "autolab_ai_test"
        engine = create_engine(test_url)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    tables = [AutoTaskRun.__table__, AutoTaskRunLog.__table__, ProblemGroup.__table__, ProblemObjective.__table__,
              ProblemSet.__table__, ProblemSetAnswerSheet.__table__, ProblemSetAnswerSheetDetail.__table__,
              ProblemSubjective.__table__, *TABLES]
    Base.metadata.create_all(engine, tables=tables)
    monkeypatch.setattr(store, "_factory", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setenv("AUTOLAB_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOLAB_AI_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTOLAB_AI_WAKE", "0")
    with store.transaction() as session:
        session.add(AiSetting(id="runtime", data='{"max_concurrency":2,"per_user_concurrency":1}'))
    yield engine
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture
def client(database):
    app = FastAPI()
    app.include_router(api.router)
    from server.auto_task_actions import router as actions
    app.include_router(actions, prefix="/auto-task/ai")
    app.include_router(native.router, prefix="/auto-task/ai")
    app.dependency_overrides[api.cover_header] = lambda: {
        "userId": 42, "username": "student", "roles": ["user"], "groups": []
    }
    return TestClient(app)


def _native_problem(client):
    client.app.dependency_overrides[api.cover_header] = lambda: {
        "userId": 7, "username": "teacher", "roles": ["admin"], "groups": []
    }
    with store.transaction() as session:
        session.add(ProblemSet(psid=9, name="native exam", type=1, groupInfo='[{"gid":9}]',
                               config="{}", username="teacher", groupId=9, tag="dev"))
        session.add(ProblemGroup(gid=9, name="programming", type=2,
                                 problemInfo='[{"pid":99,"desId":"d1"}]', username="teacher"))
        session.add_all([
            ProblemSetAnswerSheet(asid=91, psid=9, username="alice"),
            ProblemSetAnswerSheet(asid=92, psid=9, username="bob"),
            ProblemSetAnswerSheetDetail(asd_id=901, asid=91, gid=9, pid=99,
                                        answer='["a0","a1"]', tm_answer_submit=datetime.utcnow()),
            ProblemSetAnswerSheetDetail(asd_id=902, asid=92, gid=9, pid=99,
                                        answer='["b1"]', tm_answer_submit=datetime.utcnow()),
        ])


def test_native_plagiarism_freezes_one_submission_per_student_and_replays(client, monkeypatch):
    _native_problem(client)
    monkeypatch.setattr(native, "group_manager", lambda *args: None)

    async def lookup(service, path, params, data):
        assert service == "problem-service"
        assert path == "/internal/submission/listResult"
        assert data == {"bizType": 2, "bizId": 9, "problemId": 99, "exportCode": True}
        return [{"submissionId": submission_id,
                 "userId": {"a0": 101, "a1": 101, "b1": 102}[submission_id],
                 "problemId": 99, "code": "print('same')\n",
                 "gmtCreate": "2026-09-05T09:00:00Z" if submission_id == "a0" else "2026-09-05T10:00:00Z",
                 "judgeResult": 1, "judgeScore": 50 if submission_id == "a0" else 100}
                for submission_id in ("a0", "a1", "b1")]

    monkeypatch.setattr(native, "make_post", lookup)
    body = {"psid": 9, "group_id": 9, "pid": 99, "operation_key": "native-1",
            "language": "python", "threshold": 0.6}
    first = client.post("/auto-task/ai/native/plagiarism/create", json=body)
    assert first.status_code == 200, first.text
    result = first.json()["data"]
    with store.transaction() as session:
        snapshot = session.query(AiPlagiarismSnapshot).one()
        assert snapshot.count == 2
        assert session.query(AiPlagiarismSubmission).count() == 2
        assert {item.native_submission_id for item in session.query(AiPlagiarismSubmission)} == {"a1", "b1"}
        job = session.query(TaskJob).get(result["taskId"])
        assert json.loads(job.payload)["native_request"]["selection_policy"] == "latest"
    replay = client.post("/auto-task/ai/native/plagiarism/create", json=body)
    assert replay.status_code == 200 and replay.json()["data"]["replay"]
    assert client.post("/auto-task/ai/native/plagiarism/create",
                       json={**body, "threshold": 0.8}).status_code == 409


def test_jplag_report_reader_keeps_all_candidate_rows(tmp_path):
    from auto_task.plagiarism import read_comparisons

    rows = []
    mapping = {}
    for index in range(137):
        left, right = "s-0", "s-{}".format(index + 1)
        filename = "pair-{}.json".format(index)
        rows.append({"firstSubmission": left, "secondSubmission": right,
                     "similarities": {"MAX": 0.8}})
        mapping.setdefault(left, {})[right] = filename
    path = tmp_path / "all.jplag"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("topComparisons.json", json.dumps(rows))
        archive.writestr("submissionMappings.json", json.dumps({"submissionIdsToComparisonFileName": mapping}))
        for index in range(137):
            archive.writestr("comparisons/pair-{}.json".format(index), json.dumps({"matches": []}))
    assert len(read_comparisons(path)) == 137


def test_native_plagiarism_writeback_requires_reviews_and_is_idempotent(client):
    _native_problem(client)
    import hashlib
    hash_a, hash_b = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    manifest_rows = [
        {"submission_id": "a1", "user_id": "101", "username": "alice", "code_sha256": hash_a, "answer_detail_id": 901},
        {"submission_id": "b1", "user_id": "102", "username": "bob", "code_sha256": hash_b, "answer_detail_id": 902},
    ]
    manifest_hash = store.digest(manifest_rows)
    with store.transaction() as session:
        candidate = {"pairs": [{"left": "101", "right": "102",
                                 "left_submission_id": "a1", "right_submission_id": "b1",
                                 "similarity": 0.91}]}
        resource = AiResource(id="plag-native", kind="plagiarism", owner_id="7", owner_username="teacher", psid=9,
                              data=store.dumps({"native_snapshot": {"id": "snap-native", "sha256": manifest_hash, "count": 2,
                                                 "psid": 9, "group_id": 9, "pid": 99},
                                                "candidate": candidate}))
        session.add(resource)
        session.flush()
        session.add(AutoTaskRun(id="scan-native", task_type="plagiarism.scan", psid=9, username="teacher", status="success"))
        session.flush()
        session.add(TaskJob(id="scan-native", owner_id="7", owner_username="teacher", payer_id="7",
                            operation_key="native-plagiarism:test", task_type="plagiarism.scan", payload="{}",
                            input_hash="x", resource_id="plag-native", psid=9, status="success",
                            result=store.dumps(candidate)))
        session.add(AiPlagiarismSnapshot(id="snap-native", resource_id=resource.id, resource_version=1, psid=9,
                                         group_id=9, pid=99, selection_policy="latest", sha256=manifest_hash, count=2))
        session.add_all([
            AiPlagiarismSubmission(id="ps-a", snapshot_id="snap-native", answer_detail_id=901,
                                   native_submission_id="a1", user_id="101", username="alice", code="a", code_sha256=hash_a),
            AiPlagiarismSubmission(id="ps-b", snapshot_id="snap-native", answer_detail_id=902,
                                   native_submission_id="b1", user_id="102", username="bob", code="b", code_sha256=hash_b),
        ])
    path = "/auto-task/ai/plagiarism/plag-native"
    assert client.post(path + "/writeback", json={"version": 1, "reason": "teacher checked"}).status_code == 409
    reviewed = client.post(path + "/pairs/review", json={"version": 1, "left": "101", "right": "102",
                                                            "decision": "SUSPICIOUS", "reason": "matching loop"})
    assert reviewed.status_code == 200, reviewed.text
    version = reviewed.json()["data"]["version"]
    with store.transaction() as session:
        session.query(ProblemSetAnswerSheetDetail).get(901).answer = '["new-a2"]'
    assert client.post(path + "/writeback", json={"version": version, "reason": "teacher checked"}).status_code == 409
    with store.transaction() as session:
        session.query(ProblemSetAnswerSheetDetail).get(901).answer = '["a0","a1"]'
    written = client.post(path + "/writeback", json={"version": version, "reason": "teacher checked"})
    assert written.status_code == 200, written.text
    assert written.json()["data"]["updated"] == 2
    with store.transaction() as session:
        values = [json.loads(row.antiCheatingResult) for row in session.query(ProblemSetAnswerSheetDetail).order_by(ProblemSetAnswerSheetDetail.asd_id)]
        assert all(value["summary"] == 0.91 for value in values)
        assert session.query(AiResource).get("plag-native").state == "adopted"
        assert [item.version for item in session.query(AiRevision).filter_by(resource_id="plag-native").order_by(AiRevision.created_at)] == [1, 2, 3]
    replay = client.post(path + "/writeback", json={"version": version, "reason": "teacher checked"})
    assert replay.status_code == 200 and replay.json()["data"]["replay"]
    assert client.post(path + "/pairs/review", json={"version": replay.json()["data"]["version"],
        "left": "101", "right": "102", "decision": "CLEAR", "reason": "late change"}).status_code == 409
