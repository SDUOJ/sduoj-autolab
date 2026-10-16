"""Interview invitations, evidence manifests, recovery, and teacher adoption."""

import json
import math
from datetime import datetime, timedelta
from typing import List, Optional
from typing_extensions import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy import or_

from db import ProblemSetAnswerSheetDetail, ProblemSubjective
from utils import api_response
from auto_task.entities import AiFile, AiFileLink, AiResource, TaskJob, uid
from auto_task.legacy_bridge import answer_snapshot
from auto_task.resources import course_access, get_resource, interview_expired, principal, ps_access, require_admin, resource_view
from auto_task.store import cancel, digest, dumps, log, submit, transaction
from server.auto_task_ai import active_user
from sduojApi import getUserId

router = APIRouter()

TRACKS = {"audio", "video", "screen"}
TERMINAL = {"completed", "cancelled", "expired", "adopted", "rejected"}


class RubricItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    max_score: float = Field(..., gt=0, le=10000)

    @validator("max_score")
    def finite_maximum(cls, value):
        if not math.isfinite(value):
            raise ValueError("Rubric maximum must be finite")
        return value


class NativeSource(BaseModel):
    gid: int
    pid: int
    student_username: str = Field(..., min_length=1, max_length=63)


class InvitationCreate(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=96)
    student_id: str = Field(..., pattern=r"^[1-9][0-9]*$")
    student_username: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    question: str = Field("", max_length=200000)
    code: str = Field("", max_length=500000)
    language: str = Field("", max_length=40)
    report: str = Field("", max_length=500000)
    rubric: List[RubricItem] = Field(default_factory=list, max_items=30)
    max_questions: int = Field(6, ge=1, le=20)
    duration_minutes: int = Field(20, ge=1, le=120)
    expires_in_hours: int = Field(72, ge=1, le=24 * 30)
    required_tracks: List[Literal["audio", "video", "screen"]] = Field(default_factory=lambda: ["video"], min_items=1, max_items=3)
    course_id: Optional[int] = None
    psid: Optional[int] = None
    native_source: Optional[NativeSource] = None

    @validator("required_tracks")
    def distinct_tracks(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate required media track")
        return value


class InterviewList(BaseModel):
    pageNow: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    scope: Literal["all", "assigned", "managed"] = "all"
    status: Optional[str] = Field(None, max_length=24)
    course_id: Optional[int] = None


class MediaAttach(BaseModel):
    file_id: str = Field(..., min_length=1, max_length=63)
    turn_sequence: int = Field(..., ge=0, le=20)
    track: Literal["audio", "video", "screen"]
    segment_index: int = Field(..., ge=0, le=999)
    segment_count: int = Field(..., ge=1, le=1000)
    started_ms: int = Field(0, ge=0)
    duration_ms: int = Field(..., gt=0, le=60 * 60 * 1000)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class InterviewResponse(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=96)
    sequence: int = Field(..., ge=0, le=20)
    text: str = Field(..., min_length=1, max_length=50000)
    file_ids: List[str] = Field(..., min_items=1, max_items=3000)
    version: Optional[int] = Field(None, ge=1)


class RetryTask(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=96)


class RenewInvitation(BaseModel):
    operation_key: str = Field(..., min_length=1, max_length=96)
    version: int = Field(..., ge=1)
    expires_in_hours: int = Field(72, ge=1, le=24 * 30)


class CriterionScore(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    score: float = Field(..., ge=0, le=10000)

    @validator("score")
    def finite_score(cls, value):
        if not math.isfinite(value):
            raise ValueError("Criterion score must be finite")
        return value


class TeacherReview(BaseModel):
    version: int = Field(..., ge=1)
    decision: Literal["approve", "reject"]
    notes: str = Field(..., min_length=1, max_length=4000)
    criteria: List[CriterionScore] = Field(default_factory=list, max_items=30)


class AdoptGrade(BaseModel):
    version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=2000)
    overwrite_manual: bool = False


def _teacher_scope(session, user, course_id, psid):
    if course_id and psid:
        raise HTTPException(400, "Choose a course or problem set scope, not both")
    if course_id:
        course_access(session, user, course_id, True)
    elif psid:
        ps_access(session, user, psid, True)
    else:
        require_admin(user)


def _lock_interview(session, user, resource_id, teacher=False):
    accessible = get_resource(session, user, resource_id, teacher)
    if accessible.kind != "interview":
        raise HTTPException(400, "Expected interview")
    return session.query(AiResource).filter_by(id=accessible.id, deleted=0).with_for_update().one()


def _student_data(row, data):
    value = dict(data)
    for key in ("candidate", "events", "teacher_review", "native_source", "adoption"):
        value.pop(key, None)
    value["has_teacher_review"] = bool(data.get("teacher_review"))
    value["native_source_bound"] = bool(data.get("native_source"))
    return value


def _effective_status(data):
    if data.get("status") not in TERMINAL and interview_expired(data):
        return "expired"
    return data.get("status", "pending")


def _resource_result(row, user):
    result = resource_view(row)
    data = json.loads(row.data)
    result["effective_status"] = _effective_status(data)
    result["role"] = "student" if str(data.get("student_id")) == principal(user) and row.owner_id != principal(user) else "teacher"
    if result["role"] == "student":
        result["data"] = _student_data(row, data)
    return result


def _native_rubric(source):
    values = []
    for item in source.get("rubric", []):
        try:
            maximum = float(item.get("score"))
        except (TypeError, ValueError):
            raise HTTPException(409, "Native rubric is not compatible with interview grading")
        if not item.get("name") or not math.isfinite(maximum) or maximum <= 0:
            raise HTTPException(409, "Native rubric is not compatible with interview grading")
        values.append({"name": str(item["name"]), "max_score": maximum})
    if not values:
        raise HTTPException(409, "Native subjective problem has no grading rubric")
    return values


def _manifest(data, turn_sequence=None):
    grouped = {}
    for segment in data.get("media_segments", []):
        sequence = int(segment["turn_sequence"])
        if turn_sequence is not None and sequence != turn_sequence:
            continue
        key = (sequence, segment["track"])
        grouped.setdefault(key, []).append(segment)
    sequences = [turn_sequence] if turn_sequence is not None else list(range(len(data.get("turns", []))))
    groups = []
    for sequence in sequences:
        tracks = set(data.get("media_policy", {}).get("required_tracks", []))
        tracks.update(key[1] for key in grouped if key[0] == sequence)
        for track in sorted(tracks):
            segments = sorted(grouped.get((sequence, track), []), key=lambda item: item["segment_index"])
            expected = segments[0]["segment_count"] if segments else None
            present = [item["segment_index"] for item in segments]
            missing = [index for index in range(expected or 0) if index not in present]
            groups.append({
                "turn_sequence": sequence,
                "track": track,
                "required": track in data.get("media_policy", {}).get("required_tracks", []),
                "expected_segments": expected,
                "present_segments": present,
                "missing_segments": missing,
                "complete": bool(segments) and not missing and len(present) == expected,
                "segments": [{**item, "url": "/api/ps/auto-task/ai/files/" + item["file_id"]} for item in segments],
            })
    return {"groups": groups, "complete": all(item["complete"] for item in groups if item["required"])}


def _verified_candidate_job(session, row, candidate):
    jobs = session.query(TaskJob).filter_by(resource_id=row.id, task_type="interview.advance", status="success").order_by(TaskJob.finished_at.desc(), TaskJob.created_at.desc()).all()
    for job in jobs:
        if job.result and digest(json.loads(job.result)) == digest(candidate):
            return job
    raise HTTPException(409, "Interview candidate has no matching successful task")


def _review_criteria(data, supplied):
    rubric = data.get("rubric", [])
    candidate = data.get("candidate") or {}
    values = [item.dict() for item in supplied] if supplied else [
        {"name": item.get("name"), "score": item.get("score")} for item in candidate.get("criteria", [])
    ]
    expected = [(item["name"], float(item.get("max_score", item.get("maxScore", 0)))) for item in rubric]
    if [item.get("name") for item in values] != [item[0] for item in expected]:
        raise HTTPException(400, "Review criteria must match the invitation rubric in order")
    result = []
    for value, (name, maximum) in zip(values, expected):
        try:
            score = float(value["score"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Invalid criterion score")
        if not math.isfinite(score) or score < 0 or score > maximum:
            raise HTTPException(400, "Criterion score exceeds its rubric maximum")
        result.append({"name": name, "score": score, "max_score": maximum})
    if not result:
        raise HTTPException(409, "Completed interview has no scored criteria")
    return result


@router.post("/interviews/invite")
@api_response()
async def invite(body: InvitationCreate, user=Depends(active_user)):
    if body.native_source:
        if body.native_source.student_username != body.student_username:
            raise HTTPException(409, "Native answer username must match the invited student")
        try:
            native_user_id = await getUserId(body.student_username)
        except Exception as error:
            raise HTTPException(502, "Native student identity is unavailable") from error
        if native_user_id is None:
            raise HTTPException(404, "Invited native student does not exist")
        if str(native_user_id) != body.student_id:
            raise HTTPException(409, "Native student ID and username do not match")
    with transaction() as session:
        _teacher_scope(session, user, body.course_id, body.psid)
        canonical_key = digest({"kind": "interview-invite", "owner": principal(user), "operation": body.operation_key})
        previous = session.query(AiResource).filter_by(canonical_key=canonical_key).first()
        if previous:
            return {**_resource_result(previous, user), "replay": True}

        rubric = [item.dict() for item in body.rubric]
        native_source = None
        report = body.report
        question = body.question
        if body.native_source:
            if not body.psid:
                raise HTTPException(400, "Native source requires a problem set scope")
            try:
                _, source = answer_snapshot(session, body.psid, body.native_source.gid, body.native_source.pid, body.native_source.student_username)
            except ValueError as error:
                raise HTTPException(404, str(error))
            native = _native_rubric(source)
            if rubric and [(item["name"], item["max_score"]) for item in rubric] != [(item["name"], item["max_score"]) for item in native]:
                raise HTTPException(409, "Invitation rubric must match the native subjective rubric")
            rubric = native
            question = question or source["question"]
            report = report or dumps(source["answer"])
            native_source = {key: source[key] for key in ("asd_id", "psid", "gid", "pid", "username", "input_hash", "grade_hash")}
        if not rubric:
            raise HTTPException(400, "Interview requires at least one rubric criterion")
        if len({item["name"] for item in rubric}) != len(rubric):
            raise HTTPException(400, "Duplicate rubric criterion")

        now = datetime.utcnow()
        data = {
            "title": body.title,
            "student_id": body.student_id,
            "student_username": body.student_username,
            "question": question,
            "code": body.code,
            "language": body.language,
            "report": report,
            "rubric": rubric,
            "max_questions": body.max_questions,
            "duration_minutes": body.duration_minutes,
            "status": "pending",
            "turns": [],
            "events": [],
            "media_segments": [],
            "media_policy": {"required_tracks": body.required_tracks, "max_segment_seconds": 60},
            "evidence_policy": {
                "visibility": "assigned_student_and_authorized_teachers",
                "purpose": "assessment_review",
                "retention": "platform_policy",
            },
            "invited_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=body.expires_in_hours)).isoformat(),
            "native_source": native_source,
        }
        row = AiResource(id=uid(), kind="interview", owner_id=principal(user), owner_username=user["username"],
                         course_id=body.course_id, psid=body.psid, state="invited", data=dumps(data),
                         search_text="\n".join((body.title, body.student_username, body.student_id)), canonical_key=canonical_key)
        session.add(row)
        session.flush()
        return _resource_result(row, user)


@router.post("/interviews/list")
@api_response()
def list_interviews(body: InterviewList, user=Depends(active_user)):
    with transaction() as session:
        subject = principal(user)
        if body.course_id:
            course_access(session, user, body.course_id)
        assigned = AiResource.data.contains('"student_id":"' + subject + '"')
        query = session.query(AiResource).filter_by(kind="interview", deleted=0)
        if body.course_id:
            query = query.filter_by(course_id=body.course_id)
        if body.scope == "assigned":
            query = query.filter(assigned)
        elif body.scope == "managed":
            query = query.filter_by(owner_id=subject)
        else:
            query = query.filter(or_(AiResource.owner_id == subject, assigned))
        latest_jobs = {}
        for job in session.query(TaskJob).filter(TaskJob.resource_id.in_(query.with_entities(AiResource.id)), TaskJob.task_type == "interview.advance").order_by(TaskJob.created_at.desc(), TaskJob.id.desc()):
            latest_jobs.setdefault(job.resource_id, job)
        rows = []
        for row in query.order_by(AiResource.created_at.desc(), AiResource.id.desc()).all():
            try:
                get_resource(session, user, row.id)
            except HTTPException:
                continue
            result = _resource_result(row, user)
            latest = latest_jobs.get(row.id)
            if result["effective_status"] == "running" and latest and latest.status == "failed":
                result["effective_status"] = "failed"
            visible_status = row.state if row.state in ("adopted", "rejected", "approved", "failed") else result["effective_status"]
            if body.status and visible_status != body.status:
                continue
            rows.append(result)
        total = len(rows)
        start = (body.pageNow - 1) * body.pageSize
        return {"pageIndex": body.pageNow, "pageSize": body.pageSize, "totalNum": total, "rows": rows[start:start + body.pageSize]}


@router.get("/interviews/{resource_id}/session")
@api_response()
def interview_session(resource_id: str, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id)
        data = json.loads(row.data)
        status = _effective_status(data)
        if status == "expired" and data.get("status") != "expired":
            data["status"] = "expired"
            data["expired_at"] = datetime.utcnow().isoformat()
            row.data, row.version, row.state = dumps(data), row.version + 1, "expired"
            for job in session.query(TaskJob).filter_by(resource_id=row.id).filter(TaskJob.status.in_(["pending", "blocked", "running"])):
                cancel(session, job, "Interview expired")
        latest = session.query(TaskJob).filter_by(resource_id=row.id, task_type="interview.advance").order_by(TaskJob.created_at.desc(), TaskJob.id.desc()).first()
        result = _resource_result(row, user)
        result["job"] = ({"id": latest.id, "status": latest.status, "error": latest.error, "attempt": latest.attempt}
                         if latest else None)
        result["media_manifest"] = _manifest(data)
        return result


@router.post("/interviews/{resource_id}/media/attach")
@api_response()
def attach_media(resource_id: str, body: MediaAttach, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id)
        data = json.loads(row.data)
        if str(data.get("student_id")) != principal(user):
            raise HTTPException(403, "Only the assigned student may attach interview media")
        if _effective_status(data) == "expired":
            raise HTTPException(410, "Interview expired")
        turns = data.get("turns", [])
        if data.get("status") != "awaiting_answer" or body.turn_sequence >= len(turns) or turns[body.turn_sequence].get("answer") is not None:
            raise HTTPException(409, "Media does not belong to the active interview turn")
        file_row = session.query(AiFile).filter_by(id=body.file_id).one_or_none()
        linked = session.query(AiFileLink).filter_by(file_id=body.file_id, resource_id=row.id).first()
        if not file_row or file_row.owner_id != principal(user) or not linked:
            raise HTTPException(403, "Media file is not owned by this student and interview")
        if file_row.sha256 != body.sha256:
            raise HTTPException(409, "Media hash mismatch")
        if body.track == "audio" and not (file_row.media_type.startswith("audio/") or file_row.media_type.startswith("video/")):
            raise HTTPException(400, "Audio evidence requires an audio or video file")
        if body.track in ("video", "screen") and not file_row.media_type.startswith("video/"):
            raise HTTPException(400, "Video evidence requires a video file")
        maximum_duration = int(data.get("media_policy", {}).get("max_segment_seconds", 60)) * 1000
        if body.duration_ms > maximum_duration:
            raise HTTPException(400, "Media segment exceeds the interview duration policy")
        segments = data.setdefault("media_segments", [])
        existing = next((item for item in segments if item["turn_sequence"] == body.turn_sequence and item["track"] == body.track and item["segment_index"] == body.segment_index), None)
        value = {**body.dict(), "attached_at": datetime.utcnow().isoformat()}
        if existing:
            comparable = {key: existing[key] for key in body.dict()}
            if comparable != body.dict():
                raise HTTPException(409, "Media segment position is already occupied")
            return {"attached": True, "replay": True, "manifest": _manifest(data, body.turn_sequence)}
        if any(item["file_id"] == body.file_id for item in segments):
            raise HTTPException(409, "Media file is already attached to another segment")
        group = [item for item in segments if item["turn_sequence"] == body.turn_sequence and item["track"] == body.track]
        if group and any(item["segment_count"] != body.segment_count for item in group):
            raise HTTPException(409, "Media segment count conflicts with the existing manifest")
        segments.append(value)
        row.data, row.version = dumps(data), row.version + 1
        return {"attached": True, "version": row.version, "manifest": _manifest(data, body.turn_sequence)}


@router.get("/interviews/{resource_id}/media/manifest")
@api_response()
def media_manifest(resource_id: str, turn_sequence: Optional[int] = None, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id)
        return _manifest(json.loads(row.data), turn_sequence)


@router.post("/interviews/{resource_id}/responses")
@api_response()
def answer_with_media(resource_id: str, body: InterviewResponse, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id)
        data = json.loads(row.data)
        if str(data.get("student_id")) != principal(user):
            raise HTTPException(403, "Student identity mismatch")
        existing = session.query(TaskJob).filter_by(owner_id=principal(user), operation_key="interview:" + body.operation_key).first()
        action_hash = digest(body.dict())
        if existing:
            if existing.resource_id != row.id or json.loads(existing.payload).get("action_input_hash") != action_hash:
                raise HTTPException(409, "Interview operation conflict")
            return {"taskId": existing.id, "status": existing.status, "replay": True}
        if body.version is not None and row.version != body.version:
            raise HTTPException(409, "Interview version conflict")
        if _effective_status(data) == "expired":
            raise HTTPException(410, "Interview expired")
        turns = data.get("turns", [])
        if data.get("status") != "awaiting_answer" or body.sequence >= len(turns) or body.sequence != len(turns) - 1 or turns[body.sequence].get("answer") is not None:
            raise HTTPException(409, "Interview turn conflict")
        manifest = _manifest(data, body.sequence)
        if not manifest["complete"]:
            raise HTTPException(409, "Required interview media has missing segments")
        attached = sorted({item["file_id"] for group in manifest["groups"] for item in group["segments"]})
        if sorted(set(body.file_ids)) != attached or len(set(body.file_ids)) != len(body.file_ids):
            raise HTTPException(409, "Answer file list must match the turn media manifest")
        turns[body.sequence]["answer"] = body.text
        turns[body.sequence]["answer_evidence"] = {
            "submitted_at": datetime.utcnow().isoformat(),
            "text_sha256": digest(body.text),
            "files": [{"id": item["file_id"], "sha256": item["sha256"], "track": item["track"], "segment_index": item["segment_index"]}
                      for group in manifest["groups"] for item in group["segments"]],
        }
        data["status"] = "running"
        row.data, row.version = dumps(data), row.version + 1
        job = submit(session, user, "interview.advance", {"resource_version": row.version, "action_input_hash": action_hash},
                     "interview:" + body.operation_key, row, payer_id=row.owner_id)
        return {"taskId": job.id, "status": job.status, "version": row.version}


@router.post("/interviews/{resource_id}/jobs/retry")
@api_response()
def retry_interview_job(resource_id: str, body: RetryTask, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id)
        data = json.loads(row.data)
        if _effective_status(data) == "expired" or data.get("status") in TERMINAL:
            raise HTTPException(409, "Terminal interview tasks cannot be retried")
        failed = session.query(TaskJob).filter_by(resource_id=row.id, task_type="interview.advance").filter(TaskJob.status.in_(["failed", "cancelled"])).order_by(TaskJob.created_at.desc(), TaskJob.id.desc()).first()
        if not failed:
            raise HTTPException(409, "Interview has no failed task to retry")
        payload = {"resource_version": row.version, "retry_of": failed.id}
        job = submit(session, user, "interview.advance", payload, "interview-retry:" + body.operation_key, row, payer_id=row.owner_id)
        return {"taskId": job.id, "status": job.status, "replay": job.id != failed.id}


@router.post("/interviews/{resource_id}/renew")
@api_response()
def renew_interview(resource_id: str, body: RenewInvitation, user=Depends(active_user)):
    with transaction() as session:
        source = _lock_interview(session, user, resource_id, True)
        data = json.loads(source.data)
        if source.version != body.version:
            raise HTTPException(409, "Interview version conflict")
        if _effective_status(data) not in TERMINAL and source.state not in ("failed", "rejected"):
            raise HTTPException(409, "Only terminal interviews may be renewed")
        canonical_key = digest({"kind": "interview-renew", "owner": principal(user), "operation": body.operation_key})
        previous = session.query(AiResource).filter_by(canonical_key=canonical_key).first()
        if previous:
            if json.loads(previous.data).get("renewed_from") != source.id:
                raise HTTPException(409, "Renew operation conflict")
            return {**_resource_result(previous, user), "replay": True}
        now = datetime.utcnow()
        for key in ("preflight", "candidate", "teacher_review", "adoption", "started_at", "completed_at", "expired_at"):
            data.pop(key, None)
        data.update(status="pending", turns=[], events=[], media_segments=[], invited_at=now.isoformat(),
                    expires_at=(now + timedelta(hours=body.expires_in_hours)).isoformat(), renewed_from=source.id)
        row = AiResource(id=uid(), kind="interview", owner_id=source.owner_id, owner_username=source.owner_username,
                         course_id=source.course_id, psid=source.psid, state="invited", data=dumps(data),
                         search_text=source.search_text, canonical_key=canonical_key)
        session.add(row)
        session.flush()
        return _resource_result(row, user)


@router.post("/interviews/{resource_id}/teacher-review")
@api_response()
def review_interview(resource_id: str, body: TeacherReview, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id, True)
        data = json.loads(row.data)
        if row.version != body.version:
            raise HTTPException(409, "Interview version conflict")
        if row.state == "adopted":
            raise HTTPException(409, "An adopted interview review is immutable")
        if data.get("status") != "completed" or not data.get("candidate"):
            raise HTTPException(409, "Interview has no completed candidate to review")
        candidate_job = _verified_candidate_job(session, row, data["candidate"])
        criteria = _review_criteria(data, body.criteria) if body.decision == "approve" else []
        review = {
            "decision": body.decision,
            "notes": body.notes,
            "criteria": criteria,
            "score": round(sum(item["score"] for item in criteria), 4) if criteria else None,
            "maximum": round(sum(item["max_score"] for item in criteria), 4) if criteria else None,
            "actor_id": principal(user),
            "actor_username": user["username"],
            "candidate_job_id": candidate_job.id,
            "candidate_hash": digest(data["candidate"]),
            "source_hash": digest(data.get("native_source")),
            "reviewed_at": datetime.utcnow().isoformat(),
        }
        data["teacher_review"] = review
        row.data, row.version, row.state = dumps(data), row.version + 1, "approved" if body.decision == "approve" else "rejected"
        log(session, candidate_job.id, "log", {"event": "interview_reviewed", "actor": principal(user), "decision": body.decision, "notes": body.notes})
        return {"state": row.state, "version": row.version, "review": review, "native_adoption_available": bool(data.get("native_source"))}


@router.post("/interviews/{resource_id}/adopt")
@api_response()
def adopt_interview_grade(resource_id: str, body: AdoptGrade, user=Depends(active_user)):
    with transaction() as session:
        row = _lock_interview(session, user, resource_id, True)
        data = json.loads(row.data)
        if row.state == "adopted":
            return {"adopted": True, "replay": True, "version": row.version}
        if row.version != body.version:
            raise HTTPException(409, "Interview version conflict")
        review = data.get("teacher_review") or {}
        source = data.get("native_source")
        if row.state != "approved" or review.get("decision") != "approve":
            raise HTTPException(409, "Teacher approval is required before adoption")
        if not source:
            raise HTTPException(409, "Interview is not bound to a native subjective answer")
        candidate = data.get("candidate") or {}
        job = _verified_candidate_job(session, row, candidate)
        if review.get("candidate_job_id") != job.id or review.get("candidate_hash") != digest(candidate) or review.get("source_hash") != digest(source):
            raise HTTPException(409, "Reviewed candidate or source has changed")
        ps_access(session, user, source["psid"], True)
        # Refresh under the lock before comparing the invitation snapshot. A plain
        # read followed by SELECT FOR UPDATE can retain stale identity-map values.
        locked = session.query(ProblemSetAnswerSheetDetail).filter_by(asd_id=source["asd_id"]).populate_existing().with_for_update().one_or_none()
        question = session.query(ProblemSubjective).filter_by(pid=source["pid"]).populate_existing().with_for_update().one_or_none()
        if locked is None or question is None:
            raise HTTPException(409, "Native answer or question no longer exists")
        try:
            _, current = answer_snapshot(session, source["psid"], source["gid"], source["pid"], source["username"])
        except ValueError as error:
            raise HTTPException(409, str(error))
        if current["asd_id"] != source["asd_id"] or current["input_hash"] != source["input_hash"] or current["grade_hash"] != source["grade_hash"]:
            raise HTTPException(409, "Native answer, rubric, or grade changed since invitation")
        try:
            previous_log = json.loads(locked.judgeLog or "[]")
        except (TypeError, ValueError):
            previous_log = ["unparseable"]
        if previous_log and not body.overwrite_manual:
            raise HTTPException(409, "Explicit confirmation required to replace an existing grade")
        before = {"judgeLog": locked.judgeLog, "judgeComment": locked.judgeComment, "judgeLock": locked.judgeLock_username}
        locked.judgeLog = dumps([{"name": item["name"], "score": item["max_score"], "jScore": item["score"]} for item in review["criteria"]])
        locked.judgeComment = "\n\n".join(filter(None, [candidate.get("summary", ""), review.get("notes", "")]))
        locked.judgeLock_username = user["username"]
        data["adoption"] = {"actor_id": principal(user), "actor_username": user["username"], "reason": body.reason,
                            "asd_id": locked.asd_id, "adopted_at": datetime.utcnow().isoformat(), "source_grade_hash": source["grade_hash"]}
        row.data, row.version, row.state = dumps(data), row.version + 1, "adopted"
        log(session, job.id, "log", {"event": "interview_grade_adopted", "actor": principal(user), "reason": body.reason,
                                     "asd_id": locked.asd_id, "before": before, "after": json.loads(locked.judgeLog)})
        return {"adopted": True, "asd_id": locked.asd_id, "version": row.version}
