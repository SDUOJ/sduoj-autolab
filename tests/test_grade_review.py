import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from auto_task import store
from db import ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail
from model.answer_sheet import answerSheetModel
from model.grade_review import SubjectiveGradeReview, grade_version, record_ai_review, request_review
from ser.auto_task_ai import Grade
from ser.base import page
from tests.test_ai_backend import database, client, native_answer_fixture


@pytest.fixture
def review_client(client, monkeypatch):
    from server import answer_sheet
    from ser import answer_sheet as schema
    native_answer_fixture(client)
    # The authenticated identity is deliberately independent of the graded student.
    from tests.test_ai_backend import api, USER
    client.app.dependency_overrides[api.cover_header] = lambda: USER
    client.app.include_router(answer_sheet.router)
    monkeypatch.setattr(schema, "problem_set_user", AsyncMock())

    def model():
        obj = object.__new__(answerSheetModel)
        obj.session = store.factory()()
        obj.get_group_random_list_cache = AsyncMock(return_value=([0], [0], 1))
        obj.get_gid_pid_by_psid_gi_pi_cache = AsyncMock(return_value=(1, 1))
        obj.ps_get_config_by_psid_cache = AsyncMock(return_value={"showSubjectiveJudgeLog": 1})
        return obj

    monkeypatch.setattr(answer_sheet, "answerSheetModel", model)
    with store.transaction() as s:
        d = s.query(ProblemSetAnswerSheetDetail).get(1)
        d.judgeLog = '[{"name":"correct","score":10,"jScore":8}]'
        d.judgeComment, d.judgeLock_username, d.tm_answer_submit = "AI evidence", "<AUTO>", datetime.utcnow()
        record_ai_review(s, d, Grade(criteria=[{"name": "correct", "score": 8, "max_score": 10, "evidence": "answer"}],
                                   confidence=0.5, summary="Please check evidence", needs_review=True), "test-task")
        version = grade_version(d)
    return client, {"router": {"psid": 1, "gid": 0, "pid": 0}, "reason": "请复核第二项扣分", "grade_version": version}, model


