"""Explicit additive schema migration; never runs on Web startup."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from sqlalchemy import MetaData, String, inspect, text

from db import AutoTaskRun, AutoTaskRunLog
from .configuration import safe_path
from .entities import AiFile, AiFileLink, AiResource, AiSetting, TABLES, TaskJob, uid
from .store import account, digest, dumps, factory, principal, transaction


def _creation_tables(engine, existing):
    metadata = MetaData()
    originals = [AutoTaskRun.__table__, AutoTaskRunLog.__table__, *TABLES]
    tables = [table.to_metadata(metadata) for table in originals]
    # Resolve FKs through existing native tables in this private metadata graph.
    # Only `tables` below are migration targets; referenced parents are not added
    # to the create list and their schema is never changed by this migration.
    pending = list(originals)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            parent = foreign_key.column.table
            if parent.key not in metadata.tables:
                parent.to_metadata(metadata)
                pending.append(parent)
    inherited, conflicts = [], []
    if engine.dialect.name != "mysql":
        return tables, inherited, conflicts
    inspector = inspect(engine)
    # Reflection types alone omit a column's inherited table collation.
    with engine.connect() as connection:
        rules = {(row[0], row[1]): (row[2], row[3]) for row in connection.execute(text(
            "SELECT TABLE_NAME,COLUMN_NAME,CHARACTER_SET_NAME,COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"))}
    reflected = {}

    def actual_type(column):
        if column.table.name not in existing:
            return column.type
        if column.table.name not in reflected:
            reflected[column.table.name] = {item["name"]: item["type"]
                                             for item in inspector.get_columns(column.table.name)}
        value = reflected[column.table.name][column.name].copy()
        charset, collation = rules.get((column.table.name, column.name), (None, None))
        if isinstance(value, String):
            value.charset, value.collation = charset, collation
        return value

    def inherit(column, parent):
        value = actual_type(parent)
        if not isinstance(value, String):
            return
        if column.table.name in existing:
            current = actual_type(column)
            if (not isinstance(current, String)
                    or not getattr(current, "length", None)
                    or current.length < value.length
                    or getattr(current, "charset", None) != getattr(value, "charset", None)
                    or getattr(current, "collation", None) != getattr(value, "collation", None)):
                conflicts.append(str(column))
            return
        column.type = value.copy()
        inherited.append({"column": str(column), "parent": str(parent),
                          "type": column.type.compile(dialect=engine.dialect)})

    for table in metadata.sorted_tables:
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                inherit(column, foreign_key.column)
    # These task references deliberately have no FK, but participate in joins
    # (including budget and unsettled-usage checks) with TaskJob.id.
    task_id = metadata.tables[AutoTaskRun.__tablename__].c.id
    for name in ("auto_task_ai_run", "auto_task_ledger", "auto_task_review", "auto_task_revision"):
        inherit(metadata.tables[name].c.task_id, task_id)
    return tables, inherited, conflicts


def migrate(apply=False):
    engine = factory().kw["bind"]
    existing = set(inspect(engine).get_table_names())
    tables, inherited, conflicts = _creation_tables(engine, existing)
    missing = [table.name for table in tables if table.name not in existing]
    missing_indexes = []
    if AiResource.__tablename__ in existing and "ft_ai_resource_search" not in {index["name"] for index in inspect(engine).get_indexes(AiResource.__tablename__)}:
        missing_indexes.append("ft_ai_resource_search")
    if not apply:
        return {"missing_tables": missing, "missing_indexes": missing_indexes,
                "column_type_inheritance": inherited, "incompatible_existing_columns": conflicts,
                "blocked": bool(conflicts), "apply_required": bool(missing or missing_indexes)}
    if conflicts:
        raise ValueError("Existing task key types require separate migration review: " + ", ".join(conflicts))
    for table in tables:
        table.create(engine, checkfirst=True)
    with transaction() as s:
        if not s.query(AiSetting).filter_by(id="runtime").first():
            s.add(AiSetting(id="runtime", data=json.dumps({"max_concurrency": 4, "per_user_concurrency": 1})))
    if engine.dialect.name == "mysql":
        indexes = {item["name"] for item in inspect(engine).get_indexes(AiResource.__tablename__)}
        if "ft_ai_resource_search" not in indexes:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE auto_task_resource ADD FULLTEXT INDEX ft_ai_resource_search (search_text) WITH PARSER ngram"))
    return {"created": missing}


def import_legacy(actor_map, apply=False, workers_stopped=False):
    """Pending legacy tasks need explicit actor attribution, never the target student's identity."""
    if apply and not workers_stopped:
        raise ValueError("Stop all legacy consumers and acknowledge --workers-stopped before importing")
    existing = set(inspect(factory().kw["bind"]).get_table_names())
    if AutoTaskRun.__tablename__ not in existing:
        return {"tasks": [], "unresolved_task_ids": [], "applied": False}
    with transaction() as s:
        rows = s.query(AutoTaskRun).filter(AutoTaskRun.status.in_(["pending", "running"])).all()
        planned, unresolved = [], []
        for row in rows:
            if TaskJob.__tablename__ in existing and s.query(TaskJob).filter_by(id=row.id).first():
                continue
            payload_log = s.query(AutoTaskRunLog).filter_by(task_id=row.id, tag="payload").order_by(AutoTaskRunLog.log_id.desc()).first()
            payload = json.loads(payload_log.content) if payload_log else None
            actor = actor_map.get(row.id)
            if not payload or not actor:
                unresolved.append(row.id)
                continue
            subject = principal(actor)
            planned.append({"id": row.id, "type": row.task_type, "owner_id": subject})
            if apply:
                account(s, subject, actor["username"])
                payload["_actor"] = {"userId": subject, "username": actor["username"]}
                s.add(TaskJob(id=row.id, owner_id=subject, owner_username=actor["username"], payer_id=subject,
                              operation_key="legacy:" + row.id, task_type=row.task_type, psid=row.psid,
                              payload=dumps(payload), input_hash=digest(payload), status="pending"))
                row.status = "pending"
                s.add(AutoTaskRunLog(task_id=row.id, tag="log", content=dumps({"event": "durable_import", "actor": subject})))
        if apply and unresolved:
            raise ValueError("Resolve all legacy actor mappings before applying the import")
        return {"tasks": planned, "unresolved_task_ids": unresolved, "applied": apply}


