"""Every actual model HTTP request is admitted before forwarding and settled once."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tests.test_ai_backend import database, ADMIN
from tests.test_management_agent import client, create, configure
from auto_task import management_agent as agent
from auto_task import management_proxy as proxy_module
from auto_task.accounting import settle_run
from auto_task.entities import AiLedger, AiRun
from auto_task.store import account, claim, transaction


class Response:
    status_code = 200
    def __init__(self, usage, streaming=False, duplicate=False, broken=False):
        self.value = {'id': 'response-fixture', 'status': 'completed', 'output': [], 'usage': usage}
        self.streaming, self.duplicate, self.broken = streaming, duplicate, broken
        self.close = Mock()
    def iter_content(self, size):
        if self.broken: raise requests.ConnectionError('Synthetic upstream disconnect')
        yield json.dumps(self.value).encode()
    def iter_lines(self, chunk_size):
        if self.broken: raise requests.ConnectionError('Synthetic upstream disconnect')
        for _ in range(2 if self.duplicate else 1):
            yield b'data: ' + json.dumps({'type': 'response.completed', 'response': self.value}).encode()
            yield b''


@pytest.fixture
def bridge(database, monkeypatch):
    config = configure(monkeypatch)
    config.output_usd_per_million = 1
    config.max_output_tokens = 128
    c = client(); sid = create(c)
    with transaction() as s: account(s, '7', 'teacher').balance = 100
    agent.start_turn(sid, ADMIN, 'fixture')
    job = claim()
    responses = []
    forwarded = []
    class Upstream:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def post(self, url, **kwargs):
            assert self.trust_env is False
            forwarded.append((url, kwargs))
            return responses.pop(0)
    monkeypatch.setattr(proxy_module.requests, 'Session', Upstream)
    proxy = proxy_module.ModelProxy(SimpleNamespace(job=job), config, 'upstream-fixture-key')
    def call(stream=False, **kwargs):
        return requests.post(proxy.base_url + '/responses', json={'model': 'ignored', 'stream': stream, 'input': []},
                             headers={'Authorization': 'Bearer '+proxy.token}, timeout=10, **kwargs)
    yield SimpleNamespace(proxy=proxy, config=config, job=job, responses=responses,
                          forwarded=forwarded, call=call, sid=sid)
    proxy.close()


@pytest.mark.parametrize('streaming', [False, True])
def test_request_billing_is_idempotent_and_uses_saved_tariff_before_reply(bridge, streaming):
    bridge.responses.append(Response({'input_tokens': 10, 'output_tokens': 128}, duplicate=True))
    assert bridge.call(streaming).status_code == 200
    with transaction() as s:
        run = s.query(AiRun).one(); run_id = run.id
        assert run.status == 'success' and run.cost_usd == Decimal('0.000128000')
        assert s.query(AiLedger).count() == 1
    assert settle_run(run_id, {'input_tokens': 999, 'output_tokens': 999}, 'success')['replay']
    with transaction() as s: assert s.query(AiLedger).count() == 1
    url, request = bridge.forwarded[0]
    assert url == 'http://127.0.0.1:1/v1/responses'
    assert request['allow_redirects'] is False
    assert request['headers']['Authorization'] == 'Bearer upstream-fixture-key'
    assert 'Cookie' not in request['headers']
    assert request['json']['max_output_tokens'] == 128
    assert request['json']['model'] == 'fixture'


@pytest.mark.parametrize('budget', ['task', 'monthly'])
def test_second_request_is_refused_before_upstream_when_aggregate_budget_is_exhausted(bridge, budget):
    if budget == 'task': bridge.config.max_task_cost_usd = 0.0002
    else: bridge.config.monthly_user_budget_usd = 0.0002
    bridge.responses.extend([Response({'input_tokens': 10, 'output_tokens': 128}) for _ in range(2)])
    assert bridge.call().status_code == 200
    assert bridge.call().status_code == 402
    assert len(bridge.forwarded) == 1
    with transaction() as s:
        assert s.query(AiRun).count() == 1
        assert s.query(AiLedger).count() == 1


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('fault', ['missing', 'broken'])
def test_unknown_or_interrupted_usage_is_unsettled_before_any_continuation(bridge, streaming, fault):
    bridge.responses.append(Response({} if fault == 'missing' else {'input_tokens': 10, 'output_tokens': 10}, broken=fault == 'broken'))
    bridge.call(streaming)
    with transaction() as s:
        run = s.query(AiRun).one()
        assert run.status == 'unsettled' and json.loads(run.usage)['usage_complete'] is False
        assert s.query(AiLedger).count() == 0
    assert bridge.call().status_code == 402
    assert len(bridge.forwarded) == 1


def test_each_request_requires_its_current_turn_token_and_only_the_responses_path(bridge):
    for path, token in [('/responses', 'wrong'), ('/v1/chat/completions', bridge.proxy.token), ('/v1/responses?other=1', bridge.proxy.token), ('/v1/responses', 'wrong'), ('/v1/responses', 'é')]:
        origin = bridge.proxy.base_url.removesuffix('/v1')
        response = requests.post(origin+path, json={'input': []}, headers={'Authorization': 'Bearer '+token}, timeout=5)
        assert response.status_code in (401, 404)
    with transaction() as s: assert s.query(AiRun).count() == 0
    assert bridge.forwarded == []


def test_each_request_rechecks_fresh_administrator_identity_before_upstream(bridge, monkeypatch):
    from auto_task import access
    actor = Mock(return_value={**ADMIN, 'roles': ['user']})
    monkeypatch.setattr(access, 'fetch_actor', actor)
    assert bridge.call().status_code == 403
    actor.assert_called_once_with('7', fresh=True)
    assert bridge.forwarded == []
    with transaction() as s: assert s.query(AiRun).count() == 0


def test_negative_points_between_requests_block_the_next_actual_call(bridge):
    bridge.responses.append(Response({'input_tokens': 10, 'output_tokens': 128}))
    with transaction() as s: account(s, '7').balance = 0
    assert bridge.call().status_code == 200
    assert bridge.call().status_code == 402
    assert len(bridge.forwarded) == 1


def test_stop_revokes_the_proxy_before_upstream_and_leaves_no_free_retry(bridge):
    agent.stop_turn(bridge.sid, ADMIN)
    assert bridge.call().status_code == 403
    assert bridge.forwarded == []


def test_response_cached_tokens_use_the_cached_tariff(bridge):
    bridge.config.input_usd_per_million = 2
    bridge.config.cached_usd_per_million = 1
    bridge.responses.append(Response({'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 20}, 'output_tokens': 128}))
    assert bridge.call().status_code == 200
    with transaction() as s:
        run = s.query(AiRun).one()
        assert run.cost_usd == Decimal('0.000308000')
        assert json.loads(run.usage)['cached_input_tokens'] == 20


def test_saved_price_snapshot_survives_configuration_changes_during_a_request(bridge):
    response = Response({'input_tokens': 10, 'output_tokens': 128})
    original = response.iter_content
    def output(size):
        bridge.config.output_usd_per_million = 99
        yield from original(size)
    response.iter_content = output
    bridge.responses.append(response)
    assert bridge.call().status_code == 200
    with transaction() as s:
        run = s.query(AiRun).one()
        assert json.loads(run.usage)['pricing']['output'] == 1
        assert run.cost_usd == Decimal('0.000128000')


def test_cancellation_during_a_provider_response_keeps_unknown_usage_unsettled(bridge):
    response = Response({})
    def interrupted(size):
        agent.stop_turn(bridge.sid, ADMIN)
        raise requests.ConnectionError('Stopped during response')
        yield b''
    response.iter_content = interrupted
    bridge.responses.append(response)
    assert bridge.call().status_code == 502
    with transaction() as s:
        assert s.query(AiRun).one().status == 'unsettled'
        assert s.query(AiLedger).count() == 0
    assert bridge.call().status_code == 403
    assert len(bridge.forwarded) == 1


def test_another_turn_cannot_reuse_a_previous_loopback_token(bridge):
    other = proxy_module.ModelProxy(SimpleNamespace(job=bridge.job), bridge.config, 'upstream-fixture-key')
    try:
        assert other.token != bridge.proxy.token
        response = requests.post(other.base_url+'/responses', json={'input': []},
            headers={'Authorization': 'Bearer '+bridge.proxy.token}, timeout=5)
        assert response.status_code == 401
        with transaction() as s: assert s.query(AiRun).count() == 0
    finally: other.close()


@pytest.mark.skipif(not __import__('os').environ.get('AUTOLAB_AI_TEST_DATABASE_URL'), reason='Requires isolated real MySQL reservation locks')
def test_concurrent_requests_cannot_reserve_past_the_same_turn_budget(bridge):
    import threading
    bridge.config.max_task_cost_usd = 0.0002
    entered, release = threading.Event(), threading.Event()
    response = Response({'input_tokens': 10, 'output_tokens': 128})
    original = response.iter_content
    def waiting(size):
        entered.set()
        assert release.wait(10)
        yield from original(size)
    response.iter_content = waiting
    bridge.responses.extend([response, Response({'input_tokens': 10, 'output_tokens': 128})])
    result = []
    first = threading.Thread(target=lambda: result.append(bridge.call().status_code))
    first.start()
    try:
        assert entered.wait(5)
        assert bridge.call().status_code == 402
    finally:
        release.set(); first.join(timeout=10)
    assert result == [200] and len(bridge.forwarded) == 1
    with transaction() as s: assert s.query(AiRun).count() == 1


@pytest.mark.parametrize('streaming', [False, True])
def test_provider_messages_cannot_echo_model_or_local_turn_credentials(bridge, streaming):
    response = Response({'input_tokens': 10, 'output_tokens': 10})
    response.value['output'] = [{'text': 'upstream-fixture-key '+bridge.proxy.token}]
    bridge.responses.append(response)
    result = bridge.call(streaming)
    assert result.status_code == 200
    assert 'upstream-fixture-key' not in result.text
    assert bridge.proxy.token not in result.text
    assert '[redacted]' in result.text
