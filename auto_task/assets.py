"""Validated assets plus bounded reconciliation of upload storage."""

import hashlib
import logging
import os
import stat
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .configuration import safe_path
from .entities import uid


logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_GRACE_SECONDS = 24 * 60 * 60
MIN_ORPHAN_GRACE_SECONDS = 60 * 60
MAX_ORPHAN_GRACE_SECONDS = 90 * 24 * 60 * 60
MAX_ORPHAN_SCAN_RESULTS = 1000
MAX_ORPHAN_CLEANUP_FILES = 1000
MAX_ORPHAN_CLEANUP_BYTES = 1024 * 1024 * 1024


def _utc_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _upload_files():
    """Return regular upload files without following filesystem links."""
    root = safe_path("uploads")
    files, special = [], []
    if not root.exists():
        return root, files, special
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        retained = []
        for name in dirnames:
            path = os.path.join(directory, name)
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(mode):
                retained.append(name)
            else:
                special.append(path)
        dirnames[:] = retained
        for name in filenames:
            path = os.path.join(directory, name)
            try:
                details = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(details.st_mode):
                files.append((path, details))
            else:
                special.append(path)
    return root, files, special


def _relative_upload_path(root, value):
    return value.resolve(strict=False).relative_to(root.parent).as_posix()


def _file_path_current_read(session, path):
    """Lock the unique file-path key using an InnoDB current read."""
    from .entities import AiFile

    return session.query(AiFile.id).filter_by(path=path).with_for_update()


def storage_consistency(session, minimum_age_seconds=DEFAULT_ORPHAN_GRACE_SECONDS,
                        result_limit=100, now=None):
    """Build a read-only database/filesystem consistency report.

    The age gate keeps uploads that may still be in flight out of cleanup plans.
    Hashing every registered upload would turn this operational check into an
    unbounded I/O job, so metadata consistency is intentionally size-based.
    """
    from .entities import AiFile

    if not MIN_ORPHAN_GRACE_SECONDS <= minimum_age_seconds <= MAX_ORPHAN_GRACE_SECONDS:
        raise ValueError("Invalid orphan grace period")
    if not 1 <= result_limit <= MAX_ORPHAN_SCAN_RESULTS:
        raise ValueError("Invalid orphan result limit")
    checked_at = float(now if now is not None else time.time())
    root, disk_entries, special_entries = _upload_files()
    disk = {}
    for path, details in disk_entries:
        try:
            relative = _relative_upload_path(root, Path(path))
        except (OSError, RuntimeError, ValueError):
            special_entries.append(path)
            continue
        disk[relative] = details

    registered = {}
    invalid_database_paths = []
    for row in session.query(AiFile).all():
        try:
            target = (root.parent / row.path).resolve(strict=False)
            relative = target.relative_to(root.parent).as_posix()
        except (OSError, RuntimeError, ValueError):
            invalid_database_paths.append({"id": row.id, "path": row.path})
            continue
        if relative == "uploads" or not relative.startswith("uploads/"):
            invalid_database_paths.append({"id": row.id, "path": row.path})
            continue
        registered[relative] = row

    orphan_records = []
    candidates = []
    for relative, details in disk.items():
        if relative in registered:
            continue
        age = max(0, int(checked_at - details.st_mtime))
        eligible = age >= minimum_age_seconds
        public = {"path": relative, "size": details.st_size,
                  "modified_at": _utc_timestamp(details.st_mtime),
                  "age_seconds": age, "eligible": eligible}
        internal = {**public, "device": details.st_dev, "inode": details.st_ino,
                    "mtime_ns": details.st_mtime_ns}
        orphan_records.append(public)
        if eligible:
            candidates.append(internal)

    missing = []
    mismatches = []
    for relative, row in registered.items():
        details = disk.get(relative)
        if details is None:
            missing.append({"id": row.id, "path": relative, "expected_size": row.size})
        elif details.st_size != row.size:
            mismatches.append({"id": row.id, "path": relative,
                               "expected_size": row.size, "actual_size": details.st_size})

    orphan_records.sort(key=lambda item: (item["modified_at"], item["path"]))
    candidates.sort(key=lambda item: (item["mtime_ns"], item["path"]))
    missing.sort(key=lambda item: item["path"])
    mismatches.sort(key=lambda item: item["path"])
    invalid_database_paths.sort(key=lambda item: item["id"])
    special = []
    for value in special_entries:
        try:
            special.append(Path(value).relative_to(root.parent).as_posix())
        except (OSError, RuntimeError, ValueError):
            continue
    special.sort()

    report = {
        "read_only": True,
        "scanned_at": _utc_timestamp(checked_at),
        "minimum_age_seconds": minimum_age_seconds,
        "disk_file_count": len(disk),
        "registered_file_count": len(registered),
        "orphan_file_count": len(orphan_records),
        "orphan_bytes": sum(item["size"] for item in orphan_records),
        "eligible_orphan_file_count": len(candidates),
        "eligible_orphan_bytes": sum(item["size"] for item in candidates),
        "missing_file_count": len(missing),
        "metadata_mismatch_count": len(mismatches),
        "special_entry_count": len(special),
        "invalid_database_path_count": len(invalid_database_paths),
        "orphans": orphan_records[:result_limit],
        "missing_files": missing[:result_limit],
        "metadata_mismatches": mismatches[:result_limit],
        "special_entries": special[:result_limit],
        "invalid_database_paths": invalid_database_paths[:result_limit],
        "results_truncated": any(len(items) > result_limit for items in
                                 (orphan_records, missing, mismatches, special, invalid_database_paths)),
    }
    return report, candidates


