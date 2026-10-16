"""Standalone durable worker: python -m auto_task.worker. Web startup is opt-in."""

import logging
import os
import signal
import threading
from multiprocessing import Process

from .store import AuthorizationRevoked, LeaseLost, claim, complete, fail

_worker_process = None
logger = logging.getLogger(__name__)


def start_background_worker():
    global _worker_process
    if os.environ.get("AUTOLAB_START_TASK_WORKER") != "1":
        return
    if _worker_process and _worker_process.is_alive():
        return
    _worker_process = Process(target=run, name="autolab-durable-worker", daemon=True)
    _worker_process.start()


def run_once():
    job = claim()
    if not job:
        return False
    from .runtime import ACTIVE_CONTEXT, Context
    from .handlers import handle
    try:
        ctx = Context(job)
    except Exception as error:
        try:
            fail(job["id"], job["lease_token"], "Workspace unavailable: " + type(error).__name__)
        except LeaseLost:
            pass
        return True
    stop = threading.Event()
    def renew():
        from .store import heartbeat
        while not stop.wait(10):
            try:
                heartbeat(job["id"], job["lease_token"])
            except Exception:
                return
    heartbeats = threading.Thread(target=renew, daemon=True)
    heartbeats.start()
    context_token = ACTIVE_CONTEXT.set(ctx)
    try:
        ctx.check()
        result, apply = handle(ctx)
        from .access import authorize_execution
        authorize_execution(job)
        complete(job["id"], job["lease_token"], result, apply)
    except AuthorizationRevoked as error:
        try:
            # fail() fences the write with this worker's original lease. A
            # revoked identity must release its slot, never a successor's lease.
            fail(job["id"], job["lease_token"], str(error), retry=False)
        except LeaseLost:
            pass
        logger.info("Task %s authorization revoked", job["id"])
    except LeaseLost:
        logger.info("Task %s lease revoked", job["id"])
    except Exception as error:
        try:
            fail(job["id"], job["lease_token"], type(error).__name__, retry=not isinstance(error, (ValueError, KeyError)))
        except LeaseLost:
            pass
        logger.warning("Task %s failed: %s", job["id"], type(error).__name__)
    finally:
        ACTIVE_CONTEXT.reset(context_token)
        stop.set()
        heartbeats.join(timeout=2)
    return True


def run():
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    subscriber = None
    try:
        from .store import redis_client
        subscriber = redis_client().pubsub(ignore_subscribe_messages=True)
        subscriber.subscribe("autolab:ai:wake")
    except Exception:
        subscriber = None
    while not stopped.is_set():
        try:
            if not run_once():
                if subscriber is not None:
                    try:
                        subscriber.get_message(timeout=2)
                    except Exception:
                        subscriber.close()
                        subscriber = None
                else:
                    stopped.wait(2)
        except Exception as error:
            logger.error("Worker unavailable: %s", type(error).__name__)
            stopped.wait(5)
    if subscriber is not None:
        subscriber.close()


if __name__ == "__main__":
    run()
