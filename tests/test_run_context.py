"""Native authorization windows and routes without AI task storage."""
import json
from datetime import datetime
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.mysql import BIGINT,LONGTEXT,TINYINT
from sqlalchemy.ext.compiler import compiles
from db import Base,ProblemSet,ProblemGroup,ProblemSetAnswerSheet
from auto_task.run_context import check_native_context
from server import ide_context_dependencies as dependencies
from server import run_context_internal as api
ACTOR={"userId":42,"username":"student","roles":["user"],"groups":[10]}
NOW=2_000_000_000_000
@compiles(LONGTEXT,'sqlite')
def sqlite_text(*args,**kwargs):return 'TEXT'
@compiles(BIGINT,'sqlite')
@compiles(TINYINT,'sqlite')
def sqlite_int(*args,**kwargs):return 'INTEGER'
@pytest.fixture
def context(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine,tables=[ProblemGroup.__table__,ProblemSet.__table__,ProblemSetAnswerSheet.__table__])
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        with factory() as session:
            yield session
            session.commit()
    monkeypatch.setattr(dependencies,'transaction',transaction)
    monkeypatch.setattr(api,'transaction',transaction)
    with transaction() as s:
        s.add(ProblemGroup(gid=1,name='native',type=2,problemInfo='[{"pid":123}]',username='teacher'))
        s.add(ProblemSet(psid=1,name='timed',type=1,groupId=10,username='teacher',tag='exam',groupInfo='[{"gid":1}]',config='{"useSameSE":1}',tm_start=datetime.fromtimestamp((NOW-1000)/1000),tm_end=datetime.fromtimestamp((NOW+1000)/1000)))
    yield ACTOR.copy()
    engine.dispose()
def check(actor,problem=123,now=NOW):
    with dependencies.transaction() as s:return check_native_context(s,actor,problem,now)


def test_actual_group_problem_and_window_are_required(context):
    assert check(context) == {"allowed": False, "reason": "ACTIVE_EXAM"}
    assert check(context, 124)["allowed"]
    assert check({**context, "groups": [11]})["allowed"]
    assert check(context, now=NOW-1001)["allowed"]
    assert check(context, now=NOW+1001)["allowed"]
    assert not check(context, now=NOW+1000)["allowed"]
    assert not check({**context, "roles": ["admin"]})["allowed"]
    assert check({**context, "username": "teacher"})["allowed"]
    with dependencies.transaction() as s:
        assert s.query(ProblemSetAnswerSheet).count() == 0

def test_submitted_sheet_remains_restricted_and_manager_membership_is_native(context):
    with dependencies.transaction() as s:
        s.add(ProblemSetAnswerSheet(psid=1, username="student", finish=1))
    assert not check(context)["allowed"]
    with dependencies.transaction() as s:
        s.query(ProblemSet).one().manageGroupId = 10
    assert check(context)["allowed"]

def test_per_group_windows_timed_practice_and_invalid_state(context):
    with dependencies.transaction() as s:
        ps = s.query(ProblemSet).one()
        ps.type = 0
        ps.config = '{"useSameSE":0}'
        ps.groupInfo = json.dumps([{"gid":1,"timeSetting":[{"tm_start":NOW-10,"tm_end":NOW+10}]}])
    assert check(context)["reason"] == "ACTIVE_TIMED_PROBLEM_SET"
    assert check(context, now=NOW+11)["allowed"]
    with dependencies.transaction() as s:
        s.query(ProblemSet).one().groupInfo = '[{"gid":1}]'
    with pytest.raises(HTTPException) as error:
        check(context)
    assert error.value.status_code == 503

def test_rpc_auth_identity_resolution_and_no_client_roles(context, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    monkeypatch.setattr(api, "SDUOJ_TOKEN", "local-test-policy-token")
    monkeypatch.setattr(api, "fetch_actor", lambda subject, **_: context)
    monkeypatch.setattr(api, "getNowTime", lambda: NOW)
    body = {"userId":"42", "problemId":"123"}
    assert client.post('/internal/run/context', json=body).status_code == 403
    headers = {"sduoj-token":"local-test-policy-token"}
    assert client.post('/internal/run/context', json=body, headers=headers).json()["allowed"] is False
    assert client.post('/internal/run/context', json={**body,"groups":[]}, headers=headers).status_code == 422
    from starlette.requests import Request
    duplicate = Request({'type':'http','headers':[
        (b'sduoj-token',b'local-test-policy-token'),(b'sduoj-token',b'local-test-policy-token')]})
    with pytest.raises(HTTPException) as error:
        api.run_context(api.RunSubject(**body), duplicate)
    assert error.value.status_code == 403
    monkeypatch.setattr(api, "fetch_actor", lambda subject, **_: {**context, "roles":["guest"]})
    assert client.post('/internal/run/context', json=body, headers=headers).json()["allowed"] is False
    monkeypatch.setattr(api, "fetch_actor", lambda subject, **_: {**context,"userId":43})
    assert client.post('/internal/run/context', json=body, headers=headers).status_code == 503

def test_native_ide_resolves_program_route_and_uses_native_submission_window(monkeypatch):
    import auth
    from model import answer_sheet
    import sduojApi
    import utils
    events = []

    async def authorize(psid, actor, gi, submit=None):
        events.append((psid, actor["userId"], gi, submit))

    async def unchanged(db, route):
        return None

    async def code(pid):
        assert pid == 123
        return "PRIVATE-123"

    class Sheet:
        def __init__(self):
            self.session = self
        def close(self):
            events.append("closed")
        async def get_gid_pid_by_psid_gi_pi_cache(self, psid, gi, pi):
            assert (psid, gi, pi) == (9, 1, 2)
            return 77, 123
        async def group_get_info_by_id_cache(self, gid):
            return {"type": 2, "problemInfo": [{}, {}, {"desId": 4}]}

    monkeypatch.setattr(auth, "problem_set_user", authorize)
    monkeypatch.setattr(answer_sheet, "answerSheetModel", Sheet)
    monkeypatch.setattr(utils, "deal_order_change", unchanged)
    monkeypatch.setattr(sduojApi, "getProblemCode", code)
    monkeypatch.setattr(api, "fetch_actor", lambda *_, **__: ACTOR)
    monkeypatch.setattr(api, "SDUOJ_TOKEN", "ide-local-token")
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    body = {"userId": 42, "context": {"type": 2, "bizId": 9, "groupIndex": 1, "problemIndex": 2}, "writable": True}
    assert client.post('/internal/ide/context', json=body).status_code == 403
    response = client.post('/internal/ide/context', json=body, headers={"sduoj-token": "ide-local-token"})
    assert response.status_code == 200, response.text
    assert response.json() == {"key": "problemset:9:77:123:2", "bizType": 2, "bizId": 9, "problemCode": "PRIVATE-123", "descriptionId": 4}
    assert (9, 42, 1, True) in events and "closed" in events

    async def denied(*_, **__):
        raise HTTPException(403, "Window closed")
    monkeypatch.setattr(auth, "problem_set_user", denied)
    assert client.post('/internal/ide/context', json=body, headers={"sduoj-token": "ide-local-token"}).status_code == 403