def cleanup_orphan_uploads(session, apply=False,
                           minimum_age_seconds=DEFAULT_ORPHAN_GRACE_SECONDS,
                           max_files=100, max_bytes=100 * 1024 * 1024,
                           result_limit=100, now=None, actor_id=None, reason=""):
    """Plan or apply one bounded cleanup batch of old unregistered uploads."""
    from .entities import AiFile

    if not 1 <= max_files <= MAX_ORPHAN_CLEANUP_FILES:
        raise ValueError("Invalid orphan file cleanup limit")
    if not 1 <= max_bytes <= MAX_ORPHAN_CLEANUP_BYTES:
        raise ValueError("Invalid orphan byte cleanup limit")
    report, candidates = storage_consistency(
        session, minimum_age_seconds=minimum_age_seconds,
        result_limit=result_limit, now=now)
    selected, selected_bytes = [], 0
    for item in candidates:
        if len(selected) >= max_files:
            break
        if selected_bytes + item["size"] > max_bytes:
            continue
        selected.append(item)
        selected_bytes += item["size"]

    deleted, skipped = [], []
    if apply:
        upload_root = safe_path("uploads")
        for item in selected:
            # Recheck both authorities immediately before unlinking. The inode
            # identity check prevents deleting a replacement at the same path.
            # FOR UPDATE is a current read under MySQL REPEATABLE READ. On the
            # unique path index it also holds the missing-key gap until commit,
            # closing the scan/register/unlink race across worker processes.
            if _file_path_current_read(session, item["path"]).first():
                skipped.append({"path": item["path"], "reason": "registered_during_cleanup"})
                continue
            try:
                target = upload_root / Path(item["path"]).relative_to("uploads")
                details = target.lstat()
                identity = (details.st_dev, details.st_ino, details.st_mtime_ns, details.st_size)
                expected = (item["device"], item["inode"], item["mtime_ns"], item["size"])
                if not stat.S_ISREG(details.st_mode) or identity != expected:
                    skipped.append({"path": item["path"], "reason": "file_changed_during_cleanup"})
                    continue
                target.unlink()
                deleted.append({"path": item["path"], "size": item["size"]})
            except FileNotFoundError:
                skipped.append({"path": item["path"], "reason": "already_missing"})
            except (RuntimeError, ValueError):
                skipped.append({"path": item["path"], "reason": "file_changed_during_cleanup"})
            except OSError:
                skipped.append({"path": item["path"], "reason": "unlink_failed"})

    planned = [{"path": item["path"], "size": item["size"]} for item in selected]
    result = {
        "applied": bool(apply),
        "limits": {"minimum_age_seconds": minimum_age_seconds,
                   "max_files": max_files, "max_bytes": max_bytes},
        "planned_file_count": len(planned),
        "planned_bytes": selected_bytes,
        "planned_files": planned,
        "deleted_file_count": len(deleted),
        "deleted_bytes": sum(item["size"] for item in deleted),
        "deleted_files": deleted,
        "skipped_files": skipped,
        "remaining_eligible_file_count": max(0, len(candidates) - len(deleted)),
        "audit": report,
    }
    if actor_id is not None:
        result["actor_id"] = str(actor_id)
    if apply:
        logger.warning("Applied orphan upload cleanup: %s", {
            "actor_id": str(actor_id) if actor_id is not None else None,
            "reason": reason,
            "deleted_file_count": result["deleted_file_count"],
            "deleted_bytes": result["deleted_bytes"],
            "deleted_files": deleted,
            "skipped_file_count": len(skipped),
            "minimum_age_seconds": minimum_age_seconds,
        })
    return result


