"""Create only the additive metadata table used for native grade reviews."""

from sqlalchemy import inspect

from auto_task.store import factory
from model.grade_review import SubjectiveGradeReview


def migrate(apply=False):
    engine = factory().kw["bind"]
    table = SubjectiveGradeReview.__table__
    existed = table.name in inspect(engine).get_table_names()
    if not existed and not apply:
        return {"table": table.name, "created": False, "apply_required": True, "columns": 0}
    if apply:
        table.create(engine, checkfirst=True)
    actual = {column["name"] for column in inspect(engine).get_columns(table.name)}
    missing = {column.name for column in table.columns} - actual
    if missing:
        raise RuntimeError("Review metadata migration is incomplete")
    return {"table": table.name, "created": not existed, "apply_required": False, "columns": len(table.columns)}


if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, sort_keys=True))
