"""Question intake, collection manifests, and reversible question workflows."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import cover_header
from utils import api_response
from auto_task.entities import AiFile, AiFileLink, AiResource, AiRevision
from auto_task.collection_access import acl_data, get_acl, require_permission, reserve_batch
from auto_task.question_workflows import detach_duplicate, detach_item, ensure_manifest, find_item, restore_duplicate, restore_item
from auto_task.resources import course_access, get_resource, principal, resource_view
from auto_task.store import digest, dumps, submit, transaction
from server.auto_task_ai import active_user, validate_data

router = APIRouter(prefix="/question-workflows", tags=["auto-task-questions"])

QUESTION_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx",
                            ".pptx", ".md", ".txt", ".csv"}


class IntakeRequest(BaseModel):
    collection_id: Optional[str] = None
    course_id: Optional[int] = Field(None, ge=1)
    title: str = Field("题目录入批次", min_length=1, max_length=160)
    text: str = Field("", max_length=1_500_000)
    file_ids: List[str] = Field(default_factory=list, max_items=20)
    batch_id: Optional[str] = Field(None, max_length=96)
    assignment_id: Optional[str] = Field(None, max_length=63)
    auto_solve: bool = False
    auto_verify: bool = False


class FollowupRequest(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=128)
    task_type: str = Field(..., pattern=r"^(question\.solve|question\.verify|question\.chat)$")
    message: Optional[str] = Field(None, max_length=20000)


class RevisionRestore(BaseModel):
    version: int = Field(..., ge=1)
    revision_id: Optional[str] = Field(None, max_length=63)


def _actor(user):
    return {"userId": user["userId"], "username": user["username"],
            "roles": user.get("roles", []), "groups": user.get("groups", [])}


def _collection_for_access(session, user, resource_id, required):
    row = get_resource(session, user, resource_id)
    if row.kind != "collection":
        raise HTTPException(400, "Expected collection resource")
    require_permission(session, row, principal(user), required)
    if required != "view":
        return session.query(AiResource).filter_by(id=row.id, deleted=0).with_for_update().one()
    return row


def _question_for_write(session, user, resource_id, include_deleted=False):
    row = get_resource(session, user, resource_id, True, include_deleted=include_deleted)
    if row.kind != "question":
        raise HTTPException(400, "Expected question resource")
    return row


def _materialize_original(session, collection, item):
    original = item.get("original")
    if not isinstance(original, dict):
        raise HTTPException(409, "Original extracted question is unavailable")
    question = json.loads(dumps(original))
    question.pop("_duplicate", None)
    batch = next((batch for batch in json.loads(collection.data).get("batches", [])
                  if batch.get("id") == item.get("batch_id")), {})
    question["file_ids"] = list(dict.fromkeys(question.get("file_ids", []) +
                                               batch.get("source", {}).get("file_ids", [])))
    question_id = digest({"collection": collection.id, "number": item["number"], "original": question})[:32]
    resource = session.query(AiResource).filter_by(id=question_id).first()
    if not resource:
        resource = AiResource(id=question_id, kind="question", owner_id=collection.owner_id,
                              owner_username=collection.owner_username, course_id=collection.course_id,
                              psid=collection.psid, data=dumps(question),
                              search_text=str(question.get("zh", question.get("original", ""))), state="ready")
        session.add(resource)
        session.flush()
        from auto_task.entities import AiFileLink
        file_ids = set(question.get("file_ids", []))
        for figure in question.get("figures", []):
            file_ids.update(figure[key] for key in ("crop_file_id", "source_file_id", "svg_file_id")
                            if figure.get(key))
        for file_id in file_ids:
            session.add(AiFileLink(file_id=file_id, resource_id=resource.id))
    elif resource.deleted:
        raise HTTPException(409, "Materialized question was deleted; restore it explicitly before relinking")
    return resource


def _question_file_ids(data):
    file_ids = set(data.get("file_ids", []))
    for figure in data.get("figures", []):
        if not isinstance(figure, dict):
            continue
        file_ids.update(figure[key] for key in ("crop_file_id", "source_file_id", "svg_file_id")
                        if figure.get(key))
    return file_ids


@router.post("/intake")
@api_response()
def intake(body: IntakeRequest, user=Depends(active_user)):
    """Create/reuse a collection and enqueue one idempotent extraction batch."""
    with transaction() as s:
        if not body.text.strip() and not body.file_ids:
            raise HTTPException(400, "Intake requires text or files")
        if body.collection_id:
            collection = _collection_for_access(s, user, body.collection_id, "contributor")
            if body.course_id and body.course_id != collection.course_id:
                raise HTTPException(400, "Intake course scope mismatch")
        else:
            if body.course_id:
                course_access(s, user, body.course_id, True)
            data = {"title": body.title, "text": "", "file_ids": [], "question_ids": [], "items": [], "batches": [], "next_question_number": 1}
            validate_data(s, user, "collection", {"title": body.title, "text": "", "file_ids": [], "question_ids": []})
            collection_id = digest({"owner": principal(user), "intake": body.batch_id})[:32] if body.batch_id else digest({"owner": principal(user), "nonce": datetime.utcnow().isoformat()})[:32]
            existing_collection = s.query(AiResource).filter_by(id=collection_id, kind="collection", deleted=0).first()
            collection = existing_collection or AiResource(id=collection_id,
                                    kind="collection", owner_id=principal(user), owner_username=user["username"],
                                    course_id=body.course_id, data=dumps(data), search_text=body.title, state="draft")
            s.add(collection)
            s.flush()
        source = {"text": body.text, "file_ids": list(dict.fromkeys(body.file_ids))}
        validate_data(s, user, "collection", {"text": body.text, "file_ids": source["file_ids"]}, collection)
        if source["file_ids"]:
            uploads = s.query(AiFile).filter(AiFile.id.in_(source["file_ids"])).all()
            invalid = [item.filename for item in uploads
                       if Path(item.filename).suffix.lower() not in QUESTION_SOURCE_SUFFIXES]
            if invalid:
                raise HTTPException(400, "Question intake only accepts images, PDF, DOCX, PPTX, Markdown, text, or CSV")
        collection_data = json.loads(collection.data)
        ensure_manifest(collection_data)
        if len(collection_data.get("items", [])) >= 1000:
            raise HTTPException(409, "Collection already contains the maximum 1000 question slots")
        batch_id = body.batch_id or digest({"collection": collection.id, "source": source, "pipeline": [body.auto_solve, body.auto_verify]})[:32]
        subject = principal(user)
        source_hash = digest({"source": source, "title": body.title, "course_id": body.course_id, "pipeline": [body.auto_solve, body.auto_verify]})
        _, replay = reserve_batch(s, collection, subject, batch_id, source_hash,
                                  body.assignment_id)
        payload = {"resource_version": collection.version, "source": source, "batch_id": batch_id,
                   "pipeline": {"auto_solve": body.auto_solve, "auto_verify": body.auto_verify},
                   "allow_execution": False, "collaboration_grant": {
                       "collection_id": collection.id, "subject_id": subject,
                       "assignment_id": body.assignment_id}}
        operation_key = "question-intake:{}:{}".format(collection.id, batch_id)
        from auto_task.entities import TaskJob
        job = s.query(TaskJob).filter_by(owner_id=principal(user), operation_key=operation_key).first() if replay else None
        if job is None:
            job = submit(s, _actor(user), "question.extract", payload, operation_key, collection)
        acl = get_acl(s, collection)
        collaboration = acl_data(acl)
        next(item for item in collaboration["batch_reservations"]
             if item.get("batch_id") == batch_id)["task_id"] = job.id
        acl.data = dumps(collaboration)
        return {"collection": resource_view(collection), "taskId": job.id, "batch_id": batch_id,
                "pipeline": payload["pipeline"], "replay": replay}


@router.get("/collections/{resource_id}/manifest")
@api_response()
def collection_manifest(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "view")
        data = json.loads(row.data)
        ensure_manifest(data)
        return {"resource_id": row.id, "version": row.version, "items": data.get("items", []),
                "batches": data.get("batches", []), "question_ids": data.get("question_ids", [])}


@router.get("/collections/{resource_id}/questions")
@api_response()
def collection_questions(resource_id: str, pageNow: int = 1, pageSize: int = 20,
                         user=Depends(active_user)):
    if pageNow < 1 or pageSize < 1 or pageSize > 100:
        raise HTTPException(400, "Invalid collection question page")
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "view")
        data = json.loads(row.data)
        ensure_manifest(data)
        active = [item for item in data.get("items", []) if item.get("status", "active") == "active"]
        start = (pageNow - 1) * pageSize
        rows, unavailable = [], []
        for item in active[start:start + pageSize]:
            try:
                question = get_resource(s, user, item["question_id"])
                if question.kind != "question":
                    raise HTTPException(404)
                rows.append({"number": item["number"], "resource": resource_view(question)})
            except HTTPException:
                unavailable.append(item["number"])
        return {"pageIndex": pageNow, "pageSize": pageSize, "totalNum": len(active),
                "rows": rows, "unavailable_numbers": unavailable}


@router.post("/collections/{resource_id}/items/{number}/detach")
@api_response()
def collection_detach(resource_id: str, number: int, user=Depends(active_user)):
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "editor")
        data = json.loads(row.data)
        item = detach_item(data, number)
        row.data, row.version, row.state = dumps(data), row.version + 1, "draft"
        return {"version": row.version, "item": item}


@router.post("/collections/{resource_id}/items/{number}/restore")
@api_response()
def collection_restore(resource_id: str, number: int, user=Depends(active_user)):
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "editor")
        data = json.loads(row.data)
        item = find_item(data, number)
        question = s.query(AiResource).filter_by(id=item.get("question_id"), kind="question", deleted=0).first()
        item = restore_item(data, number, _materialize_original(s, row, item).id if not question else None)
        row.data, row.version, row.state = dumps(data), row.version + 1, "draft"
        return {"version": row.version, "item": item}


@router.post("/collections/{resource_id}/items/{number}/detach-duplicate")
@api_response()
def collection_detach_duplicate(resource_id: str, number: int, user=Depends(active_user)):
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "editor")
        data = json.loads(row.data)
        item = find_item(data, number)
        existing = _materialize_original(s, row, item)
        item = detach_duplicate(data, number, existing.id)
        row.data, row.version, row.state = dumps(data), row.version + 1, "draft"
        return {"version": row.version, "item": item, "question": resource_view(existing)}


@router.post("/collections/{resource_id}/items/{number}/restore-duplicate")
@api_response()
def collection_restore_duplicate(resource_id: str, number: int, user=Depends(active_user)):
    with transaction() as s:
        row = _collection_for_access(s, user, resource_id, "editor")
        data = json.loads(row.data)
        target = find_item(data, number).get("duplicate_of")
        if not s.query(AiResource).filter_by(id=target, kind="question", deleted=0).first():
            raise HTTPException(409, "Original duplicate question is unavailable")
        item = restore_duplicate(data, number)
        row.data, row.version, row.state = dumps(data), row.version + 1, "draft"
        return {"version": row.version, "item": item}


@router.post("/questions/{resource_id}/followup")
@api_response()
def question_followup(resource_id: str, body: FollowupRequest, user=Depends(active_user)):
    with transaction() as s:
        row = _question_for_write(s, user, resource_id)
        source_collection = json.loads(row.data).get("source_collection") or row.parent_id
        payload = {"resource_version": row.version, "allow_execution": False,
                   "source_collection": source_collection}
        if row.owner_id != principal(user) and source_collection:
            collection = _collection_for_access(s, user, source_collection, "contributor")
            payload["collaboration_grant"] = {"collection_id": collection.id,
                                                "subject_id": principal(user),
                                                "assignment_id": None}
        if body.task_type == "question.chat":
            if not body.message or not body.message.strip():
                raise HTTPException(400, "Question follow-up message is required")
            payload["message"] = body.message.strip()
        job = submit(s, _actor(user), body.task_type, payload, body.operation_key, row)
        return {"taskId": job.id, "status": job.status, "resource_version": row.version}


@router.post("/questions/{resource_id}/restore")
@api_response()
def restore_question(resource_id: str, body: RevisionRestore, user=Depends(active_user)):
    with transaction() as s:
        row = _question_for_write(s, user, resource_id, include_deleted=True)
        query = s.query(AiRevision).filter_by(resource_id=row.id, version=body.version)
        if body.revision_id:
            query = query.filter_by(id=body.revision_id)
        revision = query.order_by(AiRevision.created_at.desc(), AiRevision.id.desc()).first()
        if not revision:
            raise HTTPException(404, "Question revision not found")
        restored_data = json.loads(revision.data)
        file_ids = _question_file_ids(restored_data)
        if file_ids:
            available = {item.id for item in s.query(AiFile).filter(AiFile.id.in_(file_ids)).all()}
            if available != file_ids:
                raise HTTPException(409, "Question revision files are unavailable")
        s.query(AiFileLink).filter_by(resource_id=row.id).delete(synchronize_session=False)
        for file_id in file_ids:
            s.add(AiFileLink(file_id=file_id, resource_id=row.id))
        current_data = json.loads(row.data)
        if current_data.get("native"):
            from auto_task.native_bank import update_reference
            restored_data["native"] = current_data["native"]
            restored_data = update_reference(s, row, restored_data)
        row.data, row.version, row.deleted, row.state = dumps(restored_data), row.version + 1, 0, "draft"
        row.search_text = "\n".join(str(restored_data.get(key, "")) for key in ("original", "zh", "tags"))
        return resource_view(row)
