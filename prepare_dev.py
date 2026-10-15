"""Create missing model tables only in the local Autolab development database."""

import os

os.environ["SDUOJ_MODE"] = "debug"

from const import Mysql_addr, Mysql_db
from db import Base, link
from sqlalchemy import create_engine, inspect


def main():
    if Mysql_addr != "127.0.0.1:7360" or Mysql_db != "sduoj_ext_problem_set":
        raise RuntimeError("Refusing schema initialization outside local development")
    engine = create_engine(link)
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    missing = []
    for table in Base.metadata.tables.values():
        existing = {item["name"] for item in inspector.get_columns(table.name)}
        missing.extend(f"{table.name}.{column.name}" for column in table.columns if column.name not in existing)
    if missing:
        raise RuntimeError("Existing tables require a local migration: " + ", ".join(missing))
    print("Local Autolab schema ready; all model tables and columns exist")


if __name__ == "__main__":
    main()
