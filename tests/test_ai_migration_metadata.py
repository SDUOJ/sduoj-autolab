"""Migration metadata resolves native FK parents without selecting their DDL."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from auto_task import migrate
from auto_task.entities import TABLES
from db import AutoTaskRun, AutoTaskRunLog, Base


NATIVE_PARENTS = {'problem_set', 'problem_set_answer_sheet', 'problem_set_answer_sheet_detail'}


def assert_metadata_scope(tables):
    selected = [AutoTaskRun.__table__, AutoTaskRunLog.__table__, *TABLES]
    assert [table.name for table in tables] == [table.name for table in selected]
    metadata = tables[0].metadata
    assert NATIVE_PARENTS <= set(metadata.tables)
    assert not NATIVE_PARENTS.intersection(table.name for table in tables)
    assert {table.name for table in metadata.sorted_tables} == set(metadata.tables)
    for table in metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.metadata is metadata
    original = Base.metadata.tables['subjective_grade_review']
    copied = metadata.tables['subjective_grade_review']
    assert str(CreateTable(copied).compile(dialect=mysql.dialect())) == str(
        CreateTable(original).compile(dialect=mysql.dialect()))
    assert copied is not original


def test_creation_metadata_resolves_native_parent_closure_without_database_calls():
    engine = create_engine('sqlite://')
    from sqlalchemy import event

    @event.listens_for(engine, 'connect')
    def unexpected_database_call(*_):
        pytest.fail('Pure metadata construction must not connect to a database')

    try:
        tables, inherited, conflicts = migrate._creation_tables(engine, set())
        assert inherited == conflicts == []
        assert_metadata_scope(tables)
    finally:
        engine.dispose()


def test_mysql_metadata_resolves_parents_but_keeps_migration_targets(monkeypatch):
    inspected = []

    class Inspector:
        def get_columns(self, name):
            inspected.append(name)
            return [{'name': column.name, 'type': column.type.copy()}
                    for column in Base.metadata.tables[name].columns]

    class Engine:
        dialect = mysql.dialect()

        @contextmanager
        def connect(self):
            # Only the information-schema read is represented; no real DB is used.
            class Connection:
                def execute(self, statement):
                    assert 'information_schema.COLUMNS' in str(statement)
                    return []
            yield Connection()

    monkeypatch.setattr(migrate, 'inspect', lambda _: Inspector())
    before = {name: id(table) for name, table in Base.metadata.tables.items()}
    tables, inherited, conflicts = migrate._creation_tables(Engine(), NATIVE_PARENTS)
    assert conflicts == []
    assert inherited
    assert_metadata_scope(tables)
    assert {name: id(table) for name, table in Base.metadata.tables.items()} == before
    assert set(inspected) <= NATIVE_PARENTS


def test_apply_never_creates_metadata_only_native_parents(monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import Table

    engine = SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    monkeypatch.setattr(migrate, 'factory', lambda: SimpleNamespace(kw={'bind': engine}))
    monkeypatch.setattr(migrate, 'inspect', lambda _: SimpleNamespace(
        get_table_names=lambda: list(NATIVE_PARENTS)))
    created = []
    monkeypatch.setattr(Table, 'create', lambda table, *_args, **_kwargs: created.append(table.name))

    @contextmanager
    def transaction():
        yield SimpleNamespace(query=lambda *_: SimpleNamespace(
            filter_by=lambda **_: SimpleNamespace(first=lambda: object())))

    monkeypatch.setattr(migrate, 'transaction', transaction)
    result = migrate.migrate(apply=True)
    expected = [table.name for table in [AutoTaskRun.__table__, AutoTaskRunLog.__table__, *TABLES]]
    assert created == expected
    assert result['created'] == expected
    assert not NATIVE_PARENTS.intersection(created)
