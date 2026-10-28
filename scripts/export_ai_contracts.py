"""Generate reviewable API/DDL artifacts without connecting to a database."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.ddl import sort_tables
from auto_task.entities import TABLES
from server import auto_task_ai, auto_task_actions, auto_task_native, auto_task_management, auto_task_interviews, auto_task_exams
from server import auto_task_questions, auto_task_authoring
from server import auto_task_collaboration


def artifacts():
    app = FastAPI(title="Autolab AI API", version="1.0")
    app.include_router(auto_task_ai.router)
    app.include_router(auto_task_actions.router, prefix="/auto-task/ai")
    app.include_router(auto_task_native.router, prefix="/auto-task/ai")
    app.include_router(auto_task_management.router, prefix="/auto-task/ai")
    app.include_router(auto_task_interviews.router, prefix="/auto-task/ai")
    app.include_router(auto_task_exams.router, prefix="/auto-task/ai")
    app.include_router(auto_task_questions.router, prefix="/auto-task/ai")
    app.include_router(auto_task_authoring.router, prefix="/auto-task/ai")
    app.include_router(auto_task_collaboration.router, prefix="/auto-task/ai")
    schema = app.openapi()
    dialect = mysql.dialect()
    statements = ["-- Review-only MySQL 8 schema. Use python -m auto_task.migrate --apply for idempotent application."]
    for table in sort_tables(TABLES):
        statements.append(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda index: index.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return {"docs/auto-task-ai-openapi.json": json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            "docs/sql/20260905_auto_task_ai.sql": "\n\n".join(statements) + "\n"}, len(schema["paths"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    outputs, count = artifacts()
    for relative, content in outputs.items():
        target = root / relative
        if args.write:
            target.write_text(content, encoding="utf8")
        elif not target.exists() or target.read_text(encoding="utf8") != content:
            raise SystemExit("Stale artifact: " + relative)
    print("AI contract: {} paths; {} extension tables; {}".format(count, len(TABLES), "written" if args.write else "verified"))
