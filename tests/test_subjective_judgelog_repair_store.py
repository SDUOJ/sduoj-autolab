"""Transactional score repair regressions against a disposable in-memory database."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from db import AutoTaskRun, AutoTaskRunLog, Base, ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail
import fix_subjective_judgelog_names as repair


@compiles(LONGTEXT, "sqlite")
def sqlite_text(*_, **kwargs):
    return "TEXT"


@compiles(BIGINT, "sqlite")
def sqlite_integer(*_, **kwargs):
    return "INTEGER"


def payload(score, comment="original"):
    return {"judgeLog": [{"name": "Criterion A", "score": 10, "jScore": score}], "judgeComment": comment}


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    tables = [AutoTaskRun.__table__, AutoTaskRunLog.__table__,
              ProblemSetAnswerSheet.__table__, ProblemSetAnswerSheetDetail.__table__]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine)
    now = datetime(2026, 10, 16)
    with factory.begin() as session:
        session.add(ProblemSetAnswerSheet(asid=1, psid=1, username="alice"))
        session.add(ProblemSetAnswerSheetDetail(asd_id=1, asid=1, gid=2, pid=3,
                    judgeLock_username="<AUTO>", judgeLog=json.dumps(payload(2)["judgeLog"]), judgeComment="original"))
        session.add(AutoTaskRun(id="old", task_type="subjective_review", psid=1, username="alice",
                    groupId=100, problemId=3, status="success", create_time=now, end_time=now))
        session.add(AutoTaskRunLog(log_id=1, task_id="old", tag="payload",
                    content=json.dumps({"psid": 1, "gid": 2, "pid": 3, "username": "alice"})))
        session.add(AutoTaskRunLog(log_id=2, task_id="old", tag="result", content=json.dumps(payload(2))))
    yield factory, engine
    engine.dispose()


def apply(factory, update_answer=False, original=None):
    with factory.begin() as session:
        return repair.apply_repair(session, "old", 2, original or json.dumps(payload(2)), payload(7), update_answer)


def state(factory):
    with factory() as session:
        answer = session.query(ProblemSetAnswerSheetDetail).one()
        return (json.loads(session.query(AutoTaskRunLog).get(2).content),
                json.loads(answer.judgeLog), answer.judgeComment)


def test_history_only_is_the_default_even_when_current_answer_matches(database):
    factory, _ = database
    assert apply(factory) == {"history": "updated", "answer": "not-requested"}
    history, answer, _ = state(factory)
    assert history == payload(7)
    assert answer == payload(2)["judgeLog"]


def test_explicit_current_auto_answer_requires_a_matching_original_snapshot(database):
    factory, _ = database
    assert apply(factory, True)["answer"] == "updated"
    history, answer, comment = state(factory)
    assert history == payload(7)
    assert answer == payload(7)["judgeLog"]
    assert comment == "original"


@pytest.mark.parametrize("lock,score,reason", [
    ("teacher", 2, "human-or-unlocked-answer"),
    (None, 2, "human-or-unlocked-answer"),
    ("<AUTO>", 9, "answer-snapshot-differs"),
])
def test_manual_unlocked_or_newer_score_is_preserved(database, lock, score, reason):
    factory, _ = database
    with factory.begin() as session:
        answer = session.query(ProblemSetAnswerSheetDetail).one()
        answer.judgeLock_username = lock
        answer.judgeLog = json.dumps(payload(score)["judgeLog"])
    assert apply(factory, True)["answer"] == reason
    history, answer, _ = state(factory)
    assert history == payload(7)
    assert answer == payload(score)["judgeLog"]


@pytest.mark.parametrize("new_score", [2, 9])
def test_newer_successful_run_blocks_old_answer_repair_even_with_equal_scores(database, new_score):
    factory, _ = database
    with factory.begin() as session:
        now = datetime(2026, 10, 16) + timedelta(minutes=1)
        session.add(AutoTaskRun(id="new", task_type="subjective_review", psid=1, username="alice",
                    groupId=100, problemId=3, status="success", create_time=now, end_time=now))
        session.add(AutoTaskRunLog(log_id=3, task_id="new", tag="payload",
                    content=json.dumps({"psid": 1, "gid": 2, "pid": 3, "username": "alice"})))
        session.add(AutoTaskRunLog(log_id=4, task_id="new", tag="result", content=json.dumps(payload(new_score))))
        session.query(ProblemSetAnswerSheetDetail).one().judgeLog = json.dumps(payload(new_score)["judgeLog"])
    assert apply(factory, True)["answer"] == "not-latest-completed-run"
    assert state(factory)[1] == payload(new_score)["judgeLog"]


def test_active_new_run_blocks_repair_until_its_outcome_is_known(database):
    factory, _ = database
    with factory.begin() as session:
        now = datetime(2026, 10, 16) + timedelta(minutes=1)
        session.add(AutoTaskRun(id="active", task_type="subjective_review", psid=1, username="alice",
                    problemId=3, status="running", create_time=now))
        session.add(AutoTaskRunLog(log_id=3, task_id="active", tag="payload",
                    content=json.dumps({"psid": 1, "gid": 2, "pid": 3, "username": "alice"})))
    assert apply(factory, True)["answer"] == "not-latest-completed-run"
    assert state(factory)[1] == payload(2)["judgeLog"]


def test_another_question_does_not_block_current_answer_repair(database):
    factory, _ = database
    with factory.begin() as session:
        now = datetime(2026, 10, 16) + timedelta(minutes=1)
        session.add(AutoTaskRun(id="unrelated", task_type="subjective_review", psid=1, username="alice",
                    problemId=4, status="success", create_time=now, end_time=now))
        session.add(AutoTaskRunLog(log_id=3, task_id="unrelated", tag="payload",
                    content=json.dumps({"psid": 1, "gid": 2, "pid": 4, "username": "alice"})))
    assert apply(factory, True)["answer"] == "updated"


@pytest.mark.parametrize("change", ["content", "append", "retry"])
def test_changed_log_new_result_or_retried_task_rejects_the_scanned_repair(database, change):
    factory, _ = database
    with factory.begin() as session:
        if change == "content":
            session.query(AutoTaskRunLog).get(2).content = json.dumps(payload(9))
        elif change == "append":
            session.add(AutoTaskRunLog(log_id=3, task_id="old", tag="result", content=json.dumps(payload(9))))
        else:
            session.query(AutoTaskRun).get("old").status = "pending"
    with pytest.raises(repair.RepairConflict):
        apply(factory, True)
    assert state(factory)[1] == payload(2)["judgeLog"]
    assert state(factory)[0] == payload(9 if change == "content" else 2)


def test_missing_answer_does_not_prevent_independent_history_repair(database):
    factory, _ = database
    with factory.begin() as session:
        session.query(ProblemSetAnswerSheetDetail).delete()
    assert apply(factory, True) == {"history": "updated", "answer": "missing-answer"}


def test_candidate_grades_still_require_teacher_adoption(database):
    factory, _ = database
    original = payload(2)
    original["candidate"] = True
    with factory.begin() as session:
        session.query(AutoTaskRunLog).get(2).content = json.dumps(original)
    assert apply(factory, True, json.dumps(original))["answer"] == "candidate-requires-adoption"
    assert state(factory)[1] == payload(2)["judgeLog"]


def test_answer_conditional_update_conflict_rolls_back_log_and_answer(database):
    factory, engine = database
    # Simulate a change at the final conditional UPDATE boundary. SQLite does
    # not implement FOR UPDATE; actual row-lock behavior needs isolated MySQL.
    def change_answer(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE problem_set_answer_sheet_detail"):
            cursor.execute("UPDATE problem_set_answer_sheet_detail SET judgeComment='changed' WHERE asd_id=1")

    event.listen(engine, "before_cursor_execute", change_answer)
    try:
        with pytest.raises(repair.RepairConflict, match="Current answer changed"):
            apply(factory, True)
    finally:
        event.remove(engine, "before_cursor_execute", change_answer)
    history, answer, comment = state(factory)
    assert history == payload(2)
    assert answer == payload(2)["judgeLog"]
    assert comment == "original"


def test_main_commit_repairs_history_without_changing_the_current_answer(database, monkeypatch, tmp_path):
    factory, engine = database
    original = payload(2)
    original["judgeLog"][0]["name"] = "Criterion A (reworded)"
    with factory.begin() as session:
        session.query(AutoTaskRunLog).get(2).content = json.dumps(original)
        session.query(ProblemSetAnswerSheetDetail).one().judgeLog = json.dumps(original["judgeLog"])
        session.add(AutoTaskRunLog(log_id=3, task_id="old", tag="log",
                    content="<schema>" + json.dumps(payload(7)) + "</schema>"))
    report = tmp_path / "repair.jsonl"
    monkeypatch.setattr(repair, "parse_args", lambda: SimpleNamespace(
        group_id=[100], username=None, task_id=None, limit=0, only_auto=False,
        min_similarity=0.7, commit=True, update_current_answer=False, report=str(report)))
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    assert repair.main() == 0
    history, answer, _ = state(factory)
    assert history["judgeLog"][0]["jScore"] == 7
    assert answer == original["judgeLog"]
    record = json.loads(report.read_text())
    assert record["write_mode"] == "historical_log_only"
    assert record["repair"] == {"history": "updated", "answer": "not-requested"}


@pytest.mark.parametrize("commit", [False, True])
def test_main_report_covers_every_written_score_including_exact_names_and_clamps(database, monkeypatch, tmp_path, commit):
    factory, engine = database
    original = {"judgeComment": "original", "judgeLog": [
        {"name": "A", "score": 10, "jScore": 8},
        {"name": "Criterion B (reworded)", "score": 10, "jScore": 2},
        {"name": "Unrelated", "score": 10, "jScore": 11},
        {"name": "Tiny criterion (reworded)", "score": 10, "jScore": 0.5},
    ]}
    source = {"judgeLog": [{"name": "A", "score": 10, "jScore": 1},
                           {"name": "Criterion B", "score": 10, "jScore": 7},
                           {"name": "Tiny criterion", "score": 10, "jScore": 0.5000001}]}
    with factory.begin() as session:
        session.query(AutoTaskRunLog).get(2).content = json.dumps(original)
        session.query(ProblemSetAnswerSheetDetail).one().judgeLog = json.dumps(original["judgeLog"])
        session.add(AutoTaskRunLog(log_id=3, task_id="old", tag="log",
                    content="<schema>" + json.dumps(source) + "</schema>"))
    report = tmp_path / "score-diff.jsonl"
    monkeypatch.setattr(repair, "parse_args", lambda: SimpleNamespace(
        group_id=[100], username=None, task_id=None, limit=0, only_auto=False,
        min_similarity=0.7, commit=commit, update_current_answer=False, report=str(report)))
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    assert repair.main() == 0
    items = json.loads(report.read_text())["inconsistent_items"]
    reported = [(item["result_index"], item["old_result_jScore"], item["new_result_jScore"]) for item in items]
    assert reported == [(0, 8, 1), (1, 2, 7), (2, 11, 10), (3, 0.5, 0.5000001)]
    assert items[2]["matched_log_index"] is None
    history, answer, _ = state(factory)
    assert answer == original["judgeLog"]
    actual = [(i, old["jScore"], new["jScore"]) for i, (old, new) in
              enumerate(zip(original["judgeLog"], history["judgeLog"])) if old["jScore"] != new["jScore"]]
    assert actual == (reported if commit else [])


@pytest.mark.parametrize("other_id", ["aaa-later", "zzz-later"])
def test_main_preserves_current_answer_when_latest_run_timestamps_tie(database, monkeypatch, tmp_path, other_id):
    factory, engine = database
    original = payload(2)
    original["judgeLog"][0]["name"] = "Criterion A (reworded)"
    with factory.begin() as session:
        session.query(AutoTaskRunLog).get(2).content = json.dumps(original)
        session.query(ProblemSetAnswerSheetDetail).one().judgeLog = json.dumps(original["judgeLog"])
        session.add(AutoTaskRunLog(log_id=3, task_id="old", tag="log",
                    content="<schema>" + json.dumps(payload(7)) + "</schema>"))
        now = datetime(2026, 10, 16)
        session.add(AutoTaskRun(id=other_id, task_type="subjective_review", psid=1, username="alice",
                    groupId=100, problemId=3, status="success", create_time=now, end_time=now))
        session.add(AutoTaskRunLog(log_id=4, task_id=other_id, tag="payload",
                    content=json.dumps({"psid": 1, "gid": 2, "pid": 3, "username": "alice"})))
        session.add(AutoTaskRunLog(log_id=5, task_id=other_id, tag="result", content=json.dumps(original)))
    report = tmp_path / "tied-latest.jsonl"
    monkeypatch.setattr(repair, "parse_args", lambda: SimpleNamespace(
        group_id=[100], username=None, task_id=["old"], limit=0, only_auto=False,
        min_similarity=0.7, commit=True, update_current_answer=True, report=str(report)))
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    assert repair.main() == 0
    history, answer, _ = state(factory)
    assert history["judgeLog"][0]["jScore"] == 7
    assert answer == original["judgeLog"]
    with factory() as session:
        assert json.loads(session.query(AutoTaskRunLog).get(5).content) == original
    record = json.loads(report.read_text())
    assert record["repair"] == {"history": "updated", "answer": "not-latest-completed-run"}
