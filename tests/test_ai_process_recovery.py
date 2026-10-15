"""Real process death against the opt-in isolated MySQL fixture."""

import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest
from tests.test_ai_backend import database, enqueue
from auto_task import store
from auto_task.entities import AiRun, TaskJob


@pytest.mark.skipif(not os.environ.get("AUTOLAB_AI_TEST_DATABASE_URL"), reason="Requires isolated local MySQL")
def test_hard_exit_after_call_intent_cannot_retry_without_usage_reconciliation(database):
    task_id = enqueue("crash-process")
    child = '''
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from auto_task import store
from auto_task.accounting import begin_run
from auto_task.configuration import ProviderConfig
store._factory = sessionmaker(bind=create_engine(os.environ["AUTOLAB_AI_TEST_DATABASE_URL"]), expire_on_commit=False)
job = store.claim()
assert job["id"] == os.environ["AUTOLAB_TEST_EXPECTED_TASK"]
begin_run(job, ProviderConfig(base_url="https://example.invalid", model="no-network-fixture"))
os._exit(99)
'''
    result = subprocess.run([sys.executable, "-c", child], env=dict(os.environ, AUTOLAB_TEST_EXPECTED_TASK=task_id), capture_output=True, timeout=20)
    assert result.returncode == 99, result.stderr.decode()
    with store.transaction() as s:
        job = s.query(TaskJob).get(task_id)
        assert job.status == "running"
        assert s.query(AiRun).filter_by(task_id=task_id, status="running").count() == 1
        job.lease_until = datetime.utcnow() - timedelta(seconds=1)
    assert store.claim() is None
    with store.transaction() as s:
        assert s.query(TaskJob).get(task_id).status == "blocked"
        assert s.query(AiRun).filter_by(task_id=task_id, status="unsettled").count() == 1
        assert s.query(TaskJob).get(task_id).attempt == 1
