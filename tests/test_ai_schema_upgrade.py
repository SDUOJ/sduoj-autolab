"""Opt-in MySQL upgrade regressions; the database must be the disposable test DB."""
import os

import pytest
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db import AutoTaskRun, AutoTaskRunLog, ProblemSet, ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail
from auto_task import store
from auto_task.entities import AiRun, TABLES, TaskInput, TaskJob
from auto_task.migrate import _creation_tables, migrate


@pytest.fixture
def upgrade_database(monkeypatch):
    value = os.environ.get("AUTOLAB_AI_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Requires isolated AUTOLAB_AI_TEST_DATABASE_URL")
    url = make_url(value)
    assert url.host in ("127.0.0.1", "localhost") and url.database == "autolab_ai_test"
    engine = create_engine(value)
    assert engine.dialect.name == "mysql"
    targets, _, _ = _creation_tables(engine, set())
    metadata = targets[0].metadata
    metadata.drop_all(engine, tables=targets)
    # Native answer tables are fixture prerequisites, not AI migration targets.
    native = MetaData()
    for table in [ProblemSet.__table__, ProblemSetAnswerSheet.__table__, ProblemSetAnswerSheetDetail.__table__]:
        table.to_metadata(native)
    native.create_all(engine)
    monkeypatch.setattr(store, "_factory", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setenv("AUTOLAB_AI_WAKE", "0")
    yield engine
    metadata.drop_all(engine, tables=targets)
    native.drop_all(engine)
    engine.dispose()


def rules(engine):
    with engine.connect() as connection:
        return {(row[0], row[1]): row[2] for row in connection.execute(text(
            "SELECT TABLE_NAME,COLUMN_NAME,COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE()"))}


@pytest.mark.parametrize("legacy_logs", [True, False])
def test_upgrade_preserves_legacy_rules_history_and_task_joins(upgrade_database, legacy_logs):
    engine = upgrade_database
    # Production has a unicode_ci database and legacy 0900_ai_ci task tables.
    with engine.begin() as connection:
        connection.execute(text("ALTER DATABASE autolab_ai_test COLLATE utf8mb4_unicode_ci"))
    legacy = MetaData()
    for table in ([AutoTaskRun.__table__, AutoTaskRunLog.__table__] if legacy_logs else [AutoTaskRun.__table__]):
        cloned = table.to_metadata(legacy)
        cloned.dialect_options["mysql"].update(charset="utf8mb4", collate="utf8mb4_0900_ai_ci")
    legacy.create_all(engine)
    with store.transaction() as session:
        session.add(AutoTaskRun(id="history", task_type="summary_report", status="success"))
        session.flush()
        if legacy_logs:
            session.add(AutoTaskRunLog(task_id="history", tag="result", content='{"kept":true}'))
    before_tables = inspect(engine).get_table_names()
    before_rules = rules(engine)
    original_type = TaskJob.__table__.c.id.type
    plan = migrate()
    assert not plan["blocked"] and plan["apply_required"]
    assert any(item["column"] == "auto_task_ai_run.task_id"
               and "utf8mb4_0900_ai_ci" in item["type"] for item in plan["column_type_inheritance"])
    assert inspect(engine).get_table_names() == before_tables and rules(engine) == before_rules
    assert TaskJob.__table__.c.id.type is original_type
    migrate(apply=True)
    actual = rules(engine)
    for key, value in before_rules.items():
        assert actual[key] == value
    for table, column in [("auto_task_job", "id"), ("auto_task_input", "task_id"),
                          ("auto_task_ai_run", "task_id"), ("auto_task_ledger", "task_id"),
                          ("auto_task_review", "task_id"), ("auto_task_revision", "task_id")]:
        assert actual[table, column] == "utf8mb4_0900_ai_ci"
    assert actual["auto_task_run_log", "task_id"] == "utf8mb4_0900_ai_ci"
    assert actual["auto_task_job", "operation_key"] == "utf8mb4_bin"
    assert not migrate()["apply_required"] and not migrate()["blocked"]
    migrate(apply=True)
    with store.transaction() as session:
        assert session.query(AutoTaskRun).one().status == "success"
        if legacy_logs:
            assert session.query(AutoTaskRunLog).one().content == '{"kept":true}'
        else:
            session.add(AutoTaskRunLog(task_id="history", tag="result", content="new-log"))
        session.add(TaskJob(id="history", owner_id="42", owner_username="student", payer_id="42",
                            operation_key="Case", task_type="summary_report", payload="{}",
                            input_hash="hash", status="success"))
        session.flush()
        session.add(AiRun(id="usage", task_id="history", attempt=1, model="fixture", status="unsettled"))
        session.flush()
        assert store.has_unsettled_usage(session, "42")
        assert session.query(AiRun).join(TaskJob, TaskJob.id == AiRun.task_id).count() == 1
    with pytest.raises(IntegrityError):
        with store.transaction() as session:
            session.add(TaskInput(task_id="orphan", resource_version=1, resource_kind="fixture",
                                  content="{}", sha256="hash"))
            session.flush()


def test_fresh_schema_and_existing_conflict_are_safe(upgrade_database):
    engine = upgrade_database
    with engine.begin() as connection:
        connection.execute(text("ALTER DATABASE autolab_ai_test COLLATE utf8mb4_unicode_ci"))
    migrate(apply=True)
    assert not migrate()["apply_required"] and not migrate()["blocked"]
    assert rules(engine)["auto_task_job", "id"] == "utf8mb4_unicode_ci"
    # A pre-existing incompatible logical reference requires a separate review;
    # the additive migration must neither silently rewrite it nor create more DDL.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE auto_task_ai_run MODIFY task_id "
                                "VARCHAR(63) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL"))
    before = rules(engine)
    plan = migrate()
    assert plan["blocked"] and "auto_task_ai_run.task_id" in plan["incompatible_existing_columns"]
    with pytest.raises(ValueError, match="separate migration review"):
        migrate(apply=True)
    assert rules(engine) == before
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE auto_task_ai_run MODIFY task_id "
                                "VARCHAR(1) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL"))
    assert "auto_task_ai_run.task_id" in migrate()["incompatible_existing_columns"]
    with pytest.raises(ValueError, match="separate migration review"):
        migrate(apply=True)
