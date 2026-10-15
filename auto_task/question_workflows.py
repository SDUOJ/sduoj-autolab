"""Deterministic collection manifests for incremental question intake."""

from copy import deepcopy


def _number(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def ensure_manifest(data):
    items = data.setdefault("items", [])
    if not isinstance(items, list):
        raise ValueError("Invalid collection manifest")
    used = {_number(item.get("number")) for item in items if isinstance(item, dict)}
    used.discard(None)
    represented = {item.get("question_id") for item in items if isinstance(item, dict)}
    next_number = max(used, default=0) + 1
    for question_id in data.get("question_ids", []):
        if question_id in represented:
            continue
        while next_number in used:
            next_number += 1
        items.append({"number": next_number, "question_id": question_id, "status": "active",
                      "batch_id": "legacy", "original": None})
        used.add(next_number)
        represented.add(question_id)
        next_number += 1
    requested = _number(data.get("next_question_number")) or 1
    data["next_question_number"] = max(requested, max(used, default=0) + 1)
    refresh_active_ids(data)
    return items


def refresh_active_ids(data):
    active = []
    for item in data.get("items", []):
        if item.get("status", "active") == "active" and item.get("question_id") not in active:
            active.append(item["question_id"])
    data["question_ids"] = active
    return active


def append_batch(data, batch_id, records, source=None):
    if not batch_id or len(str(batch_id)) > 96:
        raise ValueError("Invalid collection batch ID")
    items = ensure_manifest(data)
    batches = data.setdefault("batches", [])
    if not isinstance(batches, list):
        raise ValueError("Invalid collection batches")
    if any(item.get("batch_id") == batch_id for item in items) or any(
            batch.get("id") == batch_id for batch in batches if isinstance(batch, dict)):
        raise ValueError("Collection batch already applied")
    if len(items) + len(records) > 1000:
        raise ValueError("Collection exceeds 1000 question slots")
    next_number = data["next_question_number"]
    appended = []
    for record in records:
        original = deepcopy(record["original"])
        original.pop("_duplicate", None)
        item = {"number": next_number, "question_id": record["question_id"], "status": "active",
                "batch_id": batch_id, "original": original}
        if record.get("duplicate_of"):
            item["duplicate_of"] = record["duplicate_of"]
        items.append(item)
        appended.append(item)
        next_number += 1
    data["next_question_number"] = next_number
    batches.append({"id": batch_id, "count": len(records), "numbers": [item["number"] for item in appended],
                    "source": deepcopy(source or {})})
    refresh_active_ids(data)
    return appended


def find_item(data, number):
    expected = _number(number)
    item = next((item for item in ensure_manifest(data) if item.get("number") == expected), None)
    if not item:
        raise KeyError("Collection question number not found")
    return item


def detach_item(data, number):
    item = find_item(data, number)
    item["status"] = "detached"
    refresh_active_ids(data)
    return item


def restore_item(data, number, question_id=None):
    item = find_item(data, number)
    if question_id:
        item["question_id"] = question_id
    item["status"] = "active"
    refresh_active_ids(data)
    return item


def detach_duplicate(data, number, question_id):
    item = find_item(data, number)
    if not item.get("duplicate_of"):
        raise ValueError("Collection item is not an extracted duplicate")
    if not isinstance(item.get("original"), dict):
        raise ValueError("Original extracted question is unavailable")
    item["materialized_question_id"] = question_id
    item["question_id"] = question_id
    item["duplicate_detached"] = True
    item["status"] = "active"
    refresh_active_ids(data)
    return item


def restore_duplicate(data, number):
    item = find_item(data, number)
    duplicate_of = item.get("duplicate_of")
    if not duplicate_of:
        raise ValueError("Collection item is not an extracted duplicate")
    item["question_id"] = duplicate_of
    item["duplicate_detached"] = False
    item["status"] = "active"
    refresh_active_ids(data)
    return item
