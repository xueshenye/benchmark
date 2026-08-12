"""Host-side unit tests for the ticket-system task's scorer.

Exercises scorer *mechanics* only (no live HTTP): app discovery, free-port,
tamper detection, reward product, main. Actual hidden inputs live in the task's
own ground_truth/facts.json (generated at grading time).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "ticket-system" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "ticket-system" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("ts_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iso_days_ago_returns_iso_string(scorer) -> None:
    value = scorer._iso_days_ago(45)
    assert "T" in value
    assert value.endswith(":SS") is False  # seconds present


def test_free_port_is_bindable(scorer) -> None:
    port = scorer._free_port()
    assert isinstance(port, int) and port > 0
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))  # a fresh socket can bind to the freed port
    finally:
        s.close()


def test_candidates_include_package_and_skip_missing(scorer, tmp_path) -> None:
    candidates = scorer._candidates(str(tmp_path))
    # The first candidate is the importable package.
    assert candidates[0][0][-2:] == ["-m", "ticket_system"]
    assert candidates[0][1] == str(tmp_path / "ticket_system" / "__main__.py")
    # A workspace with no files → every candidate's precheck is missing.
    assert scorer._resolve_app(str(tmp_path)) is None


def test_resolve_app_finds_existing_app_py(scorer, tmp_path, monkeypatch) -> None:
    (tmp_path / "app.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setattr(scorer, "_probe_app", lambda argv, b, p, d, timeout=4: True)
    argv = scorer._resolve_app(str(tmp_path))
    assert argv is not None
    assert os.path.basename(argv[-1]) == "app.py"


def test_resolve_app_prefers_package_when_present(scorer, tmp_path, monkeypatch) -> None:
    (tmp_path / "ticket_system" / "__main__.py").parent.mkdir(parents=True)
    (tmp_path / "ticket_system" / "__main__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(scorer, "_probe_app", lambda argv, b, p, d, timeout=4: True)
    argv = scorer._resolve_app(str(tmp_path))
    assert argv is not None
    assert argv[-2:] == ["-m", "ticket_system"]


def test_docs_tampered_detects_modified_file(scorer, tmp_path) -> None:
    gt = tmp_path / "gt" / "docs"
    ws = tmp_path / "ws" / "docs"
    gt.mkdir(parents=True)
    ws.mkdir(parents=True)
    (gt / "api.md").write_text("字段必须一致", encoding="utf-8")
    (ws / "api.md").write_text("字段随便改", encoding="utf-8")
    assert scorer._docs_tampered(str(tmp_path / "ws"), str(tmp_path / "gt")) is True


def test_docs_not_tampered_when_identical(scorer, tmp_path) -> None:
    gt = tmp_path / "gt" / "docs"
    ws = tmp_path / "ws" / "docs"
    gt.mkdir(parents=True)
    ws.mkdir(parents=True)
    (gt / "api.md").write_text("字段必须一致", encoding="utf-8")
    (ws / "api.md").write_text("字段必须一致", encoding="utf-8")
    (ws / "extra.txt").write_text("extra is fine", encoding="utf-8")
    assert scorer._docs_tampered(str(tmp_path / "ws"), str(tmp_path / "gt")) is False


def test_docs_missing_file_is_tampered(scorer, tmp_path) -> None:
    gt = tmp_path / "gt" / "docs"
    ws = tmp_path / "ws" / "docs"
    gt.mkdir(parents=True)
    ws.mkdir(parents=True)
    (gt / "api.md").write_text("x", encoding="utf-8")
    assert scorer._docs_tampered(str(tmp_path / "ws"), str(tmp_path / "gt")) is True


def test_scenario_and_checkers_are_consistent(scorer) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario_ids = [m["test_id"] for m in scenario["milestones"]]
    assert len(scenario_ids) == len(set(scenario_ids))
    assert set(scenario_ids) == set(scorer.CHECKERS)
    assert [m["index"] for m in scenario["milestones"]] == list(
        range(1, len(scenario["milestones"]) + 1)
    )
    assert scenario["max_rounds"] >= len(scenario["milestones"])


def test_score_iterates_and_products(scorer, monkeypatch) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    failing = scenario["milestones"][1]["test_id"]

    def _mk(test_id: str):
        def _check(base_dir: str, gt: str) -> float:  # noqa: ARG001
            return 0.0 if test_id == failing else 1.0
        return _check

    monkeypatch.setattr(
        scorer, "CHECKERS", {m["test_id"]: _mk(m["test_id"]) for m in scenario["milestones"]}
    )
    result = scorer.score(scenario, "/tmp/ignored", "/tmp/ignored-gt")
    assert result["round_1"] == 1.0
    assert result[f"round_{scenario['milestones'][1]['index']}"] == 0.0
    assert result["reward"] == 0.0


def test_main_writes_reward_json(scorer, tmp_path, monkeypatch) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        scorer, "CHECKERS", {m["test_id"]: (lambda b, g: 1.0) for m in scenario["milestones"]}
    )
    out = tmp_path / "reward.json"
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    rc = scorer.main(
        [
            "--base-dir", str(tmp_path),
            "--scenario", str(scenario_path),
            "--ground-truth", str(tmp_path / "gt"),
            "--reward-out", str(out),
        ]
    )
    assert rc == 0
    rewards = json.loads(out.read_text(encoding="utf-8"))
    assert rewards["reward"] == 1.0
    assert all(rewards[f"round_{m['index']}"] == 1.0 for m in scenario["milestones"])
