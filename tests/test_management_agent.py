import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from tests.test_ai_backend import database, ADMIN, USER
from auth import cover_header
from auto_task import management_agent as agent
from auto_task.store import transaction, dumps, digest, claim
from auto_task.entities import AiResource, TaskJob
from auto_task.resources import get_resource
from server.management_agent import router


def client(user=ADMIN):
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[cover_header] = lambda: user
    return TestClient(app)


def create(c):
    r = c.post('/management-agent/sessions', json={'title': '测试会话', 'preset': 'programming', 'instructions': '测试说明'})
    assert r.status_code == 200, r.text
    return r.json()['data']['id']


def pending(sid, monkeypatch):
    value = {'method': 'GET', 'path': '/manage/contest/query', 'arguments': {'contestId': 432}, 'reason': '读取当前比赛，检查题号'}
    from auto_task.configuration import ProviderConfig
    monkeypatch.setattr(agent, 'provider', lambda s: (ProviderConfig(provider='codex', base_url='http://127.0.0.1:1/v1', model='test'), 'fixture'))
    agent.start_turn(sid, ADMIN, 'fixture turn')
    runtime = claim()
    assert runtime and runtime['task_type'] == 'management.turn'
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one()
        data = json.loads(row.data); data['actions'] = [{'id': 'call1', 'task_id': runtime['id'], 'hash': digest(value), 'status': 'pending', 'action': value}]
        row.data = dumps(data); row.state = 'approval'
    return value, runtime


def test_sessions_are_persisted_owned_and_admin_only(database):
    c = client(); sid = create(c)
    assert c.get('/management-agent/sessions').json()['data'][0]['id'] == sid
    assert '测试说明' in c.get('/management-agent/sessions/'+sid).json()['data']['instructions']
    other = client({**ADMIN, 'userId': ADMIN['userId']+100, 'username': 'other'})
    assert other.get('/management-agent/sessions').json()['data'] == []
    assert other.get('/management-agent/sessions/'+sid).status_code == 404
    assert other.post('/management-agent/sessions/'+sid+'/stop').status_code == 404
    assert client(USER).get('/management-agent/presets').status_code == 403
    with transaction() as s:
        with pytest.raises(HTTPException): get_resource(s, ADMIN, sid, True)


def test_confirmation_is_exactly_once_and_uses_current_browser_cookie(database, monkeypatch):
    c = client(); sid = create(c); value, runtime = pending(sid, monkeypatch)
    execute = Mock(return_value={'success': True, 'response': {'code': 0}})
    monkeypatch.setattr(agent, 'execute_action', execute)
    c.get('/management-agent/sessions/'+sid); execute.assert_not_called()
    endpoint = f'/management-agent/sessions/{sid}/actions/call1'
    assert c.post(endpoint, json={'action_hash': '0'*64, 'approve': True}).status_code == 409
    execute.assert_not_called()
    r = c.post(endpoint, json={'action_hash': digest(value), 'approve': True}, headers={'cookie': 'SESSION=temporary'})
    assert r.status_code == 200
    execute.assert_called_once_with(value, 'SESSION=temporary')
    assert c.post(endpoint, json={'action_hash': digest(value), 'approve': True}).status_code == 409
    execute.assert_called_once()
    assert 'temporary' not in c.get('/management-agent/sessions/'+sid).text
    # Confirmation persists a result; the remote task worker delivers it.
    with transaction() as s:
        data = json.loads(s.query(AiResource).filter_by(id=sid).one().data)
        assert data['actions'][0]['result']['success']


def test_rejection_never_executes_and_cannot_be_approved_later(database, monkeypatch):
    c = client(); sid = create(c); value, runtime = pending(sid, monkeypatch)
    execute = Mock(); monkeypatch.setattr(agent, 'execute_action', execute)
    url = f'/management-agent/sessions/{sid}/actions/call1'
    assert c.post(url, json={'action_hash': digest(value), 'approve': False}).status_code == 200
    assert c.post(url, json={'action_hash': digest(value), 'approve': True}).status_code == 409
    execute.assert_not_called()


