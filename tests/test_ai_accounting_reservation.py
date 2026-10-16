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
