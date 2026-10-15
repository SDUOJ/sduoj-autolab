"""Autolab-native AI API. All browser routes retain the existing /api/ps boundary."""

import hashlib
import json
import mimetypes
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_

from auth import cover_header
from db import AutoTaskRunLog
from utils import api_response
from ser.auto_task_ai import PageRequest, PointAdjustment, ResourceCreate, ResourceUpdate, ReviewUpdate, TaskCreate
from auto_task.configuration import ProviderConfig, provider, public_settings, safe_path, save_provider
from auto_task.entities import AiAccount, AiFile, AiFileLink, AiLedger, AiResource, AiReview, AiRun, AiSetting, TaskJob, uid
from auto_task.handlers import KINDS
from auto_task.resources import course_access, get_resource, interview_expired, is_admin, principal, ps_access, require_admin, resource_view
from auto_task.store import account, assert_active, cancel, decode_log, digest, dumps, ledger, recalculate_access, submit, transaction

router = APIRouter(prefix="/auto-task/ai", tags=["auto-task-ai"])

EDITABLE = {
    "question": {"question_type", "language", "original", "zh", "options", "text", "file_ids", "tags", "answer", "explanation"},
    "collection": {"title", "text", "file_ids", "question_ids"},
    "authoring": {"title", "requirements", "language", "difficulty", "topics", "file_ids"},
    "grading": {"title", "question", "answer", "rubric", "file_ids"},
    "plagiarism": {"title", "language", "submissions", "base_code", "threshold", "ai_review_limit"},
    "ide": {"title", "question", "code", "language", "file_ids"},
    "course": {"title", "description", "textbook_version"},
    "document": {"title", "text", "file_ids"},
    "conversation": {"title", "question_id"},
    "interview": {"title", "student_id", "question", "code", "language", "report", "rubric", "max_questions", "duration_minutes"},
    "exam": {"title", "candidates", "rooms", "mode", "question_ids", "description", "randomize"},
}


def active_user(user=Depends(cover_header)):
    with transaction() as s:
        assert_active(account(s, principal(user), user["username"]))
    return user


def validate_data(session, user, kind, data, resource=None):
    if len(dumps(data)) > 2 * 1024 * 1024:
        raise HTTPException(413, "Resource input too large")
    if set(data) - EDITABLE[kind]:
        raise HTTPException(400, "Unsupported or server-owned resource fields")
    file_ids = data.get("file_ids", [])
    if not isinstance(file_ids, list) or len(file_ids) > 20 or not all(isinstance(value, str) for value in file_ids):
        raise HTTPException(400, "Expected at most 20 file IDs")
    for file_id in data.get("file_ids", []):
        item = session.query(AiFile).filter_by(id=file_id).first()
        if not item or (item.owner_id != principal(user) and (not resource or item.resource_id != resource.id)):
            raise HTTPException(403, "File permission denied")
    for resource_id in data.get("question_ids", []):
        linked = get_resource(session, user, resource_id)
        if linked.kind != "question":
            raise HTTPException(400, "Expected question reference")
    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list) or len(tags) > 6 or any(
                not isinstance(value, str) or not value.strip() or len(value.strip()) > 64
                for value in tags):
            raise HTTPException(400, "Expected at most 6 non-empty tags of 64 characters")
    if kind == "interview":
        if not str(data.get("student_id", "")).isdigit():
            raise HTTPException(400, "Interview must bind a stable SDUOJ student ID")
        if not isinstance(data.get("max_questions", 6), int) or not 1 <= data.get("max_questions", 6) <= 20:
            raise HTTPException(400, "Invalid interview length")
        if not isinstance(data.get("duration_minutes", 20), int) or not 1 <= data.get("duration_minutes", 20) <= 120:
            raise HTTPException(400, "Invalid interview duration")
        rubric = data.get("rubric", [])
        if not isinstance(rubric, list) or not rubric or any(not isinstance(item, dict) or not item.get("name") or not isinstance(item.get("max_score", item.get("maxScore")), (int, float)) or item.get("max_score", item.get("maxScore")) <= 0 for item in rubric):
            raise HTTPException(400, "Interview requires a named rubric with positive maximum scores")
        if len({item["name"] for item in rubric}) != len(rubric):
            raise HTTPException(400, "Duplicate rubric criterion")


@router.get("/capabilities")
@api_response()
def capabilities(user=Depends(active_user)):
    return {"version": 1, "task_types": list(KINDS), "resource_kinds": list(EDITABLE),
            "admin": is_admin(user), "sandbox_enabled": os.environ.get("AUTOLAB_SANDBOX_ENABLED") == "1"}


@router.get("/account")
@api_response()
def my_account(user=Depends(cover_header)):
    with transaction() as s:
        row = account(s, principal(user), user["username"])
        result = {"subject_id": row.subject_id, "status": row.status, "reason": row.reason, "permanent": bool(row.permanent)}
        if row.status != "suspended":
            result["balance"] = str(row.balance)
        return result


