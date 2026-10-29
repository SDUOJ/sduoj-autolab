"""Recover review tags for existing, unchanged automatic native grades."""

import json
from sqlalchemy import inspect

from db import ProblemSetAnswerSheetDetail
from auto_task.entities import TaskJob
from auto_task.store import factory
from model.grade_review import SubjectiveGradeReview, record_ai_review
from ser.auto_task_ai import Grade


def backfill(psid=221, apply=False):
    changed, skipped = [], 0
    session = factory()()
    try:
        metadata_exists = inspect(session.bind).has_table(SubjectiveGradeReview.__tablename__)
        if apply and not metadata_exists:
            raise RuntimeError("Apply the grade review table migration before backfilling metadata")
        jobs = (session.query(TaskJob).filter_by(psid=psid, task_type="subjective_review", status="success")
                .order_by(TaskJob.finished_at.desc()).all())
        seen = set()
        for job in jobs:
            value = json.loads(job.result or "{}")
            source = value.get("source") or {}
            asd_id = source.get("asd_id")
            if not asd_id or asd_id in seen:
                continue
            seen.add(asd_id)
            query = session.query(ProblemSetAnswerSheetDetail).filter_by(asd_id=asd_id)
            detail = (query.with_for_update() if apply else query).first()
            existing = (session.query(SubjectiveGradeReview).filter_by(asd_id=asd_id).first()
                        if metadata_exists else None)
            if (not detail or existing or not value.get("written_back") or detail.judgeLock_username != "<AUTO>"
                    or json.loads(detail.judgeLog or "[]") != value.get("judgeLog")
                    or detail.judgeComment != value.get("judgeComment")):
                skipped += 1
                continue
            evaluation = Grade.parse_obj(value["evaluation"])
            if apply:
                record_ai_review(session, detail, evaluation, job.id)
            changed.append({"task_id": job.id, "asd_id": asd_id, "ai_needs_review": evaluation.needs_review})
        if apply:
            session.commit()
        else:
            session.rollback()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
    return {"psid": psid, "apply": apply, "candidates": changed, "backfilled": changed if apply else [],
            "skipped": skipped, "grades_changed": False}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psid", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.psid <= 0:
        parser.error("--psid must be positive")
    print(json.dumps(backfill(args.psid, apply=args.apply), ensure_ascii=False, sort_keys=True))