def test_other_user_and_disconnected_runtime_cannot_confirm(database, monkeypatch):
    c = client(); sid = create(c); value, runtime = pending(sid, monkeypatch)
    url = f'/management-agent/sessions/{sid}/actions/call1'; body = {'action_hash': digest(value), 'approve': True}
    assert client({**ADMIN, 'userId': 9876}).post(url, json=body).status_code == 404
    from datetime import datetime, timedelta
    with transaction() as s:
        s.query(TaskJob).filter_by(id=runtime['id']).update({'lease_until': datetime.utcnow()-timedelta(seconds=1)})
    assert c.get('/management-agent/sessions/'+sid).json()['data']['actions'][0]['status'] == 'expired'
    assert c.post(url, json=body).status_code == 409


@pytest.mark.parametrize('path', ['/internal/problem/create', 'https://evil.test', '/manage/user/update', '/manage/contest/query?x=1', '/manage/../user/update'])
def test_agent_cannot_call_unlisted_or_external_interfaces(path):
    with pytest.raises(HTTPException):
        agent.validate_action({'method': 'POST', 'path': path, 'arguments': {}, 'reason': 'test'})


def test_duplicate_contest_problems_and_modified_pending_payload_are_rejected(database, monkeypatch):
    with pytest.raises(HTTPException):
        agent.validate_action({'method': 'POST', 'path': '/manage/contest/update', 'arguments': {'problems': [{'problemCode': 'SDUOJ-1806'}, {'problemCode': ' sduoj-1806 '}]}, 'reason': 'test'})
    c = client(); sid = create(c); value, _ = pending(sid, monkeypatch)
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one(); data = json.loads(row.data)
        data['actions'][0]['action']['arguments']['contestId'] = 999; row.data = dumps(data)
    assert c.post(f'/management-agent/sessions/{sid}/actions/call1', json={'action_hash': digest(value), 'approve': True}).status_code == 409


def test_dynamic_tool_is_only_recorded_until_approval(database, monkeypatch):
    c = client(); sid = create(c)
    _, job = pending(sid, monkeypatch)
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one(); data = json.loads(row.data); data['actions'] = []; row.data = dumps(data)
    runtime = object.__new__(agent.Agent); runtime.session_id = sid; runtime.job = job; runtime.calls = {}; runtime.send = Mock(); runtime.tool_reply = Mock()
    action = {'method': 'GET', 'path': '/manage/problem/list', 'arguments': {'pageNow': 1}, 'reason': '查询已有题目'}
    execute = Mock(); monkeypatch.setattr(agent, 'execute_action', execute)
    runtime.receive({'id': 5, 'method': 'item/tool/call', 'params': {'tool': 'oj_request', 'arguments': action}})
    execute.assert_not_called(); runtime.tool_reply.assert_not_called()
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one()
        assert json.loads(row.data)['actions'][0]['action'] == action
        assert row.state == 'approval'
    runtime.receive({'id': 6, 'method': 'item/commandExecution/requestApproval', 'params': {}})
    assert runtime.send.call_args.args[0]['error']['code'] == -32601


