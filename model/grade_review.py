"""Review metadata for a native subjective grade, separate from its score."""

import hashlib
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import Column, DateTime as SQLDateTime, ForeignKey, Integer, String, Text

from db import Base, ProblemSetAnswerSheetDetail


class SubjectiveGradeReview(Base):
    __tablename__ = "subjective_grade_review"
    asd_id = Column(Integer, ForeignKey("problem_set_answer_sheet_detail.asd_id"), primary_key=True)
    grade_version = Column(String(64), nullable=False)
    ai_needs_review = Column(Integer, nullable=False, default=0, index=True)
    ai_reason = Column(Text, nullable=True)
    task_id = Column(String(63), nullable=True)
    status = Column(String(16), nullable=False, default="none", index=True)
    reviewer_id = Column(String(63), nullable=True)
    reviewer_until = Column(SQLDateTime, nullable=True)
    reason = Column(Text, nullable=True)
    requested_by = Column(String(63), nullable=True)
    requested_at = Column(SQLDateTime, nullable=True)
    resolved_by = Column(String(63), nullable=True)
    resolved_at = Column(SQLDateTime, nullable=True)
    history = Column(Text, nullable=False, default="[]")


def grade_version(detail):
    raw = json.dumps({"log": detail.judgeLog, "comment": detail.judgeComment},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def review_view(session, detail, row=False):
    if row is False:
        row = session.query(SubjectiveGradeReview).filter_by(asd_id=detail.asd_id).first()
    automatic = detail.judgeLock_username == "<AUTO>" and bool(json.loads(detail.judgeLog or "[]"))
    version = grade_version(detail)
    current = row is not None and row.grade_version == version
    return {"can_request": automatic, "grade_version": version,
            "ai_needs_review": bool(current and automatic and row.ai_needs_review),
            "ai_reason": row.ai_reason if current and automatic and row.ai_needs_review else None,
            "status": row.status if current else "none", "reason": row.reason if current else None,
            "requested_at": row.requested_at.isoformat() + "Z" if current and row.requested_at else None}


def record_ai_review(session, detail, evaluation, task_id):
    row = session.query(SubjectiveGradeReview).filter_by(asd_id=detail.asd_id).with_for_update().first()
    if row and row.status == "pending":
        raise ValueError("Student grade review is awaiting a teacher")
    if row is None:
        row = SubjectiveGradeReview(asd_id=detail.asd_id)
        session.add(row)
    row.grade_version = grade_version(detail)
    row.ai_needs_review, row.ai_reason = int(evaluation.needs_review), evaluation.summary if evaluation.needs_review else None
    row.task_id, row.status = task_id, "none"
    row.reviewer_id = row.reviewer_until = None
    row.reason = row.requested_by = row.requested_at = row.resolved_by = row.resolved_at = None
    return row


def claim_auto_grade(session, detail, judger, now=None):
    now = now or datetime.utcnow()
    row = session.query(SubjectiveGradeReview).filter_by(asd_id=detail.asd_id).with_for_update().first()
    if row is None:
        row = SubjectiveGradeReview(asd_id=detail.asd_id, grade_version=grade_version(detail), history="[]")
        session.add(row)
        session.flush()
    if row.reviewer_until is None or row.reviewer_until <= now or row.reviewer_id == judger:
        row.reviewer_id, row.reviewer_until = judger, now + timedelta(minutes=5)
    return row.reviewer_id == judger


def request_review(session, detail, username, reason, expected_version):
    # The caller locks the native answer row before this metadata row.
    if detail.judgeLock_username != "<AUTO>" or not json.loads(detail.judgeLog or "[]"):
        raise HTTPException(409, "仅自动评分支持申请人工复核")
    if grade_version(detail) != expected_version:
        raise HTTPException(409, "成绩已更新，请刷新后再申请复核")
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(422, "请填写 1 至 2000 字的复核理由")
    row = session.query(SubjectiveGradeReview).filter_by(asd_id=detail.asd_id).with_for_update().first()
    if row and row.status == "pending":
        if row.reason != reason:
            raise HTTPException(409, "已有待处理的人工复核申请")
        return review_view(session, detail, row)
    if row is None:
        row = SubjectiveGradeReview(asd_id=detail.asd_id, grade_version=expected_version, history="[]")
        session.add(row)
    row.grade_version, row.status = expected_version, "pending"
    row.reason, row.requested_by, row.requested_at = reason, username, datetime.utcnow()
    row.resolved_by = row.resolved_at = None
    history = json.loads(row.history or "[]")
    history.append({"event": "requested", "by": username, "reason": reason,
                    "grade_version": expected_version, "at": row.requested_at.isoformat() + "Z"})
    row.history = json.dumps(history, ensure_ascii=False)
    session.flush()
    return review_view(session, detail, row)


def resolve_review(session, detail, judger):
    row = session.query(SubjectiveGradeReview).filter_by(asd_id=detail.asd_id).with_for_update().first()
    if row is None:
        return
    row.reviewer_id = row.reviewer_until = None
    if row.status == "pending":
        row.status, row.resolved_by, row.resolved_at = "resolved", judger, datetime.utcnow()
        history = json.loads(row.history or "[]")
        history.append({"event": "resolved", "by": judger, "at": row.resolved_at.isoformat() + "Z"})
        row.history = json.dumps(history, ensure_ascii=False)
    row.ai_needs_review, row.grade_version = 0, grade_version(detail)
