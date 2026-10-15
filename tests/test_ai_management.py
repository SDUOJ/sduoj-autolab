import json
import os
import time
from unittest.mock import Mock

from tests.test_ai_backend import database, client, ADMIN, USER, native_answer_fixture
from auto_task import store
from auto_task.entities import AiFile, AiResource, TaskJob
from server import auto_task_ai as api


def test_manual_ai_access_is_audited_idempotent_and_does_not_override_content_review(client):
    from server.auto_task_management import router
    from auto_task.entities import AiAccount, AiReview
    client.app.include_router(router, prefix="/auto-task/ai")
    body = {"subject_id": "42", "suspended": True, "operation_key": "hold", "reason": "Investigation requested"}
    path = "/auto-task/ai/admin/accounts/access"
    assert client.post(path, json=body).status_code == 403
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    assert client.post(path, json={**body, "subject_id": "7"}).status_code == 403
    assert client.post(path, json=body).json()["data"]["status"] == "suspended"
    assert client.post(path, json=body).json()["data"]["replay"]
    assert client.post(path, json={**body, "reason": "different"}).status_code == 409
    with store.transaction() as s:
        s.add(AiReview(subject_id="42", decision="violation", reason="Pending content review"))
        store.recalculate_access(s, "42", True)
        assert s.query(AiAccount).get("42").status == "suspended"
    restored = client.post(path, json={**body, "suspended": False, "operation_key": "release"}).json()["data"]
    assert restored["status"] == "suspended" and restored["reason"] == "Pending content review"
    with store.transaction() as s:
        s.query(AiReview).filter_by(decision="violation").one().decision = "normal"
        s.flush()
        store.recalculate_access(s, "42", True)
        assert s.query(AiAccount).get("42").status == "active"
    history = client.get("/auto-task/ai/admin/accounts/access-history?subject_id=42").json()["data"]
    assert len(history) == 2
    assert {entry["actor_id"] for entry in history} == {"7"}


def test_provider_health_has_no_key_or_provider_body(client, monkeypatch):
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    path = "/auto-task/ai/settings/health"
    assert client.get(path).json()["data"]["configured"] is False
    from auto_task.configuration import save_provider
    from tests.test_ai_backend import model_config
    with store.transaction() as s:
        save_provider(s, model_config(), "fixture-secret")
    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Mock(ok=True, status_code=200, json=lambda: {"data": [{"id": "test"}], "secret": "fixture-secret"}))
    response = client.get(path)
    assert response.json()["data"]["ready"]
    assert "fixture-secret" not in response.text
    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Mock(ok=True, status_code=200, json=lambda: []))
    assert not client.get(path).json()["data"]["ready"]
    client.app.dependency_overrides[api.cover_header] = lambda: USER
    assert client.get(path).status_code == 403


def test_tag_aliases_are_normalized_and_cannot_overlap(client):
    from server.auto_task_actions import router
    client.app.include_router(router, prefix="/auto-task/ai")
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    created = client.post("/auto-task/ai/tags/upsert", json={
        "name": "  数据结构  ", "aliases": ["DS", "data structures"],
    })
    assert created.status_code == 200, created.text
    assert created.json()["data"]["name"] == "数据结构"
    assert created.json()["data"]["aliases"] == ["DS", "data structures"]
    assert client.post("/auto-task/ai/tags/upsert", json={
        "name": "Other", "aliases": ["DS"],
    }).status_code == 409
    assert client.post("/auto-task/ai/tags/upsert", json={
        "name": "bad", "aliases": ["bad"],
    }).status_code == 400
    assert client.post("/auto-task/ai/tags/upsert", json={
        "name": "bad", "aliases": ["", "x"],
    }).status_code == 400