def _migration_resource_id(source_id):
    return digest({"problem_stack_resource": source_id})[:32]


def _migration_file_id(source_id):
    return digest({"problem_stack_asset": source_id})[:32]


def _safe_asset_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Unsafe migration asset path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe migration asset path: " + value)
    return path


def _migration_file_sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _forbidden_migration_keys(value, location="resource"):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold().replace("_", "").replace("-", "")
            forbidden = ("password" in normalized or "apikey" in normalized or "secret" in normalized
                         or normalized in {"token", "accesstoken", "accesstokenhash", "refreshtoken",
                                           "sessiontoken", "bearertoken"})
            if forbidden and item not in (None, "", [], {}):
                raise ValueError("Forbidden credential field in migration bundle: {}.{}".format(location, key))
            _forbidden_migration_keys(item, "{}.{}".format(location, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _forbidden_migration_keys(item, "{}[{}]".format(location, index))


def _assert_local_problem_stack_apply():
    engine = factory().kw["bind"]
    if engine.dialect.name == "sqlite" and os.environ.get("PYTEST_CURRENT_TEST"):
        return
    url = engine.url
    if os.environ.get("SDUOJ_MODE") != "debug" or url.host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Problem Stack apply is allowed only for a loopback SDUOJ_MODE=debug database")


def _resolve_migration_links(value, resource_ids, file_ids):
    result = copy.deepcopy(value)
    for key in ("question_id",):
        if result.get(key):
            source_id = str(result[key])
            if source_id not in resource_ids:
                raise ValueError("Unknown migrated resource reference: " + source_id)
            result[key] = resource_ids[source_id]
    if "question_ids" in result:
        if not isinstance(result["question_ids"], list):
            raise ValueError("question_ids must be a list")
        missing = [str(item) for item in result["question_ids"] if str(item) not in resource_ids]
        if missing:
            raise ValueError("Unknown migrated question reference: " + missing[0])
        result["question_ids"] = [resource_ids[str(item)] for item in result["question_ids"]]
    if "file_ids" in result:
        if not isinstance(result["file_ids"], list):
            raise ValueError("file_ids must be a list")
        missing = [str(item) for item in result["file_ids"] if str(item) not in file_ids]
        if missing:
            raise ValueError("Unknown migrated asset reference: " + missing[0])
        result["file_ids"] = [file_ids[str(item)] for item in result["file_ids"]]
    candidate = result.get("candidate")
    if isinstance(candidate, dict) and candidate.get("legacy_report_file_id"):
        source_id = str(candidate["legacy_report_file_id"])
        if source_id not in file_ids:
            raise ValueError("Unknown migrated report asset: " + source_id)
        candidate["legacy_report_file_id"] = file_ids[source_id]
    for item in result.get("figures", []):
        if isinstance(item, dict) and "asset_ids" in item:
            missing = [str(asset) for asset in item["asset_ids"] if str(asset) not in file_ids]
            if missing:
                raise ValueError("Unknown migrated figure asset: " + missing[0])
            item["asset_ids"] = [file_ids[str(asset)] for asset in item["asset_ids"]]
    for item in result.get("media_segments", []):
        if isinstance(item, dict) and item.get("file_id"):
            source_id = str(item["file_id"])
            if source_id not in file_ids:
                raise ValueError("Unknown migrated media asset: " + source_id)
            item["file_id"] = file_ids[source_id]
    for item in result.get("problem_numbers", []):
        if isinstance(item, dict) and item.get("question_id"):
            source_id = str(item["question_id"])
            if source_id not in resource_ids:
                raise ValueError("Unknown migrated problem number reference: " + source_id)
            item["question_id"] = resource_ids[source_id]
    return result


def import_problem_stack(export, actor_map, apply=False, workers_stopped=False, asset_root=None):
    """Plan/import a neutral Problem Stack JSON export into AiResource.

    The source database is never queried here. Each record must carry a stable
    source id, kind, JSON data, and the verified OJ actor who owns the record.
    ``apply`` requires old consumers to be stopped and is idempotent by the
    source marker. Files remain references until a separate, checksummed asset
    copy is approved; silently copying arbitrary paths would break provenance.
    """
    if apply and not workers_stopped:
        raise ValueError("Stop all legacy consumers and acknowledge --workers-stopped before importing")
    if apply:
        _assert_local_problem_stack_apply()
    if not isinstance(export, dict) or not isinstance(export.get("resources"), list):
        raise ValueError("Expected an export with a resources list")
    if export.get("format") not in (None, "problem-stack-neutral-v1"):
        raise ValueError("Unsupported Problem Stack migration bundle")
    report = export.get("report", {})
    if export.get("format") and (not isinstance(report, dict) or not report.get("ready_to_import")):
        raise ValueError("Problem Stack conversion audit is not ready to import")
    if not isinstance(export.get("assets", []), list) or not isinstance(export.get("audits", {}), dict):
        raise ValueError("Invalid Problem Stack asset or audit section")
    _forbidden_migration_keys(export.get("resources", []))
    _forbidden_migration_keys(export.get("assets", []), "assets")
    source_rows = {}
    for source in export["resources"]:
        if isinstance(source, dict) and source.get("id"):
            source_id = str(source["id"])
            if source_id in source_rows:
                raise ValueError("Duplicate source resource id: " + source_id)
            source_rows[source_id] = source
    resource_ids = {source_id: _migration_resource_id(source_id) for source_id in source_rows}
    if len(set(resource_ids.values())) != len(resource_ids):
        raise ValueError("Deterministic resource ID collision")
    assets = {}
    for asset in export.get("assets", []):
        if not isinstance(asset, dict) or not asset.get("id"):
            raise ValueError("Invalid migration asset")
        asset_id = str(asset["id"])
        if asset_id in assets:
            raise ValueError("Duplicate migration asset id: " + asset_id)
        if not isinstance(asset.get("sha256"), str) or len(asset["sha256"]) != 64 or not all(c in "0123456789abcdef" for c in asset["sha256"]):
            raise ValueError("Invalid migration asset checksum: " + asset_id)
        if not isinstance(asset.get("size"), int) or asset["size"] < 0:
            raise ValueError("Invalid migration asset size: " + asset_id)
        assets[asset_id] = asset
    file_ids = {asset_id: _migration_file_id(asset_id) for asset_id in assets}
    if len(set(file_ids.values())) != len(file_ids):
        raise ValueError("Deterministic asset ID collision")
    resolved_asset_root = None
    if assets:
        if asset_root is None:
            raise ValueError("Verified asset root is required for this migration bundle")
        if Path(asset_root).expanduser().is_symlink():
            raise ValueError("Migration asset root may not be a symbolic link")
        resolved_asset_root = Path(asset_root).expanduser().resolve(strict=True)
        if not resolved_asset_root.is_dir():
            raise ValueError("Migration asset root must be a directory")
    planned, unresolved, seen = [], [], set()
    created_paths = []
    try:
        with transaction() as s:
            for source in export["resources"]:
                if not isinstance(source, dict) or not source.get("id") or not isinstance(source.get("data"), dict):
                    unresolved.append(str(source.get("id", "")) if isinstance(source, dict) else "")
                    continue
                source_id = str(source["id"])
                if source_id in seen:
                    raise ValueError("Duplicate source resource id: " + source_id)
                seen.add(source_id)
                actor = actor_map.get(source_id) or actor_map.get(str(source.get("owner_id", "")))
                if not actor:
                    unresolved.append(source_id)
                    continue
                subject = principal(actor)
                kind = str(source.get("kind", ""))
                if kind not in {"question", "collection", "authoring", "grading", "plagiarism", "ide", "course", "document", "conversation", "interview", "exam"}:
                    raise ValueError("Unsupported exported resource kind: " + kind)
                if source.get("course_id") is not None and source.get("psid") is not None:
                    raise ValueError("Migrated resource cannot bind both course and problem set")
                parent_source = source.get("parent_source_id")
                if parent_source and str(parent_source) not in resource_ids:
                    raise ValueError("Unknown migrated parent resource: " + str(parent_source))
                marker = "problem-stack:" + source_id
                marker_key = digest({"migration_source": marker})
                source_hash = digest(source)
                existing = s.query(AiResource).filter_by(canonical_key=marker_key).first()
                target_id = resource_ids[source_id]
                collision = s.query(AiResource).filter_by(id=target_id).first()
                if collision and (not existing or collision.id != existing.id):
                    raise ValueError("Deterministic resource ID collision: " + source_id)
                if existing:
                    mapped_parent = resource_ids[str(parent_source)] if parent_source else None
                    if (existing.id != target_id or existing.owner_id != subject
                            or existing.owner_username != actor["username"]
                            or existing.course_id != source.get("course_id") or existing.psid != source.get("psid")
                            or existing.parent_id != mapped_parent):
                        raise ValueError("Previously imported identity or scope mapping changed: " + source_id)
                    if json.loads(existing.data).get("migration", {}).get("source_hash") != source_hash:
                        raise ValueError("Previously imported source changed: " + source_id)
                planned.append({"source_id": source_id, "resource_id": target_id, "kind": kind,
                                "owner_id": subject, "replay": bool(existing)})
                if apply and not existing:
                    value = _resolve_migration_links(source["data"], resource_ids, file_ids)
                    value["migration"] = {"source": marker, "source_hash": source_hash,
                                          "source_updated_at": source.get("updated_at"),
                                          "source_file_ids": sorted(asset_id for asset_id, asset in assets.items()
                                                                    if source_id in asset.get("resource_source_ids", []))}
                    row = AiResource(id=target_id, kind=kind, owner_id=subject, owner_username=actor["username"],
                                     course_id=source.get("course_id"), psid=source.get("psid"),
                                     parent_id=resource_ids[str(parent_source)] if parent_source else None,
                                     data=dumps(value), search_text=str(value.get("title", value.get("zh", value.get("text", ""))))[:10000],
                                     canonical_key=marker_key, state=str(source.get("state") or "draft")[:24])
                    s.add(row)
            if apply and unresolved:
                raise ValueError("Resolve all exported actor mappings before applying: " + ",".join(unresolved[:20]))
            if apply:
                for asset_id, asset in sorted(assets.items()):
                    relative = _safe_asset_relative(asset.get("bundle_path"))
                    source_path = resolved_asset_root.joinpath(*relative.parts)
                    resolved_source = source_path.resolve(strict=True)
                    if resolved_asset_root not in resolved_source.parents or source_path.is_symlink() or not resolved_source.is_file():
                        raise ValueError("Migration asset escaped its approved root: " + asset_id)
                    actual_size = resolved_source.stat().st_size
                    actual_hash = _migration_file_sha256(resolved_source)
                    if actual_size != asset["size"] or actual_hash != asset["sha256"]:
                        raise ValueError("Migration asset checksum mismatch: " + asset_id)
                    file_id = file_ids[asset_id]
                    suffix = str(asset.get("suffix") or Path(str(asset.get("filename", ""))).suffix).lower()
                    if suffix and (not suffix.startswith(".") or not suffix[1:].isalnum() or len(suffix) > 12):
                        raise ValueError("Invalid migration asset suffix: " + asset_id)
                    relative_target = "uploads/migration/{}{}".format(file_id, suffix)
                    target = safe_path(relative_target)
                    owners = {resource_ids[source_id]: source_rows[source_id] for source_id in asset.get("resource_source_ids", [])
                              if source_id in resource_ids}
                    owner_source = next(iter(owners.values()), None)
                    actor = ((actor_map.get(str(owner_source.get("id"))) or
                              actor_map.get(str(owner_source.get("owner_id")))) if owner_source else None)
                    if not actor:
                        raise ValueError("Migration asset has no mapped owning resource: " + asset_id)
                    expected_owner_id = principal(actor)
                    existing_file = s.query(AiFile).filter_by(id=file_id).first()
                    if existing_file:
                        if (existing_file.sha256 != actual_hash or existing_file.size != actual_size
                                or existing_file.path != relative_target or existing_file.owner_id != expected_owner_id):
                            raise ValueError("Previously imported asset changed: " + asset_id)
                        if not target.is_file() or target.stat().st_size != actual_size or _migration_file_sha256(target) != actual_hash:
                            raise ValueError("Previously imported asset file is missing or changed: " + asset_id)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with resolved_source.open("rb") as input_stream, target.open("xb") as output_stream:
                            while True:
                                chunk = input_stream.read(1024 * 1024)
                                if not chunk:
                                    break
                                output_stream.write(chunk)
                        target.chmod(0o600)
                        if target.stat().st_size != actual_size or _migration_file_sha256(target) != actual_hash:
                            raise ValueError("Copied migration asset checksum mismatch: " + asset_id)
                        created_paths.append(target)
                        s.add(AiFile(id=file_id, owner_id=expected_owner_id, resource_id=None,
                                     filename=Path(str(asset.get("filename") or file_id)).name[:255],
                                     media_type=str(asset.get("media_type") or "application/octet-stream")[:128],
                                     path=relative_target, sha256=actual_hash, size=actual_size,
                                     sequence_key="migration:" + digest(asset_id)))
                    for source_id in asset.get("resource_source_ids", []):
                        if source_id not in resource_ids:
                            raise ValueError("Migration asset references an unknown resource: " + str(source_id))
                        target_resource = resource_ids[source_id]
                        if not s.query(AiFileLink).filter_by(file_id=file_id, resource_id=target_resource).first():
                            s.add(AiFileLink(file_id=file_id, resource_id=target_resource))
            return {"resources": planned, "unresolved_source_ids": unresolved, "applied": apply,
                    "assets": [{"source_id": key, "file_id": file_ids[key]} for key in sorted(file_ids)],
                    "audit_only": {key: len(value) for key, value in export.get("audits", {}).items()},
                    "ledger_mutations": 0, "task_requeues": 0}
    except BaseException:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Explicitly create additive tables in the configured Autolab database")
    parser.add_argument("--legacy-map", help="JSON file mapping legacy task IDs to verified SDUOJ userId/username")
    parser.add_argument("--problem-stack-export", help="Neutral JSON from scripts/convert_problem_stack_export.py")
    parser.add_argument("--identity-map", help="JSON mapping legacy Problem Stack identities to verified SDUOJ userId/username")
    parser.add_argument("--asset-root", help="Approved root containing the neutral bundle's assets/ directory")
    parser.add_argument("--workers-stopped", action="store_true")
    args = parser.parse_args()
    if args.problem_stack_export and args.apply:
        _assert_local_problem_stack_apply()
    print(json.dumps(migrate(args.apply)))
    if args.legacy_map:
        from pathlib import Path
        print(json.dumps(import_legacy(json.loads(Path(args.legacy_map).read_text()), args.apply, args.workers_stopped)))
    if args.problem_stack_export:
        if not args.identity_map:
            raise SystemExit("--identity-map is required with --problem-stack-export")
        print(json.dumps(import_problem_stack(json.loads(Path(args.problem_stack_export).read_text()),
                                              json.loads(Path(args.identity_map).read_text()), args.apply,
                                              args.workers_stopped, args.asset_root)))
