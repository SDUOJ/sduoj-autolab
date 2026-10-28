"""Keep native automatic grade writeback and allow later teacher corrections."""

import json

from db import ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail, ProblemSubjective
from ser.auto_task_ai import Grade
from .entities import AiResource, uid
from .store import digest, dumps, log, transaction


def answer_snapshot(session, psid, gid, pid, username):
    sheet = session.query(ProblemSetAnswerSheet).filter_by(psid=psid, username=username).first()
    detail = session.query(ProblemSetAnswerSheetDetail).filter_by(asid=sheet.asid if sheet else -1, gid=gid, pid=pid).first()
    question = session.query(ProblemSubjective).filter_by(pid=pid).first()
    if not detail or not question:
        raise ValueError("Subjective answer or question not found")
    config = json.loads(question.config or "{}")
    return detail, {"asd_id": detail.asd_id, "psid": psid, "gid": gid, "pid": pid, "username": username,
                    "question": question.description, "answer": json.loads(detail.answer or "[]"),
                    "rubric": config.get("judgeConfig", []), "grade_hash": digest({"log": detail.judgeLog, "comment": detail.judgeComment}),
                    "input_hash": digest({"question": question.description, "answer": detail.answer, "rubric": config.get("judgeConfig", [])})}


def writeback_subjective_result(session, job, value):
    """Write a verified task result atomically, without replacing later edits."""
    if value.get("written_back"):
        return value
    if job.task_type != "subjective_review":
        raise ValueError("Expected native subjective review task")
    source = value["source"]
    payload = json.loads(job.payload)
    if any(source[key] != payload[key] for key in ("psid", "gid", "pid", "username")):
        raise ValueError("Native grading source does not match task")
    detail = (session.query(ProblemSetAnswerSheetDetail)
              .filter_by(asd_id=source["asd_id"]).with_for_update().one())
    _, current = answer_snapshot(session, source["psid"], source["gid"], source["pid"], source["username"])
    if current["input_hash"] != source["input_hash"] or current["grade_hash"] != source["grade_hash"]:
        raise ValueError("Native answer/rubric/grade changed during model execution")
    if detail.judgeLog and json.loads(detail.judgeLog) and detail.judgeLock_username not in (None, "<AUTO>"):
        raise ValueError("Existing manual grade must be preserved")
    evaluation = Grade.parse_obj(value["evaluation"])
    expected = [(item.get("name"), float(item.get("score", 0))) for item in current["rubric"]]
    if expected != [(item.name, item.max_score) for item in evaluation.criteria]:
        raise ValueError("Rubric mismatch")
    judge_log = [{"name": item.name, "score": item.max_score, "jScore": item.score}
                 for item in evaluation.criteria]
    if judge_log != value["judgeLog"] or evaluation.summary != value["judgeComment"]:
        raise ValueError("Native grade differs from validated model result")
    before = {"judgeLog": detail.judgeLog, "judgeComment": detail.judgeComment,
              "judgeLock_username": detail.judgeLock_username}
    detail.judgeLog = dumps(judge_log)
    detail.judgeComment = evaluation.summary
    detail.judgeLock_username = "<AUTO>"
    from model.grade_review import record_ai_review
    record_ai_review(session, detail, evaluation, job.id)
    log(session, job.id, "log", {"event": "native_grade_auto_written_back",
        "asd_id": detail.asd_id, "before": before, "after": judge_log})
    return {**value, "candidate": False, "written_back": True}


def subjective(ctx):
    from .handlers import ask
    payload = json.loads(ctx.job["payload"])
    with transaction() as s:
        detail, source = answer_snapshot(s, payload["psid"], payload["gid"], payload["pid"], payload["username"])
        if detail.judgeLog and json.loads(detail.judgeLog) and detail.judgeLock_username not in (None, "<AUTO>"):
            return {"judgeLog": json.loads(detail.judgeLog), "judgeComment": detail.judgeComment or "",
                    "candidate": False, "written_back": False, "skipped": "manual_grade_exists"}, None
    saved = ctx.job.get("input_snapshot", {}).get("data", {}).get("native_source")
    if saved:
        if source["input_hash"] != saved["input_hash"] or source["grade_hash"] != saved["grade_hash"]:
            raise ValueError("Native answer changed since task submission")
        source = saved
    from .subjective_review import SubjectiveReviewPayload, _SubjectiveReviewService, _ensure_fastapi_cache
    service = _SubjectiveReviewService(SubjectiveReviewPayload.parse_obj(payload), ctx.job["id"])
    service._ensure_not_deleted = ctx.check
    try:
        _ensure_fastapi_cache(service.loop)
        detail = service._get_answer_detail_dict()
        from sduojApi import getUserId
        student_id = service._run_async(getUserId(payload["username"]))
        with transaction() as s:
            question_type = s.query(ProblemSubjective).filter_by(pid=payload["pid"]).one().type
        text = service._get_student_answer_text(question_type, student_id, detail)
        question_text = service._get_subject_markdown(payload["pid"], source["question"], student_id)
        index_map, _ = service._run_async(service.answer_model.get_gid_pid2indexDict_cache(payload["psid"]))
        related = service._build_programming_sections(service.payload, index_map, student_id)
        from .handlers import moderation
        material = {"question": question_text, "answer": text, "rubric": source["rubric"], "related_programming": related}
        moderation(ctx, material, [], third_party=True)
        result = ask(ctx, Grade, "Grade the student's answer against these exact rubric names and maxima. Return evidence and candidate scores in Chinese. Never alter a rubric maximum.\n" + dumps(material))
    finally:
        service.answer_model.session.close()
        service.subject_model.session.close()
    expected = [(item.get("name"), float(item.get("score", 0))) for item in source["rubric"]]
    actual = [(item["name"], item["max_score"]) for item in result["criteria"]]
    if expected != actual:
        from .store import log
        with transaction() as s:
            from .store import fenced
            fenced(s, ctx.job["id"], ctx.job["lease_token"])
            log(s, ctx.job["id"], "log", {
                "event": "grade_rubric_mismatch",
                "expected_criteria": expected,
                "actual_criteria": actual,
            })
        raise ValueError("Rubric mismatch")
    legacy = {"judgeLog": [{"name": item["name"], "score": item["max_score"], "jScore": item["score"]} for item in result["criteria"]],
              "judgeComment": result["summary"], "candidate": True, "source": source, "evaluation": result}
    def apply(session, job, value):
        session.info.update(ai_actor=job.owner_id, ai_task=job.id)
        if job.resource_id:
            row = session.query(AiResource).filter_by(id=job.resource_id, deleted=0).with_for_update().one()
            if row.version != ctx.job["input_snapshot"]["version"]:
                raise ValueError("Native grading resource changed")
            row.version += 1
        else:
            row = AiResource(id=uid(), kind="grading", owner_id=job.owner_id, owner_username=job.owner_username, psid=job.psid)
            session.add(row)
        value = writeback_subjective_result(session, job, value)
        row.state, row.data = "adopted", dumps({"candidate": value, "candidate_job_id": job.id,
                                              "native_writeback": {"automatic": True, "task_id": job.id}})
        job.resource_id = row.id
        return {**value, "resource_id": row.id}
    return legacy, apply


def summary(ctx):
    from .summary_report import SummaryReportTask
    result = SummaryReportTask(json.loads(ctx.job["payload"]), {"task_id": ctx.job["id"]}).run()
    ctx.check()
    return result, None