@pytest.mark.skipif(__import__('os').environ.get('AUTOLAB_MANAGEMENT_PROTOCOL_TEST') != '1', reason='Opt-in real Codex CLI, local fake model only')
@pytest.mark.parametrize('scenario', ['complete', 'missing-final', 'resume'])
def test_real_app_server_tool_confirmation_loop(database, monkeypatch, scenario):
    """Real CLI protocol + local deterministic Responses server; no paid model calls."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from auto_task.configuration import ProviderConfig
    model_calls = []
    action = {'method': 'GET', 'path': '/manage/problem/list', 'arguments': {'pageNow': 1, 'pageSize': 20}, 'reason': '查询现有题目'}
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('content-length', '0'))))
            model_calls.append(payload)
            index = len(model_calls)
            tool = next((t.get('name') for t in payload.get('tools', []) if t.get('name', '').endswith('oj_request')), 'oj_request')
            item = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_test', 'name': tool, 'arguments': dumps(action), 'status': 'completed'} if index == 1 else {'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': '已收到确认并读取题目列表。', 'annotations': []}]}
            response = {'id': 'resp_'+str(index), 'object': 'response', 'created_at': 1789300000, 'model': 'test-model', 'status': 'in_progress', 'output': []}
            complete = {**response, 'status': 'completed', 'output': [item]}
            if scenario != 'missing-final' or index != 2:
                complete['usage'] = {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}
            events = [{'type': 'response.created', 'response': response}, {'type': 'response.output_item.added', 'output_index': 0, 'item': item}, {'type': 'response.output_item.done', 'output_index': 0, 'item': item}, {'type': 'response.completed', 'response': complete}]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for sequence, event in enumerate(events):
                self.wfile.write(('data: '+dumps({**event, 'sequence_number': sequence})+'\n\n').encode()); self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = ProviderConfig(provider='codex', base_url=f'http://127.0.0.1:{server.server_port}/v1', model='test-model', timeout_seconds=60)
    monkeypatch.setattr(agent, 'provider', lambda s: (config, 'local-test-only'))
    received = []
    OriginalAgent = agent.Agent
    class RecordingAgent(OriginalAgent):
        def receive(self, message):
            received.append(message)
            return super().receive(message)
    monkeypatch.setattr(agent, 'Agent', RecordingAgent)
    execute = Mock(return_value={'success': True, 'response': {'code': 0, 'data': {'rows': []}}})
    monkeypatch.setattr(agent, 'execute_action', execute)
    c = client(); sid = create(c)
    try:
        r = c.post(f'/management-agent/sessions/{sid}/messages', json={'text': '查询题目列表，等待我在界面确认。'})
        assert r.status_code == 200, r.text
        from auto_task.worker import run_once
        worker = threading.Thread(target=run_once)
        worker.start()
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            data = c.get('/management-agent/sessions/'+sid).json()['data']
            if data['actions']: break
            time.sleep(0.05)
        assert data['actions'], (data, len(model_calls))
        execute.assert_not_called()
        pending_action = data['actions'][0]
        r = c.post(f'/management-agent/sessions/{sid}/actions/{pending_action["id"]}', json={'action_hash': pending_action['hash'], 'approve': True}, headers={'cookie': 'SESSION=protocol-test'})
        assert r.status_code == 200, r.text
        execute.assert_called_once_with(action, 'SESSION=protocol-test')
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            data = c.get('/management-agent/sessions/'+sid).json()['data']
            if data['state'] == 'ready': break
            time.sleep(0.05)
        assert any('已收到确认' in e['text'] for e in data['events']), data
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert len(model_calls) == 2
        from auto_task.entities import AiRun
        with transaction() as s:
            run = s.query(AiRun).one()
            usage = json.loads(run.usage)
            if scenario == 'missing-final':
                assert run.status == 'unsettled', received
            else:
                assert run.status == 'success', received
                assert usage['usage_complete'] is True, received
                assert usage['input_tokens'] == 20 and usage['output_tokens'] == 20, received
        if scenario == 'resume':
            assert client().post(f'/management-agent/sessions/{sid}/messages', json={'text': '继续总结'}).status_code == 200
            assert run_once()
            with transaction() as s:
                runs = s.query(AiRun).order_by(AiRun.created_at).all()
                assert len(runs) == 2 and runs[1].status == 'success', received
                resumed = json.loads(runs[1].usage)
                assert resumed['input_tokens'] == 10 and resumed['output_tokens'] == 10, received
            assert len(model_calls) == 3
        assert {t.get('name') for t in model_calls[0].get('tools', [])} <= {'oj_request', 'request_user_input', 'update_plan', 'skills'}
    finally:
        agent.stop_turn(sid, ADMIN)
        if 'worker' in locals(): worker.join(timeout=10)
        server.shutdown(); server.server_close()


def configure(monkeypatch):
    from auto_task.configuration import ProviderConfig
    config = ProviderConfig(provider='codex', base_url='http://127.0.0.1:1/v1', model='fixture')
    monkeypatch.setattr(agent, 'provider', lambda s: (config, 'local-fixture'))
    return config


def test_messages_are_durable_across_web_instances_without_starting_processes(database, monkeypatch):
    configure(monkeypatch)
    process = Mock(side_effect=AssertionError('Web must not create Agent processes'))
    monkeypatch.setattr(agent, 'Agent', process)
    first, second = client(), client()
    sid = create(first)
    assert first.post(f'/management-agent/sessions/{sid}/messages', json={'text': 'first turn'}).status_code == 200
    assert second.get(f'/management-agent/sessions/{sid}').json()['data']['state'] == 'running'
    assert second.post(f'/management-agent/sessions/{sid}/messages', json={'text': 'duplicate'}).status_code == 409
    with transaction() as s:
        job = s.query(TaskJob).one()
        assert job.status == 'pending' and job.max_attempts == 1
        job_id = job.id
    process.assert_not_called()
    assert second.post(f'/management-agent/sessions/{sid}/stop').status_code == 200
    with transaction() as s:
        assert s.query(TaskJob).filter_by(id=job_id).one().status == 'cancelled'
    assert first.get(f'/management-agent/sessions/{sid}').json()['data']['state'] == 'ready'


@pytest.mark.parametrize('kind,code', [('balance', 402), ('suspended', 403), ('unsettled', 409)])
def test_management_uses_original_account_admission_rules(database, monkeypatch, kind, code):
    from auto_task.store import account
    from auto_task.entities import AiRun
    configure(monkeypatch)
    c = client(); sid = create(c)
    with transaction() as s:
        row = account(s, str(ADMIN['userId']), ADMIN['username'])
        if kind == 'balance': row.balance = -1
        if kind == 'suspended': row.status = 'suspended'
    if kind == 'unsettled':
        agent.start_turn(sid, ADMIN, 'previous')
        with transaction() as s:
            job = s.query(TaskJob).one(); job.status = 'failed'
            s.add(AiRun(id='usage-unknown', task_id=job.id, attempt=1, model='fixture', status='unsettled', usage='{}'))
    assert c.post(f'/management-agent/sessions/{sid}/messages', json={'text': 'new'}).status_code == code


def test_management_fresh_admin_and_current_session_binding_are_mandatory_even_in_debug(database, monkeypatch):
    from auto_task import access
    from auto_task.store import LeaseLost
    configure(monkeypatch)
    c = client(); sid = create(c)
    agent.start_turn(sid, ADMIN, 'turn')
    job = claim()
    fetch = Mock(return_value=dict(ADMIN))
    monkeypatch.setattr(access, 'fetch_actor', fetch)
    access.authorize_execution(job)
    fetch.assert_called_once_with(str(ADMIN['userId']), fresh=True)
    fetch.return_value = {**ADMIN, 'roles': ['user']}
    with pytest.raises(LeaseLost): access.authorize_execution(job)
    fetch.return_value = dict(ADMIN)
    with pytest.raises(LeaseLost): access.authorize_execution({**job, 'payload': dumps({'session_id': 'other'})})
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one(); data = json.loads(row.data); data['task_id'] = 'different-turn'; row.data = dumps(data)
    with pytest.raises(LeaseLost): access.authorize_execution(job)


def test_expired_management_lease_is_failed_without_replay_or_write_confirmation(database, monkeypatch):
    from datetime import datetime, timedelta
    c = client(); sid = create(c); action, job = pending(sid, monkeypatch)
    with transaction() as s:
        s.query(TaskJob).filter_by(id=job['id']).update({'lease_until': datetime.utcnow()-timedelta(seconds=1)})
    assert claim() is None
    with transaction() as s:
        old = s.query(TaskJob).filter_by(id=job['id']).one()
        assert old.status == 'failed' and old.attempt == 1
    state = client().get(f'/management-agent/sessions/{sid}').json()['data']
    assert state['state'] == 'disconnected' and state['actions'][0]['status'] == 'expired'
    execute = Mock(); monkeypatch.setattr(agent, 'execute_action', execute)
    assert c.post(f'/management-agent/sessions/{sid}/actions/call1', json={'action_hash': digest(action), 'approve': True}).status_code == 409
    execute.assert_not_called()


def test_worker_delivers_persisted_confirmation_from_another_web_instance(database, monkeypatch):
    import threading
    from auto_task.worker import run_once
    configure(monkeypatch)
    c = client(); other_web = client(); sid = create(c)
    action = {'method': 'GET', 'path': '/manage/problem/list', 'arguments': {'pageNow': 1}, 'reason': '读取列表'}
    executed = Mock(return_value={'success': True, 'response': {'code': 0}})
    monkeypatch.setattr(agent, 'execute_action', executed)
    original = agent.Agent
    delivered = []
    class FakeAgent:
        receive = original.receive
        mutate = original.mutate
        def __init__(self, session_id, config, key, job):
            self.session_id, self.job = session_id, job
            self.calls = {}; self.finished = threading.Event(); self.outcome = 'failed'; self.thread_id = 'fixture-thread'
        def start(self, data, text):
            self.receive({'id': 31, 'method': 'item/tool/call', 'params': {'tool': 'oj_request', 'arguments': action}})
            state = other_web.get(f'/management-agent/sessions/{sid}').json()['data']
            assert state['state'] == 'approval'
            call = state['actions'][-1]
            response = other_web.post(f'/management-agent/sessions/{sid}/actions/{call["id"]}',
                json={'action_hash': call['hash'], 'approve': True}, headers={'cookie': 'SESSION=current-request-only'})
            assert response.status_code == 200
        def tool_reply(self, rid, result):
            delivered.append((rid, result)); self.outcome = 'success'; self.finished.set()
        def close(self): pass
    monkeypatch.setattr(agent, 'Agent', FakeAgent)
    assert c.post(f'/management-agent/sessions/{sid}/messages', json={'text': 'read'}).status_code == 200
    assert run_once()
    assert delivered == [(31, {'success': True, 'response': {'code': 0}})]
    executed.assert_called_once_with(action, 'SESSION=current-request-only')
    with transaction() as s:
        assert s.query(TaskJob).one().status == 'success'
        assert 'current-request-only' not in s.query(AiResource).filter_by(id=sid).one().data
        assert 'current-request-only' not in s.query(TaskJob).one().payload


def test_uncertain_write_result_is_persisted_once_without_replay(database, monkeypatch):
    c = client(); sid = create(c); action, _ = pending(sid, monkeypatch)
    execute = Mock(side_effect=TimeoutError('Network lost after request'))
    monkeypatch.setattr(agent, 'execute_action', execute)
    endpoint = f'/management-agent/sessions/{sid}/actions/call1'
    body = {'action_hash': digest(action), 'approve': True}
    assert c.post(endpoint, json=body, headers={'cookie': 'SESSION=fresh'}).status_code == 200
    assert client().get(f'/management-agent/sessions/{sid}').json()['data']['actions'][0]['status'] == 'unknown'
    assert client().post(endpoint, json=body).status_code == 409
    execute.assert_called_once()


@pytest.mark.skipif(not __import__('os').environ.get('AUTOLAB_AI_TEST_DATABASE_URL'), reason='Requires isolated real MySQL row locks')
def test_sixteen_web_processes_share_confirmation_state_without_runtime_routing(database, monkeypatch):
    import multiprocessing
    import os
    c = client(); sid = create(c); action, _ = pending(sid, monkeypatch)
    database.dispose()
    context = multiprocessing.get_context('fork')
    results = context.Queue()
    def request_from_web(index):
        # Each child gets a fresh pool; inherited active MySQL connections are never reused.
        database.dispose(close=False)
        agent.execute_action = lambda value, cookie: {'success': True, 'web_pid': os.getpid()}
        try:
            web = client()
            state = web.get(f'/management-agent/sessions/{sid}').json()['data']['state']
            response = web.post(f'/management-agent/sessions/{sid}/actions/call1',
                json={'action_hash': digest(action), 'approve': True}, headers={'cookie': f'SESSION=worker-{index}'})
            results.put((state, response.status_code))
        except Exception as error:
            results.put(('error', type(error).__name__))
        finally: database.dispose()
    children = [context.Process(target=request_from_web, args=(i,)) for i in range(16)]
    try:
        for child in children: child.start()
        responses = [results.get(timeout=20) for _ in children]
        for child in children:
            child.join(timeout=5)
            assert child.exitcode == 0
        assert all(state in ('approval', 'running') for state, _ in responses), responses
        assert [status for _, status in responses].count(200) == 1, responses
        assert [status for _, status in responses].count(409) == 15, responses
        assert c.get(f'/management-agent/sessions/{sid}').json()['data']['actions'][0]['status'] == 'completed'
    finally:
        for child in children:
            if child.is_alive(): child.terminate(); child.join(timeout=5)
        results.close(); results.join_thread()


def metered_agent(monkeypatch, total, fail=False, stop=False, on_start=None):
    import threading
    class Metered:
        def __init__(self, session_id, config, key, job):
            self.session_id, self.job, self.config = session_id, job, config
            self.thread_id = 'metered-thread'
            self.finished = threading.Event(); self.outcome = 'success'; self.calls = {}
            self.usage_total = total
            self.usage_baseline = None
            self.usage_complete = True
        def start(self, data, text):
            self.usage_baseline = data.get('usage_total') if data.get('thread_id') else {
                'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0}
            agent.mutate(self.session_id, lambda row, value: value.update(thread_id=self.thread_id), self.job)
            if on_start: on_start(self)
            if stop: agent.stop_turn(self.session_id, ADMIN)
            if fail: raise RuntimeError('Protocol disconnected')
            self.finished.set()
        def close(self): pass
    monkeypatch.setattr(agent, 'Agent', Metered)


def test_management_usage_is_reserved_and_charged_once_using_saved_prices_and_turn_delta(database, monkeypatch):
    from decimal import Decimal
    from auto_task.entities import AiRun, AiLedger
    from auto_task.accounting import settle_run
    from auto_task.store import account
    from auto_task.worker import run_once
    config = configure(monkeypatch)
    config.input_usd_per_million = 2; config.cached_usd_per_million = 1; config.output_usd_per_million = 4
    c = client(); sid = create(c)
    with transaction() as s: account(s, '7', 'teacher').balance = 100
    total = {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 50}
    def check_intent(runtime):
        with transaction() as s:
            run = s.query(AiRun).one()
            record = json.loads(run.usage)
            assert run.status == 'running' and record['reserved_usd'] != '0'
            assert record['pricing']['input'] == 2
        # A later configuration change must not reprice an existing call.
        config.input_usd_per_million = 99
    metered_agent(monkeypatch, total, on_start=check_intent)
    agent.start_turn(sid, ADMIN, 'first')
    assert run_once()
    with transaction() as s:
        first = s.query(AiRun).one(); first_id = first.id
        assert first.status == 'success' and first.cost_usd == Decimal('0.000380000')
        assert s.query(AiLedger).count() == 1
        assert account(s, '7').balance == Decimal('99.620000')
    replay = settle_run(first_id, {'input_tokens': 9999, 'output_tokens': 9999}, 'success')
    assert replay['replay']
    config.input_usd_per_million = 2
    metered_agent(monkeypatch, {'input_tokens': 150, 'cached_input_tokens': 25, 'output_tokens': 75})
    agent.start_turn(sid, ADMIN, 'second')
    assert run_once()
    with transaction() as s:
        runs = s.query(AiRun).order_by(AiRun.created_at).all()
        assert len(runs) == 2 and s.query(AiLedger).count() == 2
        assert runs[1].cost_usd == Decimal('0.000195000')
        assert json.loads(runs[1].usage)['input_tokens'] == 50
        assert account(s, '7').balance == Decimal('99.425000')


@pytest.mark.parametrize('mode', ['missing', 'failed', 'cancelled', 'old-session'])
def test_management_unknown_usage_remains_unsettled_and_blocks_new_paid_turns(database, monkeypatch, mode):
    from auto_task.entities import AiRun, AiLedger
    from auto_task.worker import run_once
    configure(monkeypatch)
    c = client(); sid = create(c)
    if mode == 'old-session':
        with transaction() as s:
            row = s.query(AiResource).filter_by(id=sid).one(); data = json.loads(row.data)
            data['thread_id'] = 'pre-accounting-thread'; row.data = dumps(data)
    total = None if mode == 'missing' else {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 50}
    metered_agent(monkeypatch, total, fail=mode == 'failed', stop=mode == 'cancelled')
    agent.start_turn(sid, ADMIN, 'first')
    assert run_once()
    with transaction() as s:
        run = s.query(AiRun).one()
        assert run.status == 'unsettled' and json.loads(run.usage)['usage_complete'] is False
        assert s.query(AiLedger).count() == 0
    assert c.post(f'/management-agent/sessions/{sid}/messages', json={'text': 'retry'}).status_code == 409


@pytest.mark.parametrize('value', [None, {}, {'inputTokens': 5, 'outputTokens': 2},
    {'inputTokens': True, 'cachedInputTokens': 0, 'outputTokens': 2},
    {'inputTokens': 5, 'cachedInputTokens': 6, 'outputTokens': 2},
    {'inputTokens': -1, 'cachedInputTokens': 0, 'outputTokens': 2}])
def test_protocol_missing_or_invalid_usage_is_not_zero_usage(value):
    assert agent.protocol_usage(value) is None
    assert agent.usage_delta(agent.protocol_usage(value), {'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0}) == {}


def test_protocol_usage_requires_monotonic_counters_and_valid_cache_delta():
    assert agent.protocol_usage({'inputTokens': 100, 'cachedInputTokens': 25, 'outputTokens': 50}) == {
        'input_tokens': 100, 'cached_input_tokens': 25, 'output_tokens': 50}
    base = {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 50}
    assert agent.usage_delta({'input_tokens': 90, 'cached_input_tokens': 20, 'output_tokens': 60}, base) == {}
    assert agent.usage_delta({'input_tokens': 101, 'cached_input_tokens': 25, 'output_tokens': 60}, base) == {}