def test_student_request_is_durable_idempotent_and_keeps_score(review_client):
    client, body, _ = review_client
    response = client.post("/answer_sheet/gradeReview", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "pending"
    assert response.json()["data"]["ai_needs_review"]
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 200
    assert client.post("/answer_sheet/gradeReview", json={**body, "reason": "other reason"}).status_code == 409
    with store.transaction() as s:
        review = s.query(SubjectiveGradeReview).get(1)
        assert review.requested_by == "student"
        assert review.reason == body["reason"]
        assert len(json.loads(review.history)) == 1
        assert json.loads(s.query(ProblemSetAnswerSheetDetail).get(1).judgeLog)[0]["jScore"] == 8


@pytest.mark.parametrize("reason", ["", "   ", "x" * 2001])
def test_student_request_requires_bounded_reason(review_client, reason):
    client, body, _ = review_client
    assert client.post("/answer_sheet/gradeReview", json={**body, "reason": reason}).status_code == 422


@pytest.mark.parametrize("lock", [None, "teacher"])
def test_only_auto_grades_allow_student_review(review_client, lock):
    client, body, _ = review_client
    with store.transaction() as s:
        s.query(ProblemSetAnswerSheetDetail).get(1).judgeLock_username = lock
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 409


def test_student_cannot_request_for_another_user_or_an_old_grade(review_client, monkeypatch):
    client, body, _ = review_client
    assert client.post("/answer_sheet/gradeReview", json={**body, "username": "other"}).status_code == 422
    assert client.post("/answer_sheet/gradeReview", json={**body, "grade_version": "0" * 64}).status_code == 409
    from tests.test_ai_backend import api
    client.app.dependency_overrides[api.cover_header] = lambda: {"username": "other", "userId": 99}
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 404


def test_hidden_grades_cannot_be_reviewed(review_client, monkeypatch):
    client, body, model = review_client
    from server import answer_sheet
    def hidden_model():
        obj = model()
        obj.ps_get_config_by_psid_cache = AsyncMock(return_value={"showSubjectiveJudgeLog": 0})
        return obj
    monkeypatch.setattr(answer_sheet, "answerSheetModel", hidden_model)
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 403


def test_teacher_claim_is_exclusive_and_submit_resolves_request(review_client):
    client, body, model = review_client
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 200
    obj = model()
    try:
        info = asyncio.run(obj.get_judge_info_by_psid_gi_pi_username(1, 0, 0, "student", "teacher"))
        assert info["canEdit"] and info["judgeLock_username"] == "<AUTO>"
        assert info["gradeReview"]["reason"] == body["reason"]
        other = asyncio.run(obj.get_judge_info_by_psid_gi_pi_username(1, 0, 0, "student", "other-teacher"))
        assert not other["canEdit"] and other["judgeLock_username"] == "<AUTO>"
        with pytest.raises(HTTPException) as denied:
            asyncio.run(obj.update_judgeLog_by_psid_gi_pi_username(1, 0, 0, "student",
                [{"name": "correct", "score": 10, "jScore": 1}], 0, "wrong owner", judger="other-teacher"))
        assert denied.value.status_code == 409
        with pytest.raises(HTTPException) as denied_cancel:
            asyncio.run(obj.update_judgeLog_by_psid_gi_pi_username(1, 0, 0, "student",
                [], 1, None, judger="other-teacher"))
        assert denied_cancel.value.status_code == 409
        asyncio.run(obj.update_judgeLog_by_psid_gi_pi_username(1, 0, 0, "student",
                    [{"name": "correct", "score": 10, "jScore": 9}], 0, "reviewed", judger="teacher"))
    finally:
        obj.session.close()
    with store.transaction() as s:
        d, review = s.query(ProblemSetAnswerSheetDetail).get(1), s.query(SubjectiveGradeReview).get(1)
        assert json.loads(d.judgeLog)[0]["jScore"] == 9
        assert d.judgeLock_username == "teacher"
        assert review.status == "resolved" and review.resolved_by == "teacher"
        assert not review.ai_needs_review
        assert review.reason == body["reason"]
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 409


def test_expired_teacher_claim_can_be_reclaimed(review_client):
    from datetime import timedelta
    from model.grade_review import claim_auto_grade
    client, _, model = review_client
    with store.transaction() as s:
        detail = s.query(ProblemSetAnswerSheetDetail).get(1)
        assert claim_auto_grade(s, detail, "first")
        review = s.query(SubjectiveGradeReview).get(1)
        review.reviewer_until = datetime.utcnow() - timedelta(seconds=1)
    with store.transaction() as s:
        assert claim_auto_grade(s, s.query(ProblemSetAnswerSheetDetail).get(1), "second")
        assert s.query(SubjectiveGradeReview).get(1).reviewer_id == "second"


def test_ai_cannot_overwrite_a_pending_student_request(review_client):
    client, body, _ = review_client
    assert client.post("/answer_sheet/gradeReview", json=body).status_code == 200
    with pytest.raises(ValueError, match="awaiting a teacher"):
        with store.transaction() as s:
            record_ai_review(s, s.query(ProblemSetAnswerSheetDetail).get(1),
                Grade(criteria=[], summary="new", needs_review=False, confidence=1), "new-task")


def test_review_filters_are_applied_before_pagination_and_include_scores(review_client, monkeypatch):
    client, body, model = review_client
    client.post("/answer_sheet/gradeReview", json=body)
    with store.transaction() as s:
        s.add(ProblemSetAnswerSheet(asid=2, psid=1, username="another"))
        s.flush()
        d = ProblemSetAnswerSheetDetail(asd_id=2, asid=2, gid=1, pid=1, answer='["answer"]',
            judgeLog='[{"name":"correct","score":10,"jScore":7}]', judgeLock_username="<AUTO>",
            tm_answer_submit=datetime.utcnow())
        s.add(d); s.flush()
        request_review(s, d, "another", "please review", grade_version(d))
    monkeypatch.setattr("sduojApi.getNickName", AsyncMock(return_value="Student"))
    obj = model()
    obj.ps_get_info_by_id_cache = AsyncMock(return_value={"groupInfo": [{"gid": 1, "name": "Group"}]})
    obj.group_get_info_by_id_cache = AsyncMock(return_value={"type": 1, "problemInfo": [{"pid": 1}]})
    obj.get_subjective_basic_cache = AsyncMock(return_value={"type": 1})
    try:
        count, rows = asyncio.run(obj.get_judge_list_by_psid_page(1, page(pageNow=1,pageSize=1),None,None,None,None,1,review_filter="pending"))
        assert count == 2 and len(rows) == 1
        assert rows[0]["score"] == 8 and rows[0]["max_score"] == 10
        count, rows = asyncio.run(obj.get_judge_list_by_psid_page(1, page(pageNow=1,pageSize=20),None,None,None,None,1,review_filter="ai"))
        assert count == 1 and rows[0]["gradeReview"]["ai_needs_review"]
        count, _ = asyncio.run(obj.get_judge_list_by_psid_page(1, page(pageNow=1,pageSize=20),None,None,None,None,1,review_filter="any"))
        assert count == 2
    finally:
        obj.session.close()


def test_additive_migration_is_idempotent_and_keeps_grades(review_client):
    from scripts.migrate_grade_review_table import migrate
    with store.transaction() as s:
        before = s.query(ProblemSetAnswerSheetDetail).get(1).judgeLog
    first, second = migrate(), migrate()
    assert first["table"] == "subjective_grade_review" and not first["created"]
    assert second == first
    with store.transaction() as s:
        assert s.query(ProblemSetAnswerSheetDetail).get(1).judgeLog == before


def test_migration_defaults_to_read_only_plan(database):
    from sqlalchemy import inspect
    from scripts.migrate_grade_review_table import migrate
    SubjectiveGradeReview.__table__.drop(database)
    assert migrate()["apply_required"]
    assert "subjective_grade_review" not in inspect(database).get_table_names()
    assert migrate(apply=True)["created"]
    assert not migrate()["apply_required"]


def test_backfill_restores_ai_flag_without_changing_grade_and_is_idempotent(client):
    from scripts.backfill_grade_review_metadata import backfill
    from auto_task.entities import TaskJob
    body = native_answer_fixture(client)
    created = client.post("/auto-task/ai/native/grading/create", json=body).json()["data"]
    with store.transaction() as s:
        d = s.query(ProblemSetAnswerSheetDetail).get(1)
        d.judgeLog = '[{"name":"correct","score":10,"jScore":8}]'
        d.judgeComment, d.judgeLock_username = "needs a teacher", "<AUTO>"
        job = s.query(TaskJob).get(created["taskId"])
        job.status, job.finished_at = "success", datetime.utcnow()
        job.result = json.dumps({"source": {"asd_id":1}, "written_back": True,
            "judgeLog": json.loads(d.judgeLog), "judgeComment": d.judgeComment,
            "evaluation": {"criteria":[{"name":"correct","score":8,"max_score":10,"evidence":"answer"}],
                           "summary": d.judgeComment, "needs_review":True,"confidence":0.5}})
    plan = backfill(1)
    assert len(plan["candidates"]) == 1 and plan["backfilled"] == []
    with store.transaction() as s:
        assert s.query(SubjectiveGradeReview).get(1) is None
    assert len(backfill(1, apply=True)["backfilled"]) == 1
    assert backfill(1, apply=True)["backfilled"] == []
    with store.transaction() as s:
        assert s.query(SubjectiveGradeReview).get(1).ai_needs_review == 1
        assert json.loads(s.query(ProblemSetAnswerSheetDetail).get(1).judgeLog)[0]["jScore"] == 8
    SubjectiveGradeReview.__table__.drop(store.factory().kw["bind"])
    assert len(backfill(1)["candidates"]) == 1
    with pytest.raises(RuntimeError, match="migration"):
        backfill(1, apply=True)
    from scripts.migrate_grade_review_table import migrate
    migrate(apply=True)
