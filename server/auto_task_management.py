"""Explicit teacher corrections and operational views for the shared AI UI."""

import json
import math
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auto_task.entities import AiAccount, AiResource, AiReview, TaskJob
from auto_task.legacy_bridge import answer_snapshot
from auto_task.assets import (DEFAULT_ORPHAN_GRACE_SECONDS,
                              MAX_ORPHAN_CLEANUP_BYTES,
                              MAX_ORPHAN_CLEANUP_FILES,
                              MAX_ORPHAN_GRACE_SECONDS,
                              MAX_ORPHAN_SCAN_RESULTS,
                              MIN_ORPHAN_GRACE_SECONDS,
                              cleanup_orphan_uploads, storage_consistency)
from auto_task.resources import get_resource, ps_access, principal, require_admin, resource_view
from auto_task.store import account, digest, dumps, log, recalculate_access, transaction
from server.auto_task_ai import active_user
from utils import api_response

router = APIRouter()


class AccountAccessChange(BaseModel):
    subject_id: str = Field(..., regex=r"^[1-9][0-9]*$", max_length=64)
    suspended: bool
    operation_key: str = Field(..., min_length=1, max_length=96)
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/admin/accounts/access")
@api_response()
def change_account_access(body: AccountAccessChange, user=Depends(active_user)):
    require_admin(user)
    actor = principal(user)
    if body.subject_id == actor:
        raise HTTPException(403, "Cannot change your own AI access")
    with transaction() as s:
        row = account(s, body.subject_id)
        key = digest({"account_access": body.operation_key, "actor_id": actor})
        previous = s.query(AiResource).filter_by(kind="access_audit", canonical_key=key).first()
        if previous:
            data = json.loads(previous.data)
            if data["request"] != body.dict():
                raise HTTPException(409, "Account access operation conflict")
            return {"status": row.status, "reason": row.reason, "replay": True}
        before = {"status": row.status, "reason": row.reason}
        holds = s.query(AiReview).filter_by(subject_id=body.subject_id, decision="manual_hold").all()
        if body.suspended:
            if not holds:
                s.add(AiReview(subject_id=body.subject_id, decision="manual_hold", reason=body.reason,
                               reviewer_id=actor, reviewed_at=datetime.utcnow()))
        else:
            for review in holds:
                review.decision = "manual_released"
                review.reviewer_id, review.reviewed_at = actor, datetime.utcnow()
        s.flush()
        recalculate_access(s, body.subject_id, allow_restore=True)
        s.add(AiResource(kind="access_audit", owner_id=actor, owner_username=user["username"], canonical_key=key,
                         data=dumps({"request": body.dict(), "before": before, "after": {"status": row.status, "reason": row.reason}})))
        return {"status": row.status, "reason": row.reason, "replay": False}


@router.get("/admin/accounts")
@api_response()
def list_accounts(page: int = 1, subject_id: str = "", user=Depends(active_user)):
    require_admin(user)
    if page < 1:
        raise HTTPException(400, "Invalid page")
    with transaction() as s:
        query = s.query(AiAccount)
        if subject_id:
            query = query.filter_by(subject_id=subject_id)
        return {"totalNum": query.count(), "rows": [{"subject_id": row.subject_id, "username": row.username,
            "balance": str(row.balance), "status": row.status, "reason": row.reason} for row in query.order_by(AiAccount.subject_id).offset((page - 1) * 20).limit(20)]}


