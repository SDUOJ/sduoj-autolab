"""Focused collection manifest tests independent of model and database fixtures."""

import pytest

from auto_task.question_workflows import (append_batch, detach_duplicate,
                                          detach_item, ensure_manifest,
                                          restore_duplicate, restore_item)


def test_legacy_members_get_stable_numbers_and_new_batches_keep_gaps():
    data = {"question_ids": ["legacy-a", "legacy-b"]}
    ensure_manifest(data)
    detach_item(data, 1)
    appended = append_batch(data, "batch-2", [
        {"question_id": "new-c", "original": {"question_type": "short_answer", "zh": "C"}},
    ])
    assert [item["number"] for item in data["items"]] == [1, 2, 3]
    assert appended[0]["number"] == 3
    assert data["question_ids"] == ["legacy-b", "new-c"]
    restore_item(data, 1)
    assert data["question_ids"] == ["legacy-a", "legacy-b", "new-c"]


def test_duplicate_split_preserves_original_and_can_relink():
    original = {"question_type": "single_choice", "zh": "Original", "options": []}
    data = {"question_ids": []}
    append_batch(data, "batch", [
        {"question_id": "canonical", "duplicate_of": "canonical", "original": original},
    ])
    detach_duplicate(data, 1, "independent")
    assert data["items"][0]["original"] == original
    assert data["question_ids"] == ["independent"]
    restore_duplicate(data, 1)
    assert data["items"][0]["materialized_question_id"] == "independent"
    assert data["question_ids"] == ["canonical"]


def test_batch_replay_and_manifest_corruption_are_rejected():
    data = {"question_ids": []}
    append_batch(data, "same", [])
    with pytest.raises(ValueError, match="already applied"):
        append_batch(data, "same", [])
    with pytest.raises(ValueError, match="Invalid collection manifest"):
        ensure_manifest({"items": {}})
