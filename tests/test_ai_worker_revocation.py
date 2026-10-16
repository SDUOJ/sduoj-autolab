"""Authorization failure closes only the current worker's valid lease."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from auto_task import store, worker
from auto_task.entities import AiLedger, AiRun, TaskJob
from tests.test_ai_backend import ADMIN, database, enqueue
from tests.test_ai_collection_collaboration import (
    as_user, collaboration_client, client,
)
from tests.test_ai_collection_native_chain import native_intake


def test_revoked_native_grant_immediately_fails_and_releases_worker_slot(collaboration_client, monkeypatch):
    c = collaboration_client
    _, calls = native_intake(c, monkeypatch, auto_solve=True)
    with store.transaction() as session:
        job_id = session.query(TaskJob).filter_by(task_type="question.solve").one().id
    as_user(c, ADMIN)
    assert c.post("/auto-task/ai/collection-collaboration/shared/members/8/revoke").status_code == 200
    calls.clear()
    assert worker.run_once()
    assert calls == []
    with store.transaction() as session:
        row = session.query(TaskJob).get(job_id)
        assert row.status == "failed"
        assert row.finished_at is not None and row.lease_token is None
        assert row.error == "OJ task authorization revoked"
    assert store.claim() is None


@pytest.mark.parametrize("lease_state", ["expired", "replaced"])
@pytest.mark.parametrize("failure_type", [store.LeaseLost, store.AuthorizationRevoked])
def test_old_worker_cannot_overwrite_an_expired_or_replaced_lease(database, monkeypatch, lease_state, failure_type):
    task_id = enqueue()
    job = store.claim()
    assert job["id"] == task_id
    monkeypatch.setattr(worker, "claim", lambda: job)
    fail = Mock(wraps=worker.fail)
    monkeypatch.setattr(worker, "fail", fail)

    def check():
        with store.transaction() as session:
            row = session.query(TaskJob).get(task_id)
            row.error = "successor state"
            if lease_state == "expired":
                row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            else:
                row.lease_token = "replacement-lease"
                row.attempt += 1
                row.lease_until = datetime.utcnow() + timedelta(seconds=60)
        raise failure_type("Access or lease changed")

    monkeypatch.setattr("auto_task.runtime.Context", lambda current: SimpleNamespace(check=check))
    assert worker.run_once()
    assert fail.call_count == (1 if failure_type is store.AuthorizationRevoked else 0)
    with store.transaction() as session:
        row = session.query(TaskJob).get(task_id)
        assert row.status == "running" and row.error == "successor state"
        assert row.lease_token == ("replacement-lease" if lease_state == "replaced" else job["lease_token"])
        assert row.finished_at is None


def test_revocation_preserves_unknown_inflight_usage_as_unsettled(database, monkeypatch):
    task_id = enqueue()
    monkeypatch.setattr("auto_task.runtime.Context", lambda current: SimpleNamespace(check=lambda: None))

    def interrupted(ctx):
        with store.transaction() as session:
            job = session.query(TaskJob).get(task_id)
            session.add(AiRun(id="interrupted-model-call", task_id=task_id, attempt=job.attempt,
                              model="fixture", status="running", usage=store.dumps({"input_tokens": 123})))
        raise store.AuthorizationRevoked("OJ task authorization revoked")

    monkeypatch.setattr("auto_task.handlers.handle", interrupted)
    assert worker.run_once()
    with store.transaction() as session:
        job = session.query(TaskJob).get(task_id)
        run = session.query(AiRun).get("interrupted-model-call")
        assert job.status == "failed" and job.lease_token is None
        assert run.status == "unsettled" and json.loads(run.usage) == {"input_tokens": 123}
        assert session.query(AiLedger).filter_by(task_id=task_id).count() == 0
        assert store.has_unsettled_usage(session, job.payer_id)
