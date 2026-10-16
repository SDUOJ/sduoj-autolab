"""Durable model-call intents and idempotent settlement using the saved tariff."""

import json
from decimal import Decimal
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func

from .entities import AiRun, AiSetting, TaskJob, uid
from .store import account, digest, dumps, fenced, has_unsettled_usage, ledger, log, transaction


def begin_run(job, config, prompt_bytes=0, require_balance=False):
    with transaction() as s:
        # Serialize reservations with claims/config changes across all workers.
        s.query(AiSetting).filter_by(id="runtime").with_for_update().one()
        fenced(s, job["id"], job["lease_token"])
        payer = account(s, job["payer_id"])
        if require_balance and payer.balance < 0:
            raise HTTPException(402, "Insufficient AI points")
        if has_unsettled_usage(s, job["payer_id"]):
            raise ValueError("AI usage reconciliation required")
        input_ceiling = max(Decimal(str(config.input_usd_per_million)),
                            Decimal(str(config.cached_usd_per_million)))
        reservation = (Decimal(prompt_bytes) * input_ceiling
                       + Decimal(config.max_output_tokens) * Decimal(str(config.output_usd_per_million))) / Decimal(1000000)
        month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        def total(query):
            value = Decimal(0)
            for previous in query.all():
                record = json.loads(previous.usage)
                value += Decimal(str(record.get("reserved_usd", 0))) if previous.status in ("running", "unsettled") else Decimal(previous.cost_usd)
            return value
        if total(s.query(AiRun).filter_by(task_id=job["id"])) + reservation > Decimal(str(config.max_task_cost_usd)):
            raise ValueError("Task cost reservation exceeds budget")
        scoped = s.query(AiRun).join(TaskJob, TaskJob.id == AiRun.task_id).filter(AiRun.created_at >= month)
        if config.monthly_user_budget_usd and total(scoped.filter(TaskJob.payer_id == job["payer_id"])) + reservation > Decimal(str(config.monthly_user_budget_usd)):
            raise ValueError("Monthly payer budget exhausted")
        if job.get("course_id") and config.monthly_course_budget_usd and total(scoped.filter(TaskJob.course_id == job["course_id"])) + reservation > Decimal(str(config.monthly_course_budget_usd)):
            raise ValueError("Monthly course budget exhausted")
        run = AiRun(id=uid(), task_id=job["id"], attempt=job["attempt"], model=config.model,
                    status="running", usage=dumps({"usage_complete": False, "provider": config.provider, "reserved_usd": str(reservation),
                        "pricing": {"input": config.input_usd_per_million, "cached": config.cached_usd_per_million,
                                    "output": config.output_usd_per_million, "unit": "USD per million tokens"}}))
        s.add(run)
        return run.id


def token_counts(usage):
    def count(primary, alternate):
        value = usage.get(primary, usage.get(alternate))
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10**12:
            raise ValueError("Missing or invalid provider token usage")
        return value
    incoming = count("input_tokens", "prompt_tokens")
    outgoing = count("output_tokens", "completion_tokens")
    cached = usage.get("cached_input_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    if isinstance(cached, bool) or not isinstance(cached, int) or not 0 <= cached <= incoming:
        raise ValueError("Invalid cached token usage")
    return incoming, cached, outgoing


def settle_run(run_id, usage, status, thread=None, reconciliation=None):
    with transaction() as s:
        initial = s.query(AiRun).filter_by(id=run_id).one_or_none()
        if not initial:
            raise HTTPException(404, "Model run not found")
        job = s.query(TaskJob).filter_by(id=initial.task_id).with_for_update().one()
        account(s, job.payer_id)
        run = s.query(AiRun).filter_by(id=run_id).populate_existing().with_for_update().one()
        record = json.loads(run.usage)
        if reconciliation:
            request_hash = digest(reconciliation)
            if run.status == "reconciled":
                if record.get("reconciliation_hash") != request_hash:
                    raise HTTPException(409, "Model run already reconciled with different input")
                return {"id": run.id, "status": run.status, "cost_usd": str(run.cost_usd), "replay": True}
            if run.status != "unsettled":
                raise HTTPException(409, "Only unsettled model runs can be reconciled")
        elif run.status not in ("running", "unsettled"):
            # A late worker response cannot overwrite a reviewed settlement or debit twice.
            log(s, job.id, "log", {"event": "late_usage", "run_id": run.id, "usage": usage})
            return {"id": run.id, "status": run.status, "cost_usd": str(run.cost_usd), "replay": True}
        try:
            incoming, cached, outgoing = token_counts(usage)
        except ValueError:
            if reconciliation:
                raise HTTPException(400, "Reconciliation requires complete token usage")
            run.status = "unsettled"
            run.usage = dumps({**record, "observed_usage": usage, "outcome": status, "usage_complete": False})
            return {"id": run.id, "status": run.status, "cost_usd": str(run.cost_usd), "replay": False}
        pricing = record["pricing"]
        cost = (Decimal(incoming - cached) * Decimal(str(pricing["input"]))
                + Decimal(cached) * Decimal(str(pricing["cached"]))
                + Decimal(outgoing) * Decimal(str(pricing["output"]))) / Decimal(1000000)
        record.update(usage, usage_complete=True, outcome=status)
        if reconciliation:
            record.update(reconciliation=reconciliation, reconciliation_hash=request_hash)
        run.usage, run.cost_usd, run.session_id = dumps(record), cost, thread or run.session_id
        run.status = "reconciled" if reconciliation else status
        if cost:
            ledger(s, job.payer_id, "run:" + run.id, -cost * 1000, job.owner_id,
                   "AI usage", job.id, cost)
        log(s, job.id, "log", {"event": "usage_reconciled" if reconciliation else "usage_settled",
                              "run_id": run.id, "cost_usd": str(cost)})
        return {"id": run.id, "status": run.status, "cost_usd": str(cost), "replay": False}
