"""Background worker that consumes automation tasks from Redis."""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from multiprocessing import Process
from typing import Any, Dict, Optional

import aioredis
import redis
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis.exceptions import RedisError

from const import Redis_addr, Redis_pass
from model.auto_task import autoTaskModel
from .base import TASK_REGISTRY, TaskDeletedError
from .constants import TASK_QUEUE_NAME
_worker_process: Optional[Process] = None
_CACHE_INITIALIZED = False


def start_background_worker() -> None:
    """Spawn a dedicated process that keeps consuming Redis tasks."""

    global _worker_process
    if _worker_process is not None and _worker_process.is_alive():
        return

    process = Process(target=_run_worker_process, name="auto-task-worker", daemon=True)
    process.start()
    _worker_process = process


def _run_worker_process() -> None:
    logging.basicConfig(level=logging.INFO)
    _init_worker_cache()
    worker = AutoTaskWorker()
    worker.run_forever()


class AutoTaskWorker:
    def __init__(self) -> None:
        self.logger = logging.getLogger("auto_task.worker")
        self.worker_id = f"pid-{os.getpid()}"
        self.redis = redis.Redis.from_url(
            f"redis://{Redis_addr}/0",
            password=Redis_pass,
            decode_responses=True,
        )

    def run_forever(self) -> None:
        self.logger.info("Auto task worker %s started", self.worker_id)
        while True:
            try:
                item = self.redis.blpop(TASK_QUEUE_NAME, timeout=5)
            except RedisError as exc:
                self.logger.exception("Redis connection error: %s", exc)
                time.sleep(3)
                continue

            if item is None:
                continue

            _, raw_task = item
            self._handle_task(raw_task)

    def _handle_task(self, raw_task: str) -> None:
        task_data: Dict[str, Any]
        try:
            task_data = json.loads(raw_task)
        except json.JSONDecodeError:
            self.logger.error("Invalid task payload: %s", raw_task)
            self._persist_invalid_task(raw_task, "invalid_json")
            return

        task_type = task_data.get("type") or "unknown"
        payload = task_data.get("payload")

        model = autoTaskModel()
        task_id: Optional[str] = None

        try:
            task_id = model.prepare_task_run(
                task_data.get("task_id"), task_type, payload
            )
            task_cls = TASK_REGISTRY.get(task_type)
            if task_cls is None:
                raise ValueError(f"Unknown task type: {task_type}")
            task_instance = task_cls(payload, task_data)
            result = task_instance.run()
            model.finish_task_success(task_id, result)
        except TaskDeletedError:
            self.logger.info("Task %s deleted during execution, skipping", task_data.get("task_id"))
        except Exception:  # noqa: BLE001 - must log task failure
            error_trace = traceback.format_exc()
            if task_id is not None:
                model.finish_task_failure(task_id, error_trace)
            else:
                model.record_invalid_task(raw_task, error_trace)
            self.logger.exception("Task %s failed", task_type)
        finally:
            model.session.close()

    def _persist_invalid_task(self, raw_task: str, error: str) -> None:
        model = autoTaskModel()
        try:
            model.record_invalid_task(raw_task, error)
        finally:
            model.session.close()


def _init_worker_cache() -> None:
    global _CACHE_INITIALIZED
    if _CACHE_INITIALIZED:
        return
    redis_client = aioredis.from_url(
        f"redis://{Redis_addr}/0",
        password=Redis_pass,
        encoding="utf8",
        decode_responses=True,
    )
    FastAPICache.init(RedisBackend(redis_client), prefix="fastapi-cache-worker")
    _CACHE_INITIALIZED = True


__all__ = ["start_background_worker", "TASK_QUEUE_NAME"]