@router.post("/resources/create")
@api_response()
def create_resource(body: ResourceCreate, user=Depends(active_user)):
    with transaction() as s:
        if body.kind == "course" and not body.course_id:
            raise HTTPException(400, "Course knowledge bases must reference an existing Autolab course")
        if body.course_id:
            course_access(s, user, body.course_id, body.kind in ("course", "document", "exam", "interview", "grading", "authoring", "plagiarism"))
        if body.psid:
            ps = ps_access(s, user, body.psid, body.kind != "ide")
            if body.kind == "ide" and ps.type == 1:
                raise HTTPException(403, "Teaching IDE disabled for examinations")
        if body.kind in ("exam", "interview", "grading", "plagiarism") and not body.course_id and not body.psid:
            require_admin(user)
        if body.parent_id:
            parent = get_resource(s, user, body.parent_id)
            if parent.course_id != body.course_id:
                raise HTTPException(400, "Parent course scope mismatch")
        validate_data(s, user, body.kind, body.data)
        data = dict(body.data)
        canonical_key = None
        if body.kind == "document":
            files = s.query(AiFile).filter(AiFile.id.in_(data.get("file_ids", []))).all()
            if data.get("text") or files:
                canonical_key = digest({"document_scope": body.course_id or "owner:" + principal(user),
                                        "parent_id": body.parent_id, "text": data.get("text", ""),
                                        "files": sorted(file.sha256 for file in files)})
                existing = s.query(AiResource).filter_by(kind="document", canonical_key=canonical_key).first()
                if existing:
                    if existing.deleted:
                        raise HTTPException(409, "Matching document is deleted; restoration must be explicit")
                    get_resource(s, user, existing.id, True)
                    return {**resource_view(existing), "replay": True}
        if body.kind == "interview":
            data.update(status="pending", turns=[], events=[], expires_at=(datetime.utcnow() + timedelta(hours=72)).isoformat())
        row = AiResource(id=uid(), kind=body.kind, owner_id=principal(user), owner_username=user["username"],
                         course_id=body.course_id, psid=body.psid, parent_id=body.parent_id,
                         data=dumps(data), search_text=str(data.get("title", data.get("zh", ""))), canonical_key=canonical_key)
        s.add(row)
        s.flush()
        for file_id in set(data.get("file_ids", [])):
            item = s.query(AiFile).filter_by(id=file_id).one()
            if item.resource_id is None:
                item.resource_id = row.id
            s.add(AiFileLink(file_id=file_id, resource_id=row.id))
        if row.kind == "question":
            from auth import manager
            from auto_task.native_bank import save_batch
            manager(user)
            save_batch(s, user, "手工录入", row.id, [data], resources=[row])
        return resource_view(row)


