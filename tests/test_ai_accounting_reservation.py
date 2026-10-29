"""Reservation bounds must cover either legal input tariff, independently of billing."""
import json
from decimal import Decimal

import pytest

from auto_task import store
from auto_task.accounting import begin_run, settle_run
from auto_task.configuration import ProviderConfig
from auto_task.entities import AiLedger, AiRun
from tests.test_ai_backend import database, enqueue


PRICES = [(1, 4), (4, 1)]


def config_for(input_price, cached_price, gate=None, limit=2):
    return ProviderConfig(provider='codex', base_url='http://127.0.0.1:1/v1', model='fixture',
                          input_usd_per_million=input_price, cached_usd_per_million=cached_price,
                          output_usd_per_million=0, max_prompt_bytes=1024, max_output_tokens=128,
                          max_task_cost_usd=limit if gate == 'task' else 2,
                          monthly_user_budget_usd=limit if gate == 'monthly' else 0)


@pytest.mark.parametrize('input_price,cached_price', PRICES)
@pytest.mark.parametrize('gate', ['task', 'monthly'])
def test_either_input_tariff_must_fit_the_initial_task_or_monthly_reservation(
        database, input_price, cached_price, gate):
    enqueue()
    job = store.claim()
    config = config_for(input_price, cached_price, gate, 0.003)
    with pytest.raises(ValueError, match='budget'):
        begin_run(job, config, config.max_prompt_bytes)
    with store.transaction() as session:
        assert session.query(AiRun).count() == session.query(AiLedger).count() == 0


@pytest.mark.parametrize('input_price,cached_price', PRICES)
@pytest.mark.parametrize('gate', ['task', 'monthly'])
def test_actual_settlement_and_next_reservation_share_the_task_or_monthly_limit(
        database, input_price, cached_price, gate):
    enqueue()
    job = store.claim()
    config = config_for(input_price, cached_price, gate, 0.006)
    run_id = begin_run(job, config, config.max_prompt_bytes)
    with store.transaction() as session:
        run = session.query(AiRun).get(run_id)
        assert Decimal(json.loads(run.usage)['reserved_usd']) == Decimal('0.004096')
    cached = 1024 if cached_price > input_price else 0
    settle_run(run_id, {'input_tokens': 1024, 'cached_input_tokens': cached, 'output_tokens': 0}, 'success')
    with pytest.raises(ValueError, match='budget'):
        begin_run(job, config, config.max_prompt_bytes)
    with store.transaction() as session:
        run = session.query(AiRun).one()
        assert run.status == 'success' and run.cost_usd == Decimal('0.004096000')
        assert session.query(AiLedger).one().cost_usd == Decimal('0.004096000')


@pytest.mark.parametrize('input_price,cached_price,expected', [
    (1, 4, '0.000896000'),
    (4, 1, '0.001664000'),
])
def test_conservative_reservation_does_not_change_actual_cached_token_billing(
        database, input_price, cached_price, expected):
    enqueue()
    job = store.claim()
    config = config_for(input_price, cached_price)
    run_id = begin_run(job, config, config.max_prompt_bytes)
    usage = {'input_tokens': 512, 'cached_input_tokens': 128, 'output_tokens': 0}
    settle_run(run_id, usage, 'success')
    assert settle_run(run_id, usage, 'success')['replay']
    with store.transaction() as session:
        assert session.query(AiRun).one().cost_usd == Decimal(expected)
        assert session.query(AiLedger).count() == 1


@pytest.mark.parametrize('outcome', ['success', 'failed'])
@pytest.mark.parametrize('same_task', [True, False])
def test_missing_usage_blocks_further_paid_calls_for_same_or_other_task(
        database, outcome, same_task):
    from auto_task.entities import AiAccount, TaskJob
    from tests.test_ai_backend import model_config
    from db import AutoTaskRun
    from datetime import datetime, timedelta
    task_id = enqueue()
    job = store.claim()
    config = model_config().copy(update={'provider': 'responses'})
    run_id = begin_run(job, config, 32)
    settled = settle_run(run_id, {}, outcome)
    assert settled['status'] == 'unsettled'
    with store.transaction() as session:
        before_balance = session.query(AiAccount).filter_by(subject_id=job['payer_id']).one().balance
        before_runs = session.query(AiRun).count()
        before_ledger = session.query(AiLedger).count()
    if not same_task:
        # Keep the same payer, owner, resource and valid lease; only task identity
        # differs. This calls the real reservation gate with no admission mocks.
        with store.transaction() as session:
            other = TaskJob(id='other-missing-usage-task', owner_id=job['owner_id'],
                            owner_username=job['owner_username'], payer_id=job['payer_id'],
                            operation_key='other', input_hash='other', payload='{}',
                            task_type='question.extract', status='running', attempt=1,
                            max_attempts=3, lease_token='other-valid-lease',
                            lease_until=datetime.utcnow() + timedelta(seconds=30),
                            available_at=datetime.utcnow())
            session.add(AutoTaskRun(id=other.id, task_type=other.task_type, status='running', username=job['owner_username']))
            session.add(other)
        job = {**job, 'id': 'other-missing-usage-task', 'lease_token': 'other-valid-lease'}
    with pytest.raises(ValueError, match='reconciliation required'):
        begin_run(job, config, 32)
    with store.transaction() as session:
        assert session.query(AiRun).count() == before_runs
        assert session.query(AiLedger).count() == before_ledger
        assert session.query(AiAccount).filter_by(subject_id=job['payer_id']).one().balance == before_balance
        assert session.query(AiRun).get(run_id).status == 'unsettled'


def test_successful_responses_eof_without_usage_stays_unsettled_and_blocks_next_call(
        database, monkeypatch):
    from auto_task.configuration import save_provider
    from auto_task.runtime import Context
    from tests.test_ai_backend import model_config
    config = model_config().copy(update={'provider': 'responses'})
    with store.transaction() as session:
        save_provider(session, config, 'synthetic-provider-key')
    task_id = enqueue()
    job = store.claim()
    calls = []

    class Response:
        ok = True
        def iter_lines(self, decode_unicode=True):
            yield 'data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': '{"answer":"4"}'})
        def close(self):
            pass

    def post(*args, **kwargs):
        calls.append(args[0])
        return Response()

    monkeypatch.setattr('auto_task.runtime.requests.post', post)
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    context = Context(job)
    assert context.ask('2+2?', schema) == {'answer': '4'}
    with store.transaction() as session:
        first = session.query(AiRun).filter_by(task_id=task_id).one()
        record = json.loads(first.usage)
        assert first.status == 'unsettled'
        assert record['usage_complete'] is False and record['outcome'] == 'success'
        assert Decimal(record['reserved_usd']) > 0
        assert session.query(AiLedger).count() == 0
    with pytest.raises(ValueError, match='reconciliation required'):
        context.ask('next paid stage', schema)
    assert calls == ['https://example.invalid/v1/responses']
    with store.transaction() as session:
        assert session.query(AiRun).count() == 1
        assert session.query(AiLedger).count() == 0
