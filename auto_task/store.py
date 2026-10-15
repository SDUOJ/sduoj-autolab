"""Transactions and fenced claims. Redis is a wake-up hint, never the task authority."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, create_engine, or_
from sqlalchemy.orm import sessionmaker

from db import AutoTaskRun, AutoTaskRunLog, link
from .entities import AiAccount, AiLedger, AiReview, AiRun, AiSetting, TaskInput, TaskJob, uid

_factory = None
_redis = None
TERMINAL = {"success", "failed", "cancelled"}
NON_AI = {"summary_report", "ide.execute", "exam.plan"}


def legacy_time(value):
    return value + timedelta(hours=8) if value is not None else None


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_log(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def factory():
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=create_engine(link, pool_pre_ping=True, pool_recycle=1800), expire_on_commit=False)
    return _factory


@contextmanager
def transaction():
    session = factory()()
    try:
        yield session
        session.commit()
        if session.info.get("ai_wake"):
            notify()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def redis_client():
    global _redis
    if _redis is None:
        import redis
        from const import Redis_addr, Redis_pass
        _redis = redis.Redis.from_url("redis://" + Redis_addr + "/0", password=Redis_pass,
                                     socket_connect_timeout=1, socket_timeout=3)
    return _redis


def notify():
    if os.environ.get("AUTOLAB_AI_WAKE", "1") == "0":
        return
    try:
        redis_client().publish("autolab:ai:wake", "1")
    except Exception:
        # The committed MySQL task remains recoverable if Redis is unavailable.
        pass


def principal(user):
    if not isinstance(user, dict) or not isinstance(user.get("username"), str):
        raise HTTPException(401, "Valid SDUOJ identity required")
    value = str(user.get("userId", ""))
    if not value.isascii() or not value.isdecimal() or int(value) <= 0 or not user.get("username"):
        raise HTTPException(401, "Valid SDUOJ identity required")
    return str(int(value))


def account(session, subject, username=None):
    if session.bind.dialect.name == "mysql":
        from sqlalchemy.dialects.mysql import insert
        statement = insert(AiAccount).values(subject_id=subject, username=username or subject, balance=0, status="active", permanent=0)
        session.execute(statement.on_duplicate_key_update(subject_id=statement.inserted.subject_id))
    row = session.query(AiAccount).filter_by(subject_id=subject).with_for_update().first()
    if row is None:
        row = AiAccount(subject_id=subject, username=username or subject, balance=0, status="active", permanent=0)
        session.add(row)
        session.flush()
    elif username:
        row.username = username
    return row


def assert_active(row):
    if row.status == "suspended":
        raise HTTPException(403, "AI_SUBSYSTEM_SUSPENDED")


def has_unsettled_usage(session, payer_id):
    return session.query(AiRun.id).join(TaskJob, TaskJob.id == AiRun.task_id).filter(
        TaskJob.payer_id == payer_id, AiRun.status == "unsettled").first() is not None


def log(session, task_id, tag, content):
    session.add(AutoTaskRunLog(task_id=task_id, tag=tag, content=dumps(content)))


def submit(session, user, task_type, payload, operation_key, resource=None, psid=None, payer_id=None):
    subject = principal(user)
    owner = account(session, subject, user["username"])
    assert_active(owner)
    if resource is not None and resource.kind == "question":
        from .native_bank import refresh_reference
        refresh_reference(session, resource)
        payload = {**payload, "resource_version": resource.version}
    payload_hash = digest({"type": task_type, "payload": payload, "resource": resource.id if resource else None, "psid": psid})
    existing = session.query(TaskJob).filter_by(owner_id=subject, operation_key=operation_key).first()
    if existing:
        if existing.input_hash != payload_hash:
            raise HTTPException(409, "Idempotency key already used for different input")
        return existing
    settings = session.query(AiSetting).filter_by(id="runtime").first()
    queued_limit = int(json.loads(settings.data).get("max_queued_per_user", 1000)) if settings else 1000
    if session.query(TaskJob).filter(TaskJob.owner_id == subject, TaskJob.status.in_(["pending", "blocked"])).count() >= queued_limit:
        raise HTTPException(429, "AI queue quota exceeded")
    payer_id = payer_id or subject
    payer = account(session, payer_id)
    if task_type not in NON_AI and has_unsettled_usage(session, payer_id):
        raise HTTPException(409, "AI usage reconciliation required")
    if payer.balance < 0 and task_type not in NON_AI:
        raise HTTPException(402, "Insufficient AI points")
    job_id = uid()
    row = AutoTaskRun(id=job_id, task_type=task_type, psid=psid or (resource.psid if resource else None),
                      username=user["username"], status="pending")
    session.add(row)
    session.flush()
    job = TaskJob(id=job_id, owner_id=subject, owner_username=user["username"], payer_id=payer_id,
                  operation_key=operation_key, task_type=task_type, payload=dumps(payload), input_hash=payload_hash,
                  resource_id=resource.id if resource else None, course_id=resource.course_id if resource else None,
                  psid=row.psid, status="pending", attempt=0, max_attempts=3)
    session.add(job)
    session.flush()
    if resource:
        session.add(TaskInput(task_id=job_id, resource_version=resource.version, resource_kind=resource.kind,
                              content=resource.data, sha256=hashlib.sha256(resource.data.encode()).hexdigest()))
    session.info["ai_wake"] = True
    log(session, job_id, "payload", payload)
    return job


def runtime_lock(session):
    row = session.query(AiSetting).filter_by(id="runtime").with_for_update().one()
    return json.loads(row.data)


def claim(now=None):
    now = now or datetime.utcnow()
    with transaction() as s:
        settings = runtime_lock(s)
        expired = s.query(TaskJob).filter(TaskJob.status == "running", TaskJob.lease_until < now).with_for_update().all()
        for job in expired:
            s.query(AiRun).filter_by(task_id=job.id, status="running").update({"status": "unsettled"})
            job.status = "failed" if job.attempt >= job.max_attempts else "pending"
            job.lease_token = None
            job.error = "Worker lease expired"
            if job.status == "failed":
                job.finished_at = now
            s.query(AutoTaskRun).filter_by(id=job.id).update({"status": job.status, "end_time": legacy_time(job.finished_at)})
        s.flush()
        active = s.query(TaskJob).filter_by(status="running").all()
        if len(active) >= int(settings.get("max_concurrency", 4)):
            return None
        # Scan with a keyset cursor: a blocked first page must not starve later owners.
        def eligible_pages():
            cursor = None
            while True:
                query = s.query(TaskJob).filter(TaskJob.status.in_(["pending", "blocked"]), TaskJob.available_at <= now)
                if cursor:
                    query = query.filter(or_(TaskJob.created_at > cursor[0], and_(TaskJob.created_at == cursor[0], TaskJob.id > cursor[1])))
                rows = query.order_by(TaskJob.created_at, TaskJob.id).with_for_update(skip_locked=True).limit(100).all()
                if not rows:
                    return
                cursor = (rows[-1].created_at, rows[-1].id)
                yield from rows
                if len(rows) < 100:
                    return
        candidates = eligible_pages()
        for job in candidates:
            owner = account(s, job.owner_id)
            if owner.status == "suspended":
                cancel(s, job, "Subsystem suspended")
                continue
            if job.task_type not in NON_AI and has_unsettled_usage(s, job.payer_id):
                job.status = "blocked"
                job.error = "AI usage reconciliation required"
                job.available_at = now + timedelta(seconds=30)
                s.query(AutoTaskRun).filter_by(id=job.id).update({"status": "blocked"})
                continue
            if account(s, job.payer_id).balance < 0 and job.task_type not in NON_AI:
                job.status = "blocked"
                job.error = "Insufficient AI points"
                job.available_at = now + timedelta(seconds=30)
                s.query(AutoTaskRun).filter_by(id=job.id).update({"status": "blocked"})
                continue
            if any(a.resource_id and a.resource_id == job.resource_id for a in active):
                continue
            if sum(a.owner_id == job.owner_id for a in active) >= int(settings.get("per_user_concurrency", 1)):
                continue
            def lane(task):
                return "sandbox" if task.task_type in ("ide.execute", "plagiarism.scan") else "document" if task.task_type == "course.index" else "agent"
            if sum(lane(a) == lane(job) for a in active) >= int(settings.get(lane(job) + "_concurrency", 4)):
                continue
            saved = s.query(TaskInput).filter_by(task_id=job.id).first()
            try:
                saved_data = json.loads(saved.content) if saved else None
                if saved and (not isinstance(saved_data, dict) or hashlib.sha256(saved.content.encode()).hexdigest() != saved.sha256):
                    raise ValueError("Invalid snapshot")
            except (ValueError, TypeError):
                job.status, job.finished_at, job.error = "failed", now, "Task input snapshot is corrupt"
                s.query(AutoTaskRun).filter_by(id=job.id).update({"status": "failed", "end_time": legacy_time(now)})
                log(s, job.id, "error", {"message": job.error})
                continue
            job.status, job.lease_token = "running", uid()
            job.attempt += 1
            job.lease_until = now + timedelta(seconds=60)
            s.query(AutoTaskRun).filter_by(id=job.id).update({"status": "running", "start_time": legacy_time(now)})
            log(s, job.id, "log", {"event": "claimed", "attempt": job.attempt})
            s.flush()
            result = {c.name: getattr(job, c.name) for c in TaskJob.__table__.columns}
            if saved:
                result["input_snapshot"] = {"data": saved_data, "version": saved.resource_version, "kind": saved.resource_kind}
            return result
        return None


def fenced(session, job_id, token):
    job = session.query(TaskJob).filter_by(id=job_id).with_for_update().one_or_none()
    if not job or job.status != "running" or job.lease_token != token or job.lease_until <= datetime.utcnow():
        raise LeaseLost("Task lease is no longer valid")
    if account(session, job.owner_id).status == "suspended":
        raise LeaseLost("Subsystem suspended")
    return job


class LeaseLost(Exception):
    pass


def heartbeat(job_id, token):
    with transaction() as s:
        job = fenced(s, job_id, token)
        job.lease_until = datetime.utcnow() + timedelta(seconds=60)


def complete(job_id, token, result, apply=None):
    from sqlalchemy.exc import IntegrityError, OperationalError
    for attempt in range(3):
        try:
            return _complete_once(job_id, token, result, apply)
        except (IntegrityError, OperationalError):
            if attempt == 2:
                raise


def _complete_once(job_id, token, result, apply=None):
    with transaction() as s:
        job = fenced(s, job_id, token)
        if apply:
            result = apply(s, job, result)
        job.result, job.status, job.finished_at = dumps(result), "success", datetime.utcnow()
        job.lease_token = None
        s.query(AutoTaskRun).filter_by(id=job.id).update({"status": "success", "end_time": legacy_time(job.finished_at)})
        log(s, job.id, "result", result)


def fail(job_id, token, message, retry=True):
    with transaction() as s:
        job = fenced(s, job_id, token)
        s.query(AiRun).filter_by(task_id=job.id, status="running").update({"status": "unsettled"})
        job.error = message[:2000]
        job.status = "pending" if retry and job.attempt < job.max_attempts else "failed"
        job.available_at = datetime.utcnow() + timedelta(seconds=min(300, 2 ** job.attempt * 5))
        job.lease_token = None
        if job.status == "failed":
            job.finished_at = datetime.utcnow()
        s.query(AutoTaskRun).filter_by(id=job.id).update({"status": job.status, "end_time": legacy_time(job.finished_at)})
        log(s, job.id, "error", {"message": job.error, "attempt": job.attempt})


def cancel(session, job, reason):
    if job.status in TERMINAL:
        return
    job.status, job.lease_token, job.error = "cancelled", None, reason
    job.finished_at = datetime.utcnow()
    session.query(AiRun).filter_by(task_id=job.id, status="running").update({"status": "unsettled"})
    session.query(AutoTaskRun).filter_by(id=job.id).update({"status": "cancelled", "end_time": legacy_time(job.finished_at)})
    log(session, job.id, "warning", {"event": "cancelled", "reason": reason})


def ledger(session, subject, key, amount, actor, reason, task_id=None, cost=0):
    row = account(session, subject)
    previous = session.query(AiLedger).filter_by(billing_key=key).first()
    if previous:
        if previous.subject_id != subject or previous.amount != Decimal(str(amount)):
            raise HTTPException(409, "Billing operation conflict")
        return previous
    entry = AiLedger(subject_id=subject, billing_key=key, amount=Decimal(str(amount)),
                     cost_usd=Decimal(str(cost)), actor_id=actor, reason=reason, task_id=task_id)
    row.balance += entry.amount
    session.add(entry)
    return entry


def recalculate_access(session, subject, allow_restore=False):
    row = account(session, subject)
    since = datetime.utcnow() - timedelta(days=7)
    violation = session.query(AiReview).filter_by(subject_id=subject, decision="violation").first()
    manual_hold = session.query(AiReview).filter_by(subject_id=subject, decision="manual_hold").first()
    warnings = session.query(AiReview).filter(AiReview.subject_id == subject, AiReview.decision == "warning", AiReview.created_at >= since).count()
    if manual_hold or violation or warnings >= 3:
        row.status, row.permanent = "suspended", 1
        row.reason = manual_hold.reason if manual_hold else violation.reason if violation else "Three warnings within seven days"
        for job in session.query(TaskJob).filter(TaskJob.owner_id == subject, TaskJob.status.in_(["pending", "blocked", "running"])):
            cancel(session, job, row.reason)
    elif not row.permanent or allow_restore:
        row.status, row.permanent, row.reason = ("warning" if warnings else "active"), 0, None
    row.updated_at = datetime.utcnow()
    return row
