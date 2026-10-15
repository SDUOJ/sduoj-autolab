import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import AutoTaskRun, AutoTaskRunLog, Base, ProblemGroup
from auto_task import store
from auto_task.authoring import generate, verify_candidate
from auto_task.entities import AiResource, AiSetting, TABLES, TaskJob
from auto_task.worker import run_once
from server import auto_task_ai as api
from server import auto_task_authoring as authoring_api


@compiles(LONGTEXT, "sqlite")
def sqlite_text(*_, **kwargs):
    return "TEXT"


@compiles(BIGINT, "sqlite")
@compiles(TINYINT, "sqlite")
def sqlite_integer(*_, **kwargs):
    return "INTEGER"


ADMIN = {"userId": 7, "username": "teacher", "roles": ["admin"], "groups": []}


@pytest.fixture
def database(monkeypatch, tmp_path):
    test_url = os.environ.get("AUTOLAB_AI_TEST_DATABASE_URL")
    if test_url:
        from sqlalchemy.engine.url import make_url
        url = make_url(test_url)
        assert url.host in ("127.0.0.1", "localhost") and url.database == "autolab_ai_test"
        engine = create_engine(test_url)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [AutoTaskRun.__table__, AutoTaskRunLog.__table__, ProblemGroup.__table__, *TABLES]
    Base.metadata.create_all(engine, tables=tables)
    monkeypatch.setattr(store, "_factory", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setenv("AUTOLAB_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOLAB_AI_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTOLAB_AI_WAKE", "0")
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "0")
    with store.transaction() as session:
        session.add(AiSetting(id="runtime", data='{"max_concurrency":2,"per_user_concurrency":1}'))
    yield engine
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture
def client(database, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(authoring_api.router, prefix="/auto-task/ai")
    app.dependency_overrides[api.cover_header] = lambda: ADMIN
    monkeypatch.setattr(authoring_api, "group_manager", lambda *args: None)
    return TestClient(app)


def draft():
    return {"title": "Double", "description": "Read n and print 2n.",
            "reference_code": "n=int(input());print(2*n)", "language": "python",
            "tests": [{"input": "1\n", "output": "2\n", "public": True, "purpose": "sample"},
                      {"input": "2\n", "output": "4\n", "public": False, "purpose": "small"}],
            "generator_code": "seed=int(input());print(seed)",
            "validator_code": "int(input());print('OK')", "generator_seeds": [3],
            "tags": ["arithmetic"], "explanation": "Multiply by two."}


def candidate(monkeypatch, previous=None):
    calls = {}
    def execute(ctx, language, code, stdin):
        if "seed=int" in code:
            calls[stdin] = calls.get(stdin, 0) + 1
            return {"exit_code": 0, "output": str(int(stdin.strip())) + "\n", "timed_out": False}
        if "print('OK')" in code:
            return {"exit_code": 0, "output": "OK\n", "timed_out": False}
        return {"exit_code": 0, "output": str(2 * int(stdin.strip())) + "\n", "timed_out": False}
    monkeypatch.setattr("auto_task.sandbox.execute", execute)
    ctx = SimpleNamespace()
    data = {"requirements": "double integers"}
    if previous:
        data["candidate"] = previous
    result = generate(ctx, data, {"instruction": "cover boundaries"}, lambda *args: draft())
    assert calls == {"3\n": 2}
    return result


def add_resource(value, state="approved"):
    with store.transaction() as session:
        row = AiResource(id="author-1", kind="authoring", owner_id="7", owner_username="teacher",
                         data=store.dumps({"requirements": "double integers", "candidate": value}), state=state)
        session.add(row)
        session.add(ProblemGroup(gid=8, name="programming", type=2, problemInfo="[]", username="teacher"))


def test_authoring_protocol_validates_generator_validator_reference_and_revision(monkeypatch):
    first = candidate(monkeypatch)
    assert verify_candidate(first) is first
    assert first["revision"] == 1 and first["parent_hash"] == ""
    assert len(first["test_plan"]) == 3
    assert sum(item["score"] for item in first["test_plan"]) == 100
    assert first["test_plan"][2]["output"] == "6\n"
    second = candidate(monkeypatch, first)
    assert second["revision"] == 2 and second["parent_hash"] == first["package_hash"]


def test_authoring_protocol_rejects_nondeterministic_generator(monkeypatch):
    counter = 0
    def execute(ctx, language, code, stdin):
        nonlocal counter
        if "seed=int" in code:
            counter += 1
            return {"exit_code": 0, "output": str(counter), "timed_out": False}
        return {"exit_code": 0, "output": "OK", "timed_out": False}
    monkeypatch.setattr("auto_task.sandbox.execute", execute)
    with pytest.raises(ValueError, match="not deterministic"):
        generate(SimpleNamespace(), {"requirements": "x"}, {"instruction": "x"}, lambda *args: draft())


@pytest.mark.skipif(os.environ.get("AUTOLAB_AI_REAL_SANDBOX_TEST") != "1",
                    reason="real isolated authoring test is opt-in")
def test_authoring_protocol_runs_real_generator_validator_and_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOLAB_SANDBOX_ENABLED", "1")
    ctx = SimpleNamespace(workspace=tmp_path, check=lambda: None)
    result = generate(ctx, {"requirements": "double integers"},
                      {"instruction": "real container validation"}, lambda *args: draft())
    assert verify_candidate(result)["validated"]
    assert result["test_plan"][2]["output"].strip() == "6"


def test_generate_endpoint_is_versioned_and_idempotent(client):
    with store.transaction() as session:
        session.add(AiResource(id="author-1", kind="authoring", owner_id="7", owner_username="teacher",
                               data='{"requirements":"draft"}', state="draft"))
    body = {"version": 1, "operation_key": "revision-1", "instruction": "add boundary tests"}
    first = client.post("/auto-task/ai/authoring/author-1/generate", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/auto-task/ai/authoring/author-1/generate", json=body).json()["data"]["replay"]
    assert client.post("/auto-task/ai/authoring/author-1/generate",
                       json={**body, "instruction": "different"}).status_code == 409
    with store.transaction() as session:
        job = session.query(TaskJob).one()
        assert json.loads(job.payload)["authoring_protocol"] == 2


def test_v2_authoring_job_runs_through_durable_worker_and_applies_candidate(client, monkeypatch):
    from auto_task.runtime import Context
    with store.transaction() as session:
        session.add(AiResource(id="author-1", kind="authoring", owner_id="7", owner_username="teacher",
                               data='{"requirements":"draft"}', state="draft"))
    created = client.post("/auto-task/ai/authoring/author-1/generate", json={
        "version": 1, "operation_key": "worker", "instruction": "add generated boundaries",
    })
    assert created.status_code == 200, created.text
    monkeypatch.setattr(Context, "ask", lambda self, prompt, schema, images=():
                        {"decision": "normal", "reason": ""} if schema["title"] == "Moderation" else draft())
    monkeypatch.setattr("auto_task.access.authorize_execution", lambda *args: None)
    def execute(ctx, language, code, stdin):
        if "seed=int" in code:
            return {"exit_code": 0, "output": str(int(stdin.strip())) + "\n", "timed_out": False}
        if "print('OK')" in code:
            return {"exit_code": 0, "output": "OK\n", "timed_out": False}
        return {"exit_code": 0, "output": str(2 * int(stdin.strip())) + "\n", "timed_out": False}
    monkeypatch.setattr("auto_task.sandbox.execute", execute)
    assert run_once()
    with store.transaction() as session:
        job = session.query(TaskJob).get(created.json()["data"]["taskId"])
        resource = session.query(AiResource).get("author-1")
        assert job.status == "success", job.error
        assert resource.version == 2 and resource.state == "review"
        assert verify_candidate(json.loads(resource.data)["candidate"])["revision"] == 1
    replay = client.post("/auto-task/ai/authoring/author-1/generate", json={
        "version": 1, "operation_key": "worker", "instruction": "add generated boundaries",
    })
    assert replay.status_code == 200 and replay.json()["data"]["replay"]


def test_native_plan_requires_approved_validated_revision(client, monkeypatch):
    value = candidate(monkeypatch)
    add_resource(value, "review")
    path = "/auto-task/ai/authoring/author-1/native-plan"
    body = {"version": 1, "judge_template_id": 6, "title": "Double integers"}
    assert client.post(path, json=body).status_code == 409
    with store.transaction() as session:
        session.query(AiResource).get("author-1").state = "approved"
    planned = client.post(path, json=body)
    assert planned.status_code == 200, planned.text
    data = planned.json()["data"]
    assert data["problem"]["judgeTemplates"] == [6]
    assert data["package_hash"] == value["package_hash"]
    assert sum(item["score"] for item in data["checkpoints"]) == 100


def test_native_finalize_rechecks_all_checkpoint_bytes_then_binds_group(client, monkeypatch):
    value = candidate(monkeypatch)
    add_resource(value)
    plan = value["test_plan"]
    checkpoint_ids = [101, 102, 103]
    input_files = {1001 + index * 2: item["input"].encode() for index, item in enumerate(plan)}
    output_files = {1002 + index * 2: item["output"].encode() for index, item in enumerate(plan)}
    files = {**input_files, **output_files}

    async def program_info(code):
        return {"problemId": 99, "problemCode": code, "problemTitle": "Double integers",
                "source": "autolab-ai:author-1:v1", "judgeTemplates": [{"id": 6}],
                "problemDescriptionDTO": {"id": 88, "userId": 7, "markdownDescription": value["description"]},
                "problemCaseDTOList": [{"input": item["input"][:64], "output": item["output"][:64]}
                                       for item in plan if item["public"]]}

    async def make_get(service, path, params):
        if path.endswith("queryProblemCheckPoints"):
            return [{"checkpointId": checkpoint_ids[index], "checkpointScore": item["score"]}
                    for index, item in enumerate(plan)]
        return [{"checkpointId": checkpoint_ids[index], "inputFileId": 1001 + index * 2,
                 "outputFileId": 1002 + index * 2} for index in range(len(plan))]

    async def download(file_id):
        return 200, files[file_id], {}

    monkeypatch.setattr(authoring_api, "program_info", program_info)
    monkeypatch.setattr(authoring_api, "make_get", make_get)
    monkeypatch.setattr(authoring_api, "downloadFile", download)
    body = {"version": 1, "group_id": 8, "problem_code": "SDUOJ-99", "judge_template_id": 6,
            "score": 100, "checkpoints": [{"index": index + 1, "checkpoint_id": checkpoint_id}
                                            for index, checkpoint_id in enumerate(checkpoint_ids)]}
    path = "/auto-task/ai/authoring/author-1/native-finalize"
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["ready_for_judging"] and result["version"] == 2
    assert client.post(path, json=body).json()["data"]["replay"]
    with store.transaction() as session:
        assert json.loads(session.query(ProblemGroup).get(8).problemInfo)[0]["pid"] == 99
        assert session.query(AiResource).get("author-1").state == "published"


def test_native_finalize_rejects_tampered_checkpoint_without_binding(client, monkeypatch):
    value = candidate(monkeypatch)
    add_resource(value)
    plan = value["test_plan"]
    async def program_info(code):
        return {"problemId": 99, "problemCode": code, "problemTitle": "Double",
                "source": "autolab-ai:author-1:v1", "judgeTemplates": [{"id": 6}],
                "problemDescriptionDTO": {"id": 88, "userId": 7, "markdownDescription": value["description"]},
                "problemCaseDTOList": [{"input": item["input"][:64], "output": item["output"][:64]}
                                       for item in plan if item["public"]]}
    async def make_get(service, path, params):
        if path.endswith("queryProblemCheckPoints"):
            return [{"checkpointId": 101 + index, "checkpointScore": item["score"]}
                    for index, item in enumerate(plan)]
        return [{"checkpointId": 101 + index, "inputFileId": 1001 + index * 2,
                 "outputFileId": 1002 + index * 2} for index in range(len(plan))]
    async def download(file_id):
        return 200, b"tampered", {}
    monkeypatch.setattr(authoring_api, "program_info", program_info)
    monkeypatch.setattr(authoring_api, "make_get", make_get)
    monkeypatch.setattr(authoring_api, "downloadFile", download)
    body = {"version": 1, "group_id": 8, "problem_code": "SDUOJ-99", "judge_template_id": 6,
            "checkpoints": [{"index": index + 1, "checkpoint_id": 101 + index}
                            for index in range(len(plan))]}
    assert client.post("/auto-task/ai/authoring/author-1/native-finalize", json=body).status_code == 409
    with store.transaction() as session:
        assert json.loads(session.query(ProblemGroup).get(8).problemInfo) == []
        assert session.query(AiResource).get("author-1").state == "approved"