@router.get("/admin/accounts/access-history")
@api_response()
def access_history(subject_id: str, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        rows = s.query(AiResource).filter_by(kind="access_audit").order_by(AiResource.created_at.desc()).all()
        return [{"id": row.id, "actor_id": row.owner_id, "created_at": row.created_at.isoformat() + "Z", **json.loads(row.data)}
                for row in rows if json.loads(row.data)["request"]["subject_id"] == subject_id][:100]


class CriterionEdit(BaseModel):
    name: str
    score: float = Field(..., ge=0)


class GradeEdit(BaseModel):
    version: int = Field(..., ge=1)
    criteria: List[CriterionEdit]
    comment: str = Field(..., max_length=10000)
    reason: str = Field(..., min_length=1, max_length=2000)


class OrphanCleanup(BaseModel):
    apply: bool = False
    reason: str = Field("", max_length=2000)
    minimum_age_seconds: int = Field(DEFAULT_ORPHAN_GRACE_SECONDS,
                                     ge=MIN_ORPHAN_GRACE_SECONDS,
                                     le=MAX_ORPHAN_GRACE_SECONDS)
    max_files: int = Field(100, ge=1, le=MAX_ORPHAN_CLEANUP_FILES)
    max_bytes: int = Field(100 * 1024 * 1024, ge=1, le=MAX_ORPHAN_CLEANUP_BYTES)
    result_limit: int = Field(100, ge=1, le=MAX_ORPHAN_SCAN_RESULTS)


@router.get("/admin/storage/orphans")
@api_response()
def audit_orphan_uploads(minimum_age_seconds: int = DEFAULT_ORPHAN_GRACE_SECONDS,
                         result_limit: int = 100, user=Depends(active_user)):
    require_admin(user)
    if not MIN_ORPHAN_GRACE_SECONDS <= minimum_age_seconds <= MAX_ORPHAN_GRACE_SECONDS:
        raise HTTPException(400, "Invalid orphan grace period")
    if not 1 <= result_limit <= MAX_ORPHAN_SCAN_RESULTS:
        raise HTTPException(400, "Invalid orphan result limit")
    with transaction() as s:
        report, _ = storage_consistency(s, minimum_age_seconds, result_limit)
        return report


@router.post("/admin/storage/orphans/cleanup")
@api_response()
def cleanup_orphans(body: OrphanCleanup, user=Depends(active_user)):
    require_admin(user)
    if body.apply and not body.reason.strip():
        raise HTTPException(400, "Cleanup reason is required when apply is true")
    with transaction() as s:
        return cleanup_orphan_uploads(s, actor_id=principal(user), **body.dict())


@router.post("/grading/{resource_id}/edit")
@api_response()
def edit_grade(resource_id: str, body: GradeEdit, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        data = json.loads(row.data)
        candidate = data.get("candidate") or {}
        source = candidate.get("source")
        job = s.query(TaskJob).filter_by(id=data.get("candidate_job_id"), resource_id=row.id, status="success").first()
        if row.kind != "grading" or row.version != body.version or row.state == "adopted" or not source or not job:
            raise HTTPException(409, "Expected current native grading candidate")
        ps_access(s, user, source["psid"], True)
        _, current = answer_snapshot(s, source["psid"], source["gid"], source["pid"], source["username"])
        if any(current[key] != source[key] for key in ("input_hash", "grade_hash")):
            raise HTTPException(409, "Native answer or grade changed; evaluate again")
        expected = [(item["name"], float(item["score"])) for item in source["rubric"]]
        if [item.name for item in body.criteria] != [name for name, _ in expected]:
            raise HTTPException(400, "Teacher correction must preserve the rubric")
        if any(not math.isfinite(item.score) or item.score > maximum for item, (_, maximum) in zip(body.criteria, expected)):
            raise HTTPException(400, "Score exceeds rubric maximum")
        before = candidate["judgeLog"]
        candidate["judgeLog"] = [{"name": item.name, "score": maximum, "jScore": item.score} for item, (_, maximum) in zip(body.criteria, expected)]
        candidate["judgeComment"] = body.comment
        data.setdefault("teacher_edits", []).append({"actor_id": principal(user), "reason": body.reason, "at": datetime.utcnow().isoformat() + "Z", "before": before})
        row.data, row.version, row.state = dumps(data), row.version + 1, "review"
        log(s, job.id, "log", {"event": "candidate_edited", "actor_id": principal(user), "reason": body.reason, "native_grade_changed": False})
        return {**resource_view(row), "total_score": sum(item.score for item in body.criteria), "native_grade_changed": False}