def test_teacher_edits_candidate_without_mutating_native_grade(client):
    from server.auto_task_management import router
    from auto_task.legacy_bridge import answer_snapshot
    from db import ProblemSetAnswerSheetDetail
    client.app.include_router(router, prefix="/auto-task/ai")
    native_answer_fixture(client)
    with store.transaction() as s:
        _, source = answer_snapshot(s, 1, 1, 1, "student")
        row = AiResource(id="grade", kind="grading", owner_id="7", owner_username="teacher", psid=1, data="{}")
        s.add(row); s.flush()
        job = store.submit(s, ADMIN, "subjective.grade", {}, "grade", row)
        job.status = "success"
        row.data = store.dumps({"candidate_job_id": job.id, "candidate": {"source": source, "judgeLog": [{"name": "correct", "score": 10, "jScore": 7}]}})
    path = "/auto-task/ai/grading/grade/edit"
    body = {"version": 1, "criteria": [{"name": "correct", "score": 9}], "comment": "checked", "reason": "Evidence review"}
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["total_score"] == 9
    with store.transaction() as s:
        assert not s.query(ProblemSetAnswerSheetDetail).get(1).judgeLog
        assert json.loads(s.query(AiResource).get("grade").data)["teacher_edits"][0]["actor_id"] == "7"
    assert client.post(path, json=body).status_code == 409
    assert client.post(path, json={**body, "version": 2, "criteria": [{"name": "correct", "score": 11}]}).status_code == 400


def test_resource_migration_dry_run_atomicity_replay_and_conflict(database):
    import pytest
    from auto_task.migrate import import_problem_stack
    export = {"resources": [{"id": "old-id", "owner_id": "old-user", "kind": "question", "data": {"zh": "Q"}}]}
    actors = {"old-user": USER}
    assert not import_problem_stack(export, actors)["applied"]
    with store.transaction() as s:
        assert s.query(AiResource).count() == 0
    assert import_problem_stack(export, actors, True, True)["applied"]
    assert import_problem_stack(export, actors, True, True)["resources"][0]["replay"]
    with pytest.raises(ValueError, match="source changed"):
        import_problem_stack({"resources": [{**export["resources"][0], "data": {"zh": "changed"}}]}, actors, True, True)
    with pytest.raises(ValueError, match="Resolve all"):
        import_problem_stack({"resources": [{"id": "valid-new", "owner_id": "old-user", "kind": "question", "data": {}}, {"id": "unknown", "kind": "question", "data": {}}]}, actors, True, True)
    with store.transaction() as s:
        assert s.query(AiResource).count() == 1


def test_admin_can_audit_orphans_and_missing_uploads_read_only(client):
    from auto_task.configuration import safe_path
    from server.auto_task_management import router

    client.app.include_router(router, prefix="/auto-task/ai")
    upload_root = safe_path("uploads")
    upload_root.mkdir(parents=True)
    registered = upload_root / "registered.txt"
    registered.write_bytes(b"registered")
    orphan = upload_root / "orphan.txt"
    orphan.write_bytes(b"orphan")
    recent = upload_root / "recent.txt"
    recent.write_bytes(b"recent")
    linked = upload_root / "linked.txt"
    linked.symlink_to(registered)
    old = time.time() - 7200
    os.utime(orphan, (old, old))
    with store.transaction() as s:
        s.add(AiFile(id="registered", owner_id="42", filename="registered.txt",
                     media_type="text/plain", path="uploads/registered.txt",
                     sha256="0" * 64, size=registered.stat().st_size + 1))
        s.add(AiFile(id="missing", owner_id="42", filename="missing.txt",
                     media_type="text/plain", path="uploads/missing.txt",
                     sha256="0" * 64, size=8))
        s.add(AiFile(id="outside", owner_id="42", filename="outside.txt",
                     media_type="text/plain", path="workspaces/outside.txt",
                     sha256="0" * 64, size=1))

    path = "/auto-task/ai/admin/storage/orphans?minimum_age_seconds=3600"
    assert client.get(path).status_code == 403
    assert client.post("/auto-task/ai/admin/storage/orphans/cleanup", json={}).status_code == 403
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    response = client.get(path)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["read_only"] is True
    assert result["orphan_file_count"] == 2
    assert result["eligible_orphan_file_count"] == 1
    assert {item["path"]: item["eligible"] for item in result["orphans"]} == {
        "uploads/orphan.txt": True, "uploads/recent.txt": False}
    assert result["missing_files"] == [{
        "id": "missing", "path": "uploads/missing.txt", "expected_size": 8}]
    assert result["metadata_mismatches"] == [{
        "id": "registered", "path": "uploads/registered.txt",
        "expected_size": 11, "actual_size": 10}]
    assert result["special_entries"] == ["uploads/linked.txt"]
    assert result["invalid_database_paths"] == [{
        "id": "outside", "path": "workspaces/outside.txt"}]
    assert orphan.exists() and recent.exists()