def validate_upload(path):
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        from PIL import Image
        with Image.open(path) as image:
            if image.width * image.height > 25000000:
                raise ValueError("Image exceeds pixel limit")
            image.verify()
    if path.suffix.lower() in (".docx", ".pptx"):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(item.file_size for item in entries) > 200 * 1024 * 1024:
                raise ValueError("Document archive exceeds expansion limit")


def sanitize_svg(value):
    if len(value) > 100000 or "<!" in value:
        raise ValueError("Invalid SVG document")
    root = ET.fromstring(value)
    allowed = {"svg", "g", "path", "line", "polyline", "polygon", "rect", "circle", "ellipse", "text", "tspan", "defs", "marker", "clipPath", "title", "desc"}
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("Expected SVG root")
    for element in root.iter():
        if element.tag.split("}")[-1] not in allowed:
            raise ValueError("Unsafe SVG element")
        for key, item in element.attrib.items():
            key = key.split("}")[-1].lower()
            if key.startswith("on") or key in ("href", "style") or "url(" in item.lower():
                raise ValueError("Unsafe SVG attribute")
    return ET.tostring(root, encoding="utf8")


def figures(ctx, extraction, files):
    from PIL import Image, ImageOps
    images = [(file_id, path) for file_id, path, mime in files if mime.startswith("image/")]
    generated = []
    for question in extraction["questions"]:
        for figure in question.get("figures", []):
            if figure["source_image"] >= len(images):
                raise ValueError("Figure references an unavailable image")
            source_id, source_path = images[figure["source_image"]]
            with Image.open(source_path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                x0, y0, x1, y1 = figure["bbox"]
                crop = image.crop((int(x0 * image.width), int(y0 * image.height), max(int(x0 * image.width) + 1, int(x1 * image.width)), max(int(y0 * image.height) + 1, int(y1 * image.height))))
                file_id = uid()
                path = "uploads/" + file_id + ".png"
                target = safe_path(path)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                crop.save(target)
                target.chmod(0o600)
                generated.append({"id": file_id, "filename": "figure.png", "path": path, "media_type": "image/png", "size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
                figure["crop_file_id"], figure["source_file_id"] = file_id, source_id
            svg = figure.pop("svg", "")
            if svg:
                value = sanitize_svg(svg)
                file_id = uid()
                path = "uploads/" + file_id + ".svg"
                target = safe_path(path)
                target.write_bytes(value)
                target.chmod(0o600)
                generated.append({"id": file_id, "filename": "figure.svg", "path": path, "media_type": "image/svg+xml", "size": len(value), "sha256": hashlib.sha256(value).hexdigest()})
                figure["svg_file_id"] = file_id
    extraction["_generated_files"] = generated
    return extraction
