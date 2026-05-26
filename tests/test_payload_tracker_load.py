# Created by model-proxy on 2026/05/26
# Copyright © 2026

import json
from pathlib import Path

from src.scheduler.payload_tracker import PayloadTracker


def test_load_json_file_with_yaml_content_migrates(tmp_path: Path):
    bad_json = tmp_path / "payload_limits.json"
    bad_json.write_text(
        "groq/test-model:\n  max_bytes: 100\n  hit_count: 1\n",
        encoding="utf-8",
    )
    tracker = PayloadTracker(path=bad_json)
    assert tracker.get_limit("groq", "test-model") == 100
    migrated = json.loads(bad_json.read_text(encoding="utf-8"))
    assert "groq/test-model" in migrated


def test_load_empty_json_file(tmp_path: Path):
    empty = tmp_path / "payload_limits.json"
    empty.write_text("", encoding="utf-8")
    tracker = PayloadTracker(path=empty)
    assert tracker.get_all_limits() == {}
    assert empty.read_text(encoding="utf-8") == "{}"