def test_orphan_cleanup_requires_apply_and_obeys_age_and_batch_limits(client):
    from auto_task.configuration import safe_path
    from server.auto_task_management import router

    client.app.include_router(router, prefix="/auto-task/ai")
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    upload_root = safe_path("uploads")
    upload_root.mkdir(parents=True)
    first = upload_root / "a-orphan.txt"
    second = upload_root / "b-orphan.txt"
    recent = upload_root / "recent.txt"
    first.write_bytes(b"1234")
    second.write_bytes(b"12345")
    recent.write_bytes(b"recent")
    old = time.time() - 7200
    os.utime(first, (old, old))
    os.utime(second, (old, old))
    endpoint = "/auto-task/ai/admin/storage/orphans/cleanup"

    preview = client.post(endpoint, json={"minimum_age_seconds": 3600,
                                          "max_files": 10, "max_bytes": 4})
    assert preview.status_code == 200, preview.text
    result = preview.json()["data"]
    assert result["applied"] is False
    assert result["planned_files"] == [{"path": "uploads/a-orphan.txt", "size": 4}]
    assert first.exists() and second.exists() and recent.exists()

    applied = client.post(endpoint, json={"apply": True, "minimum_age_seconds": 3600,
                                         "max_files": 1, "max_bytes": 4,
                                         "reason": "Remove crash leftovers"})
    assert applied.status_code == 200, applied.text
    result = applied.json()["data"]
    assert result["actor_id"] == "7"
    assert result["deleted_file_count"] == 1 and result["deleted_bytes"] == 4
    assert not first.exists()
    assert second.exists() and recent.exists()

    invalid = client.post(endpoint, json={"apply": True, "minimum_age_seconds": 0})
    assert invalid.status_code == 422
    assert client.post(endpoint, json={"apply": True}).status_code == 400
    assert recent.exists()


def test_orphan_cleanup_skips_file_replaced_after_scan(database, monkeypatch):
    from auto_task import assets
    from auto_task.configuration import safe_path

    upload_root = safe_path("uploads")
    upload_root.mkdir(parents=True)
    candidate = upload_root / "candidate.txt"
    candidate.write_bytes(b"orphan")
    old = time.time() - 7200
    os.utime(candidate, (old, old))
    with store.transaction() as s:
        report, candidates = assets.storage_consistency(s, minimum_age_seconds=3600)

    protected = safe_path("protected.txt")
    protected.write_bytes(b"keep")
    candidate.unlink()
    candidate.symlink_to(protected)
    monkeypatch.setattr(assets, "storage_consistency",
                        lambda *args, **kwargs: (report, candidates))
    with store.transaction() as s:
        result = assets.cleanup_orphan_uploads(
            s, apply=True, minimum_age_seconds=3600,
            max_files=1, max_bytes=100, reason="test race")
    assert result["deleted_file_count"] == 0
    assert result["skipped_files"] == [{
        "path": "uploads/candidate.txt", "reason": "file_changed_during_cleanup"}]
    assert candidate.is_symlink()
    assert protected.read_bytes() == b"keep"


def test_cleanup_registration_recheck_is_mysql_locking_current_read(database):
    from sqlalchemy.dialects.mysql import dialect
    from auto_task import assets

    with store.transaction() as s:
        query = assets._file_path_current_read(s, "uploads/candidate.txt")
        sql = str(query.statement.compile(dialect=dialect()))
    assert AiFile.__table__.c.path.unique
    assert "auto_task_file.path =" in sql
    assert sql.rstrip().endswith("FOR UPDATE")
