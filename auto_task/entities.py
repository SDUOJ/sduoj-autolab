"""Persistent AI state in Autolab's existing SQLAlchemy metadata/MySQL database."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime as SqlDateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT, VARCHAR

from db import Base

LongText = Text().with_variant(LONGTEXT(), "mysql")
DateTime = SqlDateTime().with_variant(DATETIME(fsp=6), "mysql")
OpaqueKey = String(128).with_variant(VARCHAR(128, collation="utf8mb4_bin"), "mysql")


def uid():
    return uuid.uuid4().hex


class TaskJob(Base):
    __tablename__ = "auto_task_job"
    id = Column(String(63), ForeignKey("auto_task_run.id"), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    owner_username = Column(String(100), nullable=False)
    payer_id = Column(String(64), nullable=False)
    operation_key = Column(OpaqueKey, nullable=False)
    task_type = Column(String(63), nullable=False)
    payload = Column(LongText, nullable=False)
    input_hash = Column(String(64), nullable=False)
    resource_id = Column(String(63), nullable=True, index=True)
    course_id = Column(Integer, nullable=True, index=True)
    psid = Column(Integer, nullable=True, index=True)
    status = Column(String(24), nullable=False, default="pending")
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    result = Column(LongText, nullable=True)
    error = Column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_key", name="uq_ai_job_operation"),
        Index("ix_ai_job_claim", "status", "available_at", "lease_until"),
    )


class AiAccount(Base):
    __tablename__ = "auto_task_account"
    subject_id = Column(String(64), primary_key=True)
    username = Column(String(100), nullable=False)
    balance = Column(Numeric(20, 6), nullable=False, default=0)
    status = Column(String(24), nullable=False, default="active")
    permanent = Column(Integer, nullable=False, default=0)
    reason = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TaskInput(Base):
    __tablename__ = "auto_task_input"
    task_id = Column(String(63), ForeignKey("auto_task_job.id"), primary_key=True)
    resource_version = Column(Integer, nullable=False)
    resource_kind = Column(String(40), nullable=False)
    content = Column(LongText, nullable=False)
    sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiLedger(Base):
    __tablename__ = "auto_task_ledger"
    id = Column(String(63), primary_key=True, default=uid)
    subject_id = Column(String(64), ForeignKey("auto_task_account.subject_id"), nullable=False, index=True)
    billing_key = Column(OpaqueKey, nullable=False, unique=True)
    task_id = Column(String(63), nullable=True, index=True)
    amount = Column(Numeric(20, 6), nullable=False)
    cost_usd = Column(Numeric(20, 9), nullable=False, default=0)
    actor_id = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiResource(Base):
    __tablename__ = "auto_task_resource"
    id = Column(String(63), primary_key=True, default=uid)
    kind = Column(String(40), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    owner_username = Column(String(100), nullable=False)
    course_id = Column(Integer, nullable=True, index=True)
    psid = Column(Integer, nullable=True, index=True)
    parent_id = Column(String(63), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    state = Column(String(24), nullable=False, default="draft")
    data = Column(LongText, nullable=False, default="{}")
    search_text = Column(LongText, nullable=False, default="")
    canonical_key = Column(String(64), nullable=True, unique=True)
    deleted = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (Index("ft_ai_resource_search", "search_text", mysql_prefix="FULLTEXT", mysql_with_parser="ngram"),)


class AiReview(Base):
    __tablename__ = "auto_task_review"
    id = Column(String(63), primary_key=True, default=uid)
    subject_id = Column(String(64), nullable=False, index=True)
    task_id = Column(String(63), nullable=True, index=True)
    decision = Column(String(24), nullable=False)
    reason = Column(Text, nullable=False)
    reviewer_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)


class AiRevision(Base):
    __tablename__ = "auto_task_revision"
    id = Column(String(63), primary_key=True, default=uid)
    resource_id = Column(String(63), ForeignKey("auto_task_resource.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    state = Column(String(24), nullable=False)
    deleted = Column(Integer, nullable=False)
    data = Column(LongText, nullable=False)
    sha256 = Column(String(64), nullable=False)
    actor_id = Column(String(64), nullable=False)
    task_id = Column(String(63), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiPlagiarismSnapshot(Base):
    """Immutable manifest for a native OJ plagiarism input set."""
    __tablename__ = "auto_task_plagiarism_snapshot"
    id = Column(String(63), primary_key=True, default=uid)
    resource_id = Column(String(63), ForeignKey("auto_task_resource.id"), nullable=False, index=True)
    resource_version = Column(Integer, nullable=False)
    psid = Column(Integer, nullable=False, index=True)
    group_id = Column(Integer, nullable=False, index=True)
    pid = Column(Integer, nullable=False, index=True)
    selection_policy = Column(String(24), nullable=False)
    sha256 = Column(String(64), nullable=False)
    count = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiPlagiarismSubmission(Base):
    """A code submission copied at snapshot creation time and never edited."""
    __tablename__ = "auto_task_plagiarism_submission"
    id = Column(String(63), primary_key=True, default=uid)
    snapshot_id = Column(String(63), ForeignKey("auto_task_plagiarism_snapshot.id"), nullable=False, index=True)
    answer_detail_id = Column(Integer, nullable=False, index=True)
    native_submission_id = Column(OpaqueKey, nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    username = Column(String(100), nullable=False)
    code = Column(LongText, nullable=False)
    code_sha256 = Column(String(64), nullable=False)
    gmt_create = Column(DateTime, nullable=True)
    judge_result = Column(Integer, nullable=True)
    judge_score = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("snapshot_id", "native_submission_id", name="uq_ai_plag_snapshot_submission"),
    )


class AiSetting(Base):
    __tablename__ = "auto_task_setting"
    id = Column(String(40), primary_key=True)
    data = Column(Text, nullable=False, default="{}")
    secret = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)


class AiRun(Base):
    __tablename__ = "auto_task_ai_run"
    id = Column(String(63), primary_key=True, default=uid)
    task_id = Column(String(63), nullable=False, index=True)
    attempt = Column(Integer, nullable=False)
    model = Column(String(128), nullable=False)
    usage = Column(Text, nullable=False, default="{}")
    cost_usd = Column(Numeric(20, 9), nullable=False, default=0)
    session_id = Column(String(128), nullable=True)
    status = Column(String(24), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiFile(Base):
    __tablename__ = "auto_task_file"
    id = Column(String(63), primary_key=True, default=uid)
    owner_id = Column(String(64), nullable=False, index=True)
    resource_id = Column(String(63), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    media_type = Column(String(128), nullable=False)
    path = Column(String(255), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    sequence_key = Column(String(128), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiFileLink(Base):
    __tablename__ = "auto_task_file_link"
    file_id = Column(String(63), ForeignKey("auto_task_file.id"), primary_key=True)
    resource_id = Column(String(63), ForeignKey("auto_task_resource.id"), primary_key=True)


TABLES = [TaskJob.__table__, AiAccount.__table__, AiLedger.__table__,
          AiResource.__table__, AiReview.__table__, AiSetting.__table__,
          AiRun.__table__, AiFile.__table__, AiFileLink.__table__, TaskInput.__table__, AiRevision.__table__,
          AiPlagiarismSnapshot.__table__, AiPlagiarismSubmission.__table__]


# Capture ORM mutations in the same transaction, including publication metadata
# that intentionally does not change the content version used for idempotency.
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


@event.listens_for(Session, "before_flush")
def record_resource_revision(session, flush_context, instances):
    import hashlib
    for row in list(session.new) + list(session.dirty):
        if not isinstance(row, AiResource):
            continue
        changed = row in session.new or any(inspect(row).attrs[key].history.has_changes()
                                           for key in ("data", "version", "state", "deleted"))
        if not changed:
            continue
        row.id = row.id or uid()
        row.version = row.version or 1
        row.state = row.state or "draft"
        row.deleted = row.deleted or 0
        row.data = row.data or "{}"
        row.updated_at = datetime.utcnow()
        session.add(AiRevision(resource_id=row.id, version=row.version, state=row.state, deleted=row.deleted,
                               data=row.data, sha256=hashlib.sha256(row.data.encode()).hexdigest(),
                               actor_id=session.info.get("ai_actor", row.owner_id), task_id=session.info.get("ai_task")))
