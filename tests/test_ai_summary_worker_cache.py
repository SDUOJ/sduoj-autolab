"""A summary may be the first task in a fresh standalone worker process."""
import os
from pathlib import Path
import subprocess
import sys


def test_first_summary_initializes_cache_in_fresh_worker():
    program = r'''from types import SimpleNamespace
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from auto_task import summary_report
FastAPICache.reset()
closed = []
class Model:
    def __init__(self):
        self.session = SimpleNamespace(close=lambda: closed.append(True))
summary_report.answerSheetModel = Model
summary_report.problemSetModel = Model
async def execute(service):
    assert isinstance(FastAPICache.get_backend(), RedisBackend)
    return {"initialized": True}
summary_report._SummaryReportService._execute_inner = execute
service = summary_report._SummaryReportService(SimpleNamespace(), "first-summary")
assert service.execute() == {"initialized": True}
assert len(closed) == 2
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[1],
                            env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
