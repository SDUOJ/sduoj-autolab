import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from tests.test_ai_backend import database, ADMIN, USER
from auth import cover_header
from auto_task import management_agent as agent
from auto_task.store import transaction, dumps, digest
from auto_task.entities import AiResource
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
    runtime = SimpleNamespace(closed=False, calls={'call1': 99}, tool_reply=Mock())
    monkeypatch.setitem(agent.RUNTIMES, sid, runtime)
    with transaction() as s:
        row = s.query(AiResource).filter_by(id=sid).one()
        data = json.loads(row.data); data['actions'] = [{'id': 'call1', 'hash': digest(value), 'status': 'pending', 'action': value}]
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
    runtime.tool_reply.assert_called_once()


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
    runtime.closed = True
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
    runtime = object.__new__(agent.Agent); runtime.session_id = sid; runtime.calls = {}; runtime.send = Mock(); runtime.tool_reply = Mock()
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
def test_real_app_server_tool_confirmation_loop(database, monkeypatch):
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
            events = [{'type': 'response.created', 'response': response}, {'type': 'response.output_item.added', 'output_index': 0, 'item': item}, {'type': 'response.output_item.done', 'output_index': 0, 'item': item}, {'type': 'response.completed', 'response': {**response, 'status': 'completed', 'output': [item], 'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}}}]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for sequence, event in enumerate(events):
                self.wfile.write(('data: '+dumps({**event, 'sequence_number': sequence})+'\n\n').encode()); self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = ProviderConfig(provider='codex', base_url=f'http://127.0.0.1:{server.server_port}/v1', model='test-model', timeout_seconds=60)
    monkeypatch.setattr(agent, 'provider', lambda s: (config, 'local-test-only'))
    execute = Mock(return_value={'success': True, 'response': {'code': 0, 'data': {'rows': []}}})
    monkeypatch.setattr(agent, 'execute_action', execute)
    c = client(); sid = create(c)
    try:
        r = c.post(f'/management-agent/sessions/{sid}/messages', json={'text': '查询题目列表，等待我在界面确认。'})
        assert r.status_code == 200, r.text
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
        assert len(model_calls) == 2
        assert {t.get('name') for t in model_calls[0].get('tools', [])} <= {'oj_request', 'request_user_input', 'update_plan', 'skills'}
    finally:
        runtime = agent.runtime(sid)
        if runtime: runtime.close()
        agent.RUNTIMES.pop(sid, None)
        server.shutdown(); server.server_close()