@router.get("/resources/{resource_id}")
@api_response()
def resource_detail(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        if row.kind == "question":
            from auto_task.native_bank import refresh_reference
            refresh_reference(s, row)
        result = resource_view(row)
        if row.kind == "interview" and row.owner_id != principal(user):
            if str(result["data"].get("student_id")) == principal(user):
                result["data"].pop("candidate", None)
                result["data"].pop("events", None)
        return result


@router.get("/resources/{resource_id}/history")
@api_response()
def resource_history(resource_id: str, offset: int = 0, user=Depends(active_user)):
    from auto_task.entities import AiRevision
    with transaction() as s:
        row = get_resource(s, user, resource_id, include_deleted=True)
        # History may contain teacher-only scores, removed answers, or evidence.
        if row.owner_id != principal(user):
            get_resource(s, user, resource_id, write=True, include_deleted=True)
        query = s.query(AiRevision).filter_by(resource_id=resource_id)
        return {"totalNum": query.count(), "rows": [
            {"id": r.id, "version": r.version, "state": r.state, "deleted": bool(r.deleted),
             "data": json.loads(r.data), "sha256": r.sha256, "actor_id": r.actor_id,
             "task_id": r.task_id, "created_at": r.created_at.isoformat() + "Z"}
            for r in query.order_by(AiRevision.created_at.desc(), AiRevision.id.desc()).offset(max(0, offset)).limit(50)]}


@router.get("/resources/{resource_id}/chunks")
@api_response()
def resource_chunks(resource_id: str, pageNow: int = 1, pageSize: int = 20, user=Depends(active_user)):
    """Return the current index with stable locations for citation jump-back."""
    if pageNow < 1 or pageSize < 1 or pageSize > 100:
        raise HTTPException(400, "Invalid chunk page")
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        if row.kind not in ("document", "course"):
            raise HTTPException(400, "Only document resources have index chunks")
        indexed_version = json.loads(row.data).get("indexed_version")
        query = s.query(AiResource).filter_by(parent_id=row.id, kind="chunk", deleted=0)
        rows = []
        total = 0
        if indexed_version == row.version:
            expected = '"resource_version":{}'.format(row.version - 1)
            # Chunk payloads are canonical JSON (store.dumps), so these two
            # suffix checks avoid loading the entire index just to paginate.
            query = query.filter(or_(AiResource.data.contains(expected + ","),
                                     AiResource.data.contains(expected + "}")))
            total = query.count()
            start = (pageNow - 1) * pageSize
            rows = [item for item in query.order_by(AiResource.created_at, AiResource.id).offset(start).limit(pageSize).all()
                    if json.loads(item.data).get("resource_version") == row.version - 1]
        return {"pageIndex": pageNow, "pageSize": pageSize, "totalNum": total,
                "indexed_version": indexed_version,
                "rows": [json.loads(item.data) for item in rows]}


@router.get("/citations/resolve")
@api_response()
def resolve_citation(citation: str, user=Depends(active_user)):
    if len(citation) > 300 or not citation:
        raise HTTPException(400, "Invalid citation")
    with transaction() as s:
        marker = '"citation":' + dumps(citation)
        candidates = s.query(AiResource).filter(AiResource.kind == "chunk", AiResource.deleted == 0,
                                                AiResource.data.contains(marker)).limit(2).all()
        row = next((item for item in candidates if json.loads(item.data).get("citation") == citation), None)
        if not row:
            raise HTTPException(404, "Citation not found")
        parent = get_resource(s, user, row.parent_id)
        part = json.loads(row.data)
        parent_data = json.loads(parent.data)
        if (parent.kind != "document" or parent_data.get("indexed_version") != parent.version or
                part.get("resource_version") != parent.version - 1):
            raise HTTPException(409, "Citation version is no longer current")
        return part


@router.post("/resources/list")
@api_response()
def resource_list(body: PageRequest, user=Depends(active_user)):
    with transaction() as s:
        query = s.query(AiResource).filter(AiResource.deleted == 0, AiResource.kind.in_(list(EDITABLE)))
        if body.kind:
            query = query.filter_by(kind=body.kind)
        if body.course_id:
            course_access(s, user, body.course_id)
            query = query.filter_by(course_id=body.course_id)
        else:
            query = query.filter_by(owner_id=principal(user))
        if body.keyword:
            substring = AiResource.search_text.contains(body.keyword, autoescape=True)
            if s.bind.dialect.name == "mysql":
                from sqlalchemy.dialects.mysql import match
                relevance = match(AiResource.search_text, against=body.keyword).in_natural_language_mode()
                query = query.filter(or_(relevance, substring)).order_by(relevance.desc())
            else:
                query = query.filter(substring)
        rows = []
        from auto_task.resources import normalize_tags
        taxonomy = s.query(AiResource).filter_by(kind="tag", deleted=0).all() if body.tags else []
        requested_tags = set(normalize_tags(body.tags, taxonomy))
        for row in query.order_by(AiResource.created_at.desc()).all():
            try:
                get_resource(s, user, row.id)
                if requested_tags and not requested_tags.issubset(normalize_tags(json.loads(row.data).get("tags", []), taxonomy)):
                    continue
                rows.append(row)
            except HTTPException:
                continue
        start = (body.pageNow - 1) * body.pageSize
        return {"pageIndex": body.pageNow, "pageSize": body.pageSize, "totalNum": len(rows), "rows": [resource_view(r) for r in rows[start:start + body.pageSize]]}


@router.post("/resources/{resource_id}/update")
@api_response()
def update_resource(resource_id: str, body: ResourceUpdate, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind not in EDITABLE:
            raise HTTPException(400, "Internal resource cannot be edited through this endpoint")
        if row.kind == "interview":
            raise HTTPException(409, "Use the interview state machine")
        if row.version != body.version:
            raise HTTPException(409, "Resource version conflict")
        validate_data(s, user, row.kind, body.data, row)
        data = json.loads(row.data)
        if data.get("native_psid") and "mode" in body.data and body.data["mode"] != data.get("mode"):
            raise HTTPException(409, "Create a new formal exam instead of upgrading a published test")
        data.update(body.data)
        data.pop("candidate", None)
        if row.kind == "exam":
            data.pop("publication_plan", None)
            data.pop("publication_hash", None)
        if row.kind == "question" and data.get("native"):
            from auto_task.native_bank import update_reference
            data = update_reference(s, row, data)
        row.data, row.version, row.state = dumps(data), row.version + 1, "draft"
        row.search_text = "\n".join(str(data.get(k, "")) for k in ("title", "original", "zh", "text"))
        # A native import keeps a stable source identity even when teachers
        # annotate its local AI copy. Dropping the key here would allow a later
        # import of the same OJ question to create a second AI resource.
        if not (row.kind == "question" and isinstance(data.get("native"), dict)):
            row.canonical_key = None
        if "file_ids" in body.data:
            s.query(AiFileLink).filter_by(resource_id=row.id).delete()
            for file_id in set(data.get("file_ids", [])):
                s.add(AiFileLink(file_id=file_id, resource_id=row.id))
        return resource_view(row)


@router.post("/resources/{resource_id}/delete")
@api_response()
def delete_resource(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind not in EDITABLE:
            raise HTTPException(400, "Internal resource cannot be deleted through this endpoint")
        row.deleted, row.version = 1, row.version + 1
        for child in s.query(AiResource).filter_by(parent_id=row.id):
            child.deleted = 1
        for job in s.query(TaskJob).filter_by(resource_id=row.id):
            cancel(s, job, "Resource deleted")
        return {"deleted": True}


@router.post("/jobs/create")
@api_response()
def create_job(body: TaskCreate, user=Depends(active_user)):
    with transaction() as s:
        resource = get_resource(s, user, body.resource_id, True)
        existing = s.query(TaskJob).filter_by(owner_id=principal(user), operation_key=body.operation_key).first()
        if existing:
            previous = json.loads(existing.payload)
            previous.pop("resource_version", None)
            requested = dict(body.payload)
            requested.pop("resource_version", None)
            if existing.task_type != body.task_type or existing.resource_id != resource.id or previous != requested:
                raise HTTPException(409, "Idempotency key conflict")
            return {"taskId": existing.id, "status": existing.status, "operation_key": existing.operation_key}
        if resource.kind not in KINDS[body.task_type]:
            raise HTTPException(400, "Task/resource type mismatch")
        data = json.loads(resource.data)
        if body.task_type.startswith("ide."):
            from auto_task.run_context import authorize_teaching_ide
            from utilsTime import getNowTime
            authorize_teaching_ide(s, principal(user), getNowTime())
        if body.task_type.startswith("ide.") and resource.psid and ps_access(s, user, resource.psid).type == 1:
            raise HTTPException(403, "Teaching IDE disabled for examinations")
        if body.task_type == "interview.advance":
            if str(data.get("student_id")) == principal(user) and resource.owner_id != principal(user):
                raise HTTPException(403, "Submit an answer to advance the interview")
        if len(dumps(body.payload)) > 200000:
            raise HTTPException(413, "Task input too large")
        payload = {**body.payload, "resource_version": resource.version}
        row = submit(s, user, body.task_type, payload, body.operation_key, resource)
        s.flush()
        return {"taskId": row.id, "status": row.status, "operation_key": row.operation_key}


def authorized_job(s, user, task_id):
    from db import AutoTaskRun
    row = s.query(TaskJob).filter_by(id=task_id).first()
    legacy = s.query(AutoTaskRun).filter_by(id=task_id).first()
    if not row or not legacy or legacy.status == "deleted":
        raise HTTPException(404, "Task not found")
    if row.resource_id:
        resource = get_resource(s, user, row.resource_id)
        if resource.kind == "interview" and resource.owner_id != principal(user):
            data = json.loads(resource.data)
            if str(data.get("student_id")) == principal(user):
                # The interview session endpoint exposes status-only job data.
                # Generic job results and logs contain the teacher's candidate.
                raise HTTPException(403, "Interview task details are teacher-only")
        if (resource.kind == "collection" and row.owner_id != principal(user) and
                resource.owner_id != principal(user)):
            raise HTTPException(403, "Only the batch submitter or collection owner may view this task")
        if resource.kind == "question" and resource.parent_id and row.owner_id != principal(user):
            parent = s.query(AiResource).filter_by(id=resource.parent_id, kind="collection", deleted=0).first()
            if parent and parent.owner_id != principal(user):
                raise HTTPException(403, "Only the task submitter or collection owner may view this task")
    elif row.psid:
        ps_access(s, user, row.psid, True)
    elif row.owner_id != principal(user):
        raise HTTPException(403, "Task permission denied")
    return row


@router.get("/jobs/{task_id}")
@api_response()
def job_detail(task_id: str, after: int = 0, user=Depends(active_user)):
    with transaction() as s:
        row = authorized_job(s, user, task_id)
        logs = s.query(AutoTaskRunLog).filter(AutoTaskRunLog.task_id == task_id, AutoTaskRunLog.log_id > after).order_by(AutoTaskRunLog.log_id).limit(200).all()
        resource_state = s.query(AiResource.state).filter_by(id=row.resource_id, deleted=0).scalar() if row.resource_id else None
        return {"id": row.id, "task_type": row.task_type, "resource_id": row.resource_id, "status": row.status,
                "attempt": row.attempt, "max_attempts": row.max_attempts, "error": row.error,
                "created_at": row.created_at.isoformat() + "Z",
                "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
                "review_required": row.status == "success" and resource_state == "review",
                "result": json.loads(row.result) if row.result else None,
                "ai_runs": [{"id": run.id, "model": run.model, "usage": json.loads(run.usage),
                             "cost_usd": str(run.cost_usd), "status": run.status,
                             "created_at": run.created_at.isoformat() + "Z"}
                            for run in s.query(AiRun).filter_by(task_id=task_id).order_by(AiRun.created_at)],
                "events": [{"id": item.log_id, "tag": item.tag, "content": decode_log(item.content)} for item in logs],
                "next_cursor": logs[-1].log_id if logs else after}


@router.post("/jobs/list")
@api_response()
def job_list(body: PageRequest, user=Depends(active_user)):
    with transaction() as s:
        query = s.query(TaskJob).filter_by(owner_id=principal(user))
        if body.psid:
            ps_access(s, user, body.psid)
            query = query.filter_by(psid=body.psid)
        if body.status:
            query = query.filter_by(status=body.status)
        # B-01: hide jobs whose parent resource is soft-deleted so list/detail stay consistent
        query = query.outerjoin(AiResource, TaskJob.resource_id == AiResource.id).filter(
            or_(TaskJob.resource_id.is_(None), AiResource.deleted == 0)
        )
        total = query.count()
        rows = query.order_by(TaskJob.created_at.desc()).offset((body.pageNow - 1) * body.pageSize).limit(body.pageSize).all()
        resource_ids = {row.resource_id for row in rows if row.resource_id}
        states = dict(s.query(AiResource.id, AiResource.state).filter(AiResource.id.in_(resource_ids), AiResource.deleted == 0).all()) if resource_ids else {}
        return {"totalNum": total, "rows": [
            {"id": row.id, "task_type": row.task_type, "status": row.status,
             "resource_id": row.resource_id, "attempt": row.attempt, "max_attempts": row.max_attempts,
             "created_at": row.created_at.isoformat() + "Z",
             "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
             "review_required": row.status == "success" and states.get(row.resource_id) == "review"}
            for row in rows]}


@router.get("/jobs/{task_id}/events")
async def job_events(task_id: str, request: Request, after: int = 0, user=Depends(active_user)):
    with transaction() as s:
        authorized_job(s, user, task_id)
    async def stream():
        import asyncio
        cursor = max(0, after)
        for _ in range(450):
            if await request.is_disconnected():
                return
            try:
                with transaction() as s:
                    assert_active(account(s, principal(user)))
                    job = authorized_job(s, user, task_id)
                    records = s.query(AutoTaskRunLog).filter(AutoTaskRunLog.task_id == task_id, AutoTaskRunLog.log_id > cursor).order_by(AutoTaskRunLog.log_id).limit(200).all()
                    events = [{"id": r.log_id, "tag": r.tag, "content": decode_log(r.content)} for r in records]
                    terminal = job.status in ("success", "failed", "cancelled")
                for event in events:
                    cursor = event["id"]
                    yield "id: {}\ndata: {}\n\n".format(cursor, dumps(event))
                if terminal and len(events) < 200:
                    return
            except HTTPException:
                yield "event: revoked\ndata: {}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/admin/queue")
@api_response()
def queue_metrics(user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        counts = dict(s.query(TaskJob.status, func.count(TaskJob.id)).group_by(TaskJob.status).all())
        return {"counts": counts, "cost_usd": str(s.query(func.sum(AiRun.cost_usd)).scalar() or 0),
                "settings": public_settings(s.query(AiSetting).filter_by(id="runtime").one())}


@router.post("/jobs/{task_id}/cancel")
@api_response()
def cancel_job(task_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = authorized_job(s, user, task_id)
        if row.owner_id != principal(user):
            if row.psid:
                ps_access(s, user, row.psid, True)
            elif row.course_id:
                course_access(s, user, row.course_id, True)
            else:
                raise HTTPException(403, "Only the task owner may cancel")
        cancel(s, row, "Cancelled by " + principal(user))
        return {"status": row.status}


class RetryInput(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=128)


@router.post("/jobs/{task_id}/retry")
@api_response()
def retry_job(task_id: str, body: RetryInput, user=Depends(active_user)):
    with transaction() as s:
        row = authorized_job(s, user, task_id)
        if row.status not in ("failed", "cancelled") or not row.resource_id:
            raise HTTPException(409, "Only terminal resource tasks can be retried here")
        resource = get_resource(s, user, row.resource_id, True)
        payload = {**json.loads(row.payload), "resource_version": resource.version, "retry_of": row.id}
        previous = s.query(TaskJob).filter_by(owner_id=principal(user), operation_key=body.operation_key).first()
        if previous:
            if json.loads(previous.payload).get("retry_of") != task_id:
                raise HTTPException(409, "Retry operation conflict")
            return {"taskId": previous.id, "replay": True}
        if row.task_type.startswith("ide."):
            from auto_task.run_context import authorize_teaching_ide
            from utilsTime import getNowTime
            authorize_teaching_ide(s, principal(user), getNowTime())
        job = submit(s, user, row.task_type, payload, body.operation_key, resource)
        return {"taskId": job.id}


@router.post("/files/upload")
@api_response()
async def upload(file: UploadFile = File(...), resource_id: Optional[str] = Form(None), sequence: Optional[str] = Form(None), user=Depends(active_user)):
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".pptx", ".md", ".txt", ".csv", ".webm", ".mp4", ".wav", ".mp3"):
        raise HTTPException(400, "Unsupported file type")
    file_id = uid()
    relative = "uploads/" + file_id + suffix
    target = safe_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size, sha = 0, hashlib.sha256()
    try:
        with transaction() as s:
            if resource_id:
                parent = get_resource(s, user, resource_id)
                if parent.kind != "interview" or str(json.loads(parent.data).get("student_id")) != principal(user):
                    get_resource(s, user, resource_id, True)
            with target.open("xb") as stream:
                target.chmod(0o600)
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise HTTPException(413, "File exceeds 50 MiB")
                    sha.update(chunk)
                    stream.write(chunk)
            from auto_task.assets import validate_upload
            try:
                validate_upload(target)
            except (ValueError, OSError) as error:
                raise HTTPException(400, "Invalid or oversized document") from error
            sequence_key = digest({"resource": resource_id, "owner": principal(user), "sequence": sequence}) if sequence else None
            if sequence_key:
                previous = s.query(AiFile).filter_by(sequence_key=sequence_key).first()
                if previous:
                    if previous.sha256 != sha.hexdigest():
                        raise HTTPException(409, "Media sequence conflict")
                    target.unlink()
                    return {"id": previous.id, "sha256": previous.sha256, "replay": True}
            account(s, principal(user))
            settings_row = s.query(AiSetting).filter_by(id="runtime").one()
            quota = int(json.loads(settings_row.data).get("max_storage_mb", 512)) * 1024 * 1024
            used = s.query(func.sum(AiFile.size)).filter_by(owner_id=principal(user)).scalar() or 0
            if used + size > quota:
                raise HTTPException(413, "AI file storage quota exceeded")
            # File handling must not trust the browser-controlled multipart
            # Content-Type. In particular, question extraction decides whether
            # to pass a file as an image or parse it as a document from this
            # value. Derive the persisted type from the already allow-listed
            # extension so a spoofed PDF cannot enter the image path (and an
            # image uploaded as octet-stream cannot enter a document parser).
            media_type = mimetypes.guess_type("upload" + suffix)[0] or "application/octet-stream"
            if suffix == ".md":
                media_type = "text/markdown"
            row = AiFile(id=file_id, owner_id=principal(user), resource_id=resource_id, filename=Path(file.filename or "upload").name[:255],
                         media_type=media_type, path=relative, sha256=sha.hexdigest(), size=size, sequence_key=sequence_key)
            s.add(row)
            s.flush()
            if resource_id:
                s.add(AiFileLink(file_id=file_id, resource_id=resource_id))
            return {"id": file_id, "sha256": row.sha256, "size": size}
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/files/storage")
@api_response()
def storage_usage(user=Depends(active_user)):
    with transaction() as s:
        config = json.loads(s.query(AiSetting).filter_by(id="runtime").one().data)
        return {"used_bytes": int(s.query(func.sum(AiFile.size)).filter_by(owner_id=principal(user)).scalar() or 0),
                "quota_bytes": int(config.get("max_storage_mb", 512)) * 1024 * 1024}


@router.get("/files/{file_id}")
def download(file_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = s.query(AiFile).filter_by(id=file_id).first()
        if not row:
            raise HTTPException(404, "File not found")
        relations = s.query(AiFileLink).filter_by(file_id=file_id).all()
        if relations or row.owner_id != principal(user):
            accessible = False
            for relation in relations:
                try:
                    get_resource(s, user, relation.resource_id)
                    accessible = True
                    break
                except HTTPException:
                    continue
            if not accessible:
                raise HTTPException(403, "File permission denied")
        return FileResponse(safe_path(row.path), filename=row.filename, media_type=row.media_type,
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class SettingsUpdate(ProviderConfig):
    api_key: Optional[str] = Field(None, max_length=4000)
    expected_version: int = Field(..., ge=1)


@router.get("/settings")
@api_response()
def settings(user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        return public_settings(s.query(AiSetting).filter_by(id="runtime").one())


@router.post("/settings/update")
@api_response()
def update_settings(body: SettingsUpdate, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        config = ProviderConfig.parse_obj(body.dict(exclude={"api_key", "expected_version"}))
        return save_provider(s, config, body.api_key, body.expected_version)


@router.get("/settings/models")
@api_response()
def models(user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        config, key = provider(s)
    response = requests.get(config.base_url + "/models", headers={"Authorization": "Bearer " + key},
                            timeout=(5, 15), allow_redirects=False)
    if not response.ok:
        raise HTTPException(502, "Model list request failed")
    return {"models": [item["id"] for item in response.json().get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]}


@router.get("/settings/health")
@api_response()
def provider_health(user=Depends(active_user)):
    """Admin-only readiness probe; never returns the provider key or response body."""
    require_admin(user)
    started = time.monotonic()
    with transaction() as s:
        try:
            config, key = provider(s)
        except HTTPException as error:
            if error.status_code == 503:
                return {"configured": False, "ready": False, "reason": "provider_not_configured"}
            raise
    try:
        response = requests.get(config.base_url + "/models", headers={"Authorization": "Bearer " + key},
                                timeout=(5, 15), allow_redirects=False)
        value = response.json() if response.ok else None
        ready = bool(response.ok and isinstance(value, dict) and isinstance(value.get("data"), list))
        return {"configured": True, "ready": ready, "provider": config.provider, "model": config.model,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "status_code": response.status_code}
    except (requests.RequestException, ValueError):
        return {"configured": True, "ready": False, "provider": config.provider, "model": config.model,
                "latency_ms": round((time.monotonic() - started) * 1000, 1), "status_code": None}


@router.post("/points/adjust")
@api_response()
def adjust_points(body: PointAdjustment, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        ledger(s, body.subject_id, "adjust:" + body.operation_key, body.amount, principal(user), body.reason)
        return {"balance": str(account(s, body.subject_id).balance)}


@router.get("/points/ledger")
@api_response()
def point_history(offset: int = 0, user=Depends(active_user)):
    with transaction() as s:
        rows = s.query(AiLedger).filter_by(subject_id=principal(user)).order_by(AiLedger.created_at.desc()).offset(max(0, offset)).limit(100).all()
        return [{"id": row.id, "amount": str(row.amount), "cost_usd": str(row.cost_usd),
                 "reason": row.reason, "task_id": row.task_id,
                 "created_at": row.created_at.isoformat() + "Z"} for row in rows]


@router.get("/reviews")
@api_response()
def reviews(user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        rows = s.query(AiReview).order_by(AiReview.created_at.desc()).limit(200).all()
        subjects = {row.subject_id for row in rows}
        states = {row.subject_id: row for row in s.query(AiAccount).filter(AiAccount.subject_id.in_(subjects)).all()} if subjects else {}
        return [{"id": row.id, "subject_id": row.subject_id, "task_id": row.task_id,
                 "decision": row.decision, "reason": row.reason,
                 "reviewer_id": row.reviewer_id,
                 "created_at": row.created_at.isoformat() + "Z",
                 "reviewed_at": row.reviewed_at.isoformat() + "Z" if row.reviewed_at else None,
                 "account_status": states[row.subject_id].status if row.subject_id in states else "active",
                 "account_reason": states[row.subject_id].reason if row.subject_id in states else None}
                for row in rows]


class RunReconciliation(BaseModel):
    input_tokens: int = Field(..., ge=0, le=10**12)
    cached_input_tokens: int = Field(0, ge=0, le=10**12)
    output_tokens: int = Field(..., ge=0, le=10**12)
    reason: str = Field(..., min_length=1, max_length=2000)


@router.get("/admin/usage/unsettled")
@api_response()
def unsettled_usage(offset: int = 0, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        query = s.query(AiRun).filter_by(status="unsettled")
        rows = query.order_by(AiRun.created_at, AiRun.id).offset(max(0, offset)).limit(100).all()
        task_ids = {row.task_id for row in rows}
        payers = dict(s.query(TaskJob.id, TaskJob.payer_id).filter(TaskJob.id.in_(task_ids)).all()) if task_ids else {}
        return {"totalNum": query.count(), "rows": [
            {"id": row.id, "task_id": row.task_id, "model": row.model,
             "payer_id": payers.get(row.task_id), "usage": json.loads(row.usage),
             "created_at": row.created_at.isoformat() + "Z"}
            for row in rows]}


@router.post("/admin/usage/{run_id}/reconcile")
@api_response()
def reconcile_usage(run_id: str, body: RunReconciliation, user=Depends(active_user)):
    require_admin(user)
    from auto_task.accounting import settle_run
    return settle_run(run_id, body.dict(exclude={"reason"}), "unknown",
                      reconciliation={**body.dict(), "actor_id": principal(user)})


@router.post("/reviews/{review_id}/update")
@api_response()
def update_review(review_id: str, body: ReviewUpdate, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        row = s.query(AiReview).filter_by(id=review_id).with_for_update().one_or_none()
        if not row:
            raise HTTPException(404, "Review not found")
        if row.subject_id == principal(user):
            raise HTTPException(403, "Cannot review your own suspension")
        before = {"decision": row.decision, "reason": row.reason}
        row.decision, row.reason, row.reviewer_id, row.reviewed_at = body.decision, body.reason, principal(user), datetime.utcnow()
        s.flush()
        state = recalculate_access(s, row.subject_id, True)
        from auto_task.store import log
        if row.task_id:
            log(s, row.task_id, "log", {"event": "review_changed", "actor_id": principal(user), "before": before, "after": body.dict()})
        return {"status": state.status}


class InterviewInput(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=96)
    sequence: int = Field(0, ge=0)
    text: str = Field("", max_length=50000)
    evidence: dict = Field(default_factory=dict)


def interview_replay(session, resource, user, body):
    job = session.query(TaskJob).filter_by(owner_id=principal(user), operation_key="interview:" + body.operation_key).first()
    if job:
        if job.resource_id != resource.id or json.loads(job.payload).get("action_input_hash") != digest(body.dict()):
            raise HTTPException(409, "Interview operation conflict")
        return {"taskId": job.id, "status": job.status, "replay": True}


@router.post("/interviews/{resource_id}/preflight")
@api_response()
def interview_preflight(resource_id: str, body: InterviewInput, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        row = s.query(AiResource).filter_by(id=row.id).with_for_update().one()
        data = json.loads(row.data)
        if row.kind != "interview" or str(data.get("student_id")) != principal(user):
            raise HTTPException(403, "Student identity mismatch")
        replay = interview_replay(s, row, user, body)
        if replay:
            return replay
        if data.get("status") != "pending":
            raise HTTPException(409, "Interview already started")
        if interview_expired(data):
            raise HTTPException(410, "Interview expired")
        if not all(body.evidence.get(key) for key in ("https", "camera", "microphone", "screen", "single_display")):
            raise HTTPException(400, "Preflight incomplete")
        data.update(preflight=body.evidence, status="running", started_at=datetime.utcnow().isoformat())
        row.data, row.version = dumps(data), row.version + 1
        job = submit(s, user, "interview.advance", {"resource_version": row.version, "action_input_hash": digest(body.dict())}, "interview:" + body.operation_key, row, payer_id=row.owner_id)
        return {"taskId": job.id, "status": "running"}


@router.post("/interviews/{resource_id}/answer")
@api_response()
def interview_answer(resource_id: str, body: InterviewInput, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        row = s.query(AiResource).filter_by(id=row.id).with_for_update().one()
        data = json.loads(row.data)
        if row.kind != "interview" or str(data.get("student_id")) != principal(user):
            raise HTTPException(403, "Student identity mismatch")
        replay = interview_replay(s, row, user, body)
        if replay:
            return replay
        turns = data.get("turns", [])
        if data.get("status") != "awaiting_answer" or body.sequence != len(turns) or not turns or turns[-1].get("answer") is not None:
            raise HTTPException(409, "Interview turn conflict")
        if interview_expired(data):
            raise HTTPException(410, "Interview expired")
        turns[-1]["answer"] = body.text
        data["status"] = "running"
        row.data, row.version = dumps(data), row.version + 1
        job = submit(s, user, "interview.advance", {"resource_version": row.version, "action_input_hash": digest(body.dict())}, "interview:" + body.operation_key, row, payer_id=row.owner_id)
        return {"taskId": job.id}


@router.post("/interviews/{resource_id}/events")
@api_response()
def interview_event(resource_id: str, body: InterviewInput, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        row = s.query(AiResource).filter_by(id=row.id).with_for_update().one()
        data = json.loads(row.data)
        if row.kind != "interview" or str(data.get("student_id")) != principal(user):
            raise HTTPException(403, "Student identity mismatch")
        events = data.setdefault("events", [])
        if not any(e["id"] == body.operation_key for e in events):
            if len(events) >= 2000 or len(dumps(body.evidence)) > 8000:
                raise HTTPException(413, "Evidence limit exceeded")
            events.append({"id": body.operation_key, "evidence": body.evidence, "at": datetime.utcnow().isoformat()})
            row.data = dumps(data)
        return {"recorded": True}


@router.post("/interviews/{resource_id}/cancel")
@api_response()
def cancel_interview(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "interview":
            raise HTTPException(400, "Expected interview")
        data = json.loads(row.data)
        if data.get("status") == "completed":
            raise HTTPException(409, "Completed interviews cannot be cancelled")
        data["status"] = "cancelled"
        row.data, row.version, row.state = dumps(data), row.version + 1, "cancelled"
        for job in s.query(TaskJob).filter_by(resource_id=row.id):
            cancel(s, job, "Interview cancelled by teacher")
        return {"status": "cancelled"}
