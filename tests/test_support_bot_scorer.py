"""Host-side unit tests for the support-bot task's scorer.

Follows the project rule "don't hardcode test inputs / don't write answers into
test scripts": these tests exercise scorer *mechanics* (normalization, tamper
detection, entry-point discovery, reward product) with synthetic strings and
fixtures — the actual hidden inputs live in the task's own ground_truth/facts.json.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "support-bot" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "support-bot" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("sb_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_strips_whitespace_and_lowercases(scorer) -> None:
    assert scorer._normalize("7 天") == "7天"
    assert scorer._normalize("WeChat Pay") == "wechatpay"
    assert scorer._normalize("1 year") == "1year"


def test_rng_suffix_is_latin_only(scorer) -> None:
    import random
    rng = random.Random(1)
    suffix = scorer._rng_suffix(rng, n=6)
    assert len(suffix) == 6
    assert suffix.isalpha() and suffix.islower()


def test_kb_tampered_detects_modified_file(scorer, tmp_path) -> None:
    gt = tmp_path / "gt"
    ws = tmp_path / "ws"
    (gt / "knowledge_base").mkdir(parents=True)
    (ws / "knowledge_base").mkdir(parents=True)
    (gt / "knowledge_base" / "products.md").write_text("价格:399 元", encoding="utf-8")
    (ws / "knowledge_base" / "products.md").write_text("价格:1 元", encoding="utf-8")
    assert scorer._kb_tampered(str(ws), str(gt)) is True


def test_kb_not_tampered_when_identical(scorer, tmp_path) -> None:
    gt = tmp_path / "gt"
    ws = tmp_path / "ws"
    (gt / "knowledge_base").mkdir(parents=True)
    (ws / "knowledge_base").mkdir(parents=True)
    (gt / "knowledge_base" / "products.md").write_text("价格:399 元", encoding="utf-8")
    (ws / "knowledge_base" / "products.md").write_text("价格:399 元", encoding="utf-8")
    (ws / "knowledge_base" / "extra.md").write_text("extra file is fine", encoding="utf-8")
    assert scorer._kb_tampered(str(ws), str(gt)) is False


def test_kb_missing_file_is_tampered(scorer, tmp_path) -> None:
    gt = tmp_path / "gt"
    ws = tmp_path / "ws"
    (gt / "knowledge_base").mkdir(parents=True)
    (ws / "knowledge_base").mkdir(parents=True)
    (gt / "knowledge_base" / "policies.md").write_text("x", encoding="utf-8")
    assert scorer._kb_tampered(str(ws), str(gt)) is True


def test_resolve_bot_skips_missing_files(scorer, tmp_path) -> None:
    """A workspace with no bot at all must not raise (all candidates skipped)."""
    assert scorer._resolve_bot(str(tmp_path)) is None


def test_resolve_bot_finds_executable_script(scorer, tmp_path) -> None:
    script = tmp_path / "support-bot"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    argv = scorer._resolve_bot(str(tmp_path))
    assert argv is not None
    assert os.path.basename(argv[-1]) == "support-bot"


def test_scenario_and_checkers_are_consistent(scorer) -> None:
    """Every milestone test_id has a checker and no checker is orphaned."""
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
