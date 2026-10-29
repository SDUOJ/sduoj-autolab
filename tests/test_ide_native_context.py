"""Native authorization runs with baseline DB/identity modules, without AI storage."""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base, ProblemGroup, ProblemSet
from server import ide_context_dependencies as dependencies
from server import run_context_internal as api


@compiles(LONGTEXT, 'sqlite')
def sqlite_text(*args, **kwargs):
    return 'TEXT'


@compiles(BIGINT, 'sqlite')
@compiles(TINYINT, 'sqlite')
def sqlite_integer(*args, **kwargs):
    return 'INTEGER'


def test_identity_rpc_is_fresh_strict_and_does_not_follow_redirects(monkeypatch):
    import const
    monkeypatch.setattr(const, 'SDUOJ_TOKEN', 'owned-test-token')
    monkeypatch.setenv('AUTOLAB_IDE_USER_SERVICE_ADDRESS', 'user.test:8080')
    result = {'userId': '42', 'username': 'student', 'roles': ['user'], 'groups': ['10']}
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return result
    response = Response()
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return response
    monkeypatch.setattr(dependencies, '_get', request)
    assert dependencies.fetch_actor('42') == {**result, 'userId': 42, 'groups': [10]}
    url, options = calls[0]
    assert url == 'http://user.test:8080/internal/user/runSubject'
    assert options['timeout'] == (.5, 1)
    assert options['headers'] == {'sduoj-token': 'owned-test-token'}
    result['userId'] = '43'
    with pytest.raises(ValueError):
        dependencies.fetch_actor('42')
    result['userId'] = True
    with pytest.raises(ValueError):
        dependencies.fetch_actor('42')
    response.status_code = 302
    with pytest.raises(RuntimeError):
        dependencies.fetch_actor('42')


def test_native_policy_rpc_denies_active_exam_without_ai_storage(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    tables = [ProblemGroup.__table__, ProblemSet.__table__]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as session:
        session.add(ProblemGroup(gid=1, name='fixture', type=2,
                                problemInfo='[{"pid":123}]', username='teacher'))
        session.add(ProblemSet(psid=1, name='exam', type=1, groupId=10, username='teacher',
            tag='fixture', groupInfo='[{"gid":1}]', config='{"useSameSE":1}',
            tm_start=datetime.fromtimestamp(1999999999), tm_end=datetime.fromtimestamp(2000000001)))
        session.commit()
    monkeypatch.setattr(dependencies, '_factory', factory)
    monkeypatch.setattr(api, 'SDUOJ_TOKEN', 'owned-test-token')
    monkeypatch.setattr(api, 'getNowTime', lambda: 2_000_000_000_000)
    monkeypatch.setattr(api, 'fetch_actor', lambda *_, **__: {
        'userId': 42, 'username': 'student', 'roles': ['user'], 'groups': [10]})
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    try:
        payload = {'userId': 42, 'problemId': 123}
        assert client.post('/internal/run/context', json=payload).status_code == 403
        headers = {'sduoj-token': 'owned-test-token'}
        response = client.post('/internal/run/context', json=payload, headers=headers)
        assert response.status_code == 200 and response.json() == {'allowed': False, 'reason': 'ACTIVE_EXAM'}
        assert client.post('/internal/run/context', json={**payload, 'problemId': 124},
                           headers=headers).json()['allowed']
        assert client.post('/internal/run/context', json={**payload, 'roles': ['admin']},
                           headers=headers).status_code == 422
        from sqlalchemy import inspect
        assert set(inspect(engine).get_table_names()) == {'problem_group', 'problem_set'}
    finally:
        engine.dispose()


def test_read_only_transaction_always_rolls_back_and_closes(monkeypatch):
    calls = []
    class Session:
        def rollback(self):
            calls.append('rollback')
        def close(self):
            calls.append('close')
    monkeypatch.setattr(dependencies, '_factory', Session)
    with dependencies.transaction():
        pass
    assert calls == ['rollback', 'close']
    calls.clear()
    with pytest.raises(ValueError), dependencies.transaction():
        raise ValueError('fixture')
    assert calls == ['rollback', 'close']


def test_discovery_and_operator_address_reject_unsafe_shapes(monkeypatch):
    for address in ('user:pass@host:8080', 'host:8080/path', 'host', 'host:8080?x=1'):
        monkeypatch.setenv('AUTOLAB_IDE_USER_SERVICE_ADDRESS', address)
        with pytest.raises(ValueError):
            dependencies.user_service_address()
    monkeypatch.delenv('AUTOLAB_IDE_USER_SERVICE_ADDRESS', raising=False)
    class Response:
        status_code = 200
        def json(self):
            return {'hosts': [{'ip': '127.0.0.1', 'port': 8080, 'healthy': False},
                              {'ip': '::1', 'port': 18081, 'healthy': True}]}
    monkeypatch.setattr(dependencies, '_get', lambda *_, **__: Response())
    assert dependencies.user_service_address() == '[::1]:18081'


def test_internal_http_disables_ambient_proxy_redirect_and_closes(monkeypatch):
    calls = []
    class Session:
        trust_env = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append('closed')
        def get(self, url, **kwargs):
            assert self.trust_env is False
            assert kwargs['allow_redirects'] is False
            calls.append(url)
            return 'response'
    monkeypatch.setattr(dependencies.requests, 'Session', Session)
    assert dependencies._get('http://internal.test', timeout=(.5, 1)) == 'response'
    assert calls == ['http://internal.test', 'closed']


def test_concurrent_first_requests_share_one_bounded_database_pool(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(8)
    engines = []
    class Session:
        def rollback(self):
            pass
        def close(self):
            pass
    def create(*args, **kwargs):
        assert kwargs['pool_size'] == 4 and kwargs['max_overflow'] == 4 and kwargs['pool_timeout'] == 1
        engines.append(object())
        return engines[-1]
    monkeypatch.setattr(dependencies, '_factory', None)
    monkeypatch.setattr(dependencies, 'create_engine', create)
    monkeypatch.setattr(dependencies, 'sessionmaker', lambda **kwargs: Session)
    def request(_):
        barrier.wait(timeout=2)
        with dependencies.transaction():
            pass
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(request, range(8)))
    assert len(engines) == 1
