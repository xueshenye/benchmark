"""Host-side unit tests for the demo task's scorer.

The scorer is loaded from disk via importlib (no side effects on import).
Per the project rule "don't hardcode test inputs / don't write answers into
test scripts", these tests use SYNTHETIC test_ids and synthetic checker values,
never the demo task's real test_ids or expected statistics.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCORER_PATH = (
    Path(__file__).resolve().parent.parent
    / "tasks" / "benchmark" / "multi-round-cli-demo" / "tests" / "scorer.py"
)


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_summary_extracts_numbers(scorer) -> None:
    assert scorer.parse_summary("file: count=2 mean=4.5 min=3.0 max=6.0") == {
        "count": 2,
        "mean": 4.5,
        "min": 3.0,
        "max": 6.0,
    }


def test_parse_summary_returns_none_on_garbage(scorer) -> None:
    assert scorer.parse_summary("no stats here") is None


def test_score_iterates_milestones_and_product(scorer, monkeypatch) -> None:
    monkeypatch.setattr(
        scorer,
        "CHECKERS",
        {"a": lambda base_dir: 1.0, "b": lambda base_dir: 0.0},
    )
    scenario = {
        "milestones": [
            {"index": 1, "test_id": "a"},
            {"index": 2, "test_id": "b"},
        ]
    }
    assert scorer.score(scenario, "/tmp/ignored") == {
        "round_1": 1.0,
        "round_2": 0.0,
        "reward": 0.0,
    }


def test_score_all_pass_gives_reward_one(scorer, monkeypatch) -> None:
    monkeypatch.setattr(scorer, "CHECKERS", {"a": lambda base_dir: 1.0})
    scenario = {"milestones": [{"index": 1, "test_id": "a"}]}
    assert scorer.score(scenario, "/tmp/ignored") == {"round_1": 1.0, "reward": 1.0}


def test_score_unknown_test_id_raises(scorer, monkeypatch) -> None:
    monkeypatch.setattr(scorer, "CHECKERS", {"a": lambda base_dir: 1.0})
    scenario = {"milestones": [{"index": 1, "test_id": "does-not-exist"}]}
    with pytest.raises(KeyError):
        scorer.score(scenario, "/tmp/ignored")


def test_main_writes_reward_json(scorer, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(scorer, "score", lambda scenario, base_dir: {"reward": 1.0})
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({"milestones": []}), encoding="utf-8")
    reward_out = tmp_path / "reward.json"

    rc = scorer.main(
        [
            "--base-dir",
            str(tmp_path),
            "--scenario",
            str(scenario_path),
            "--reward-out",
            str(reward_out),
        ]
    )

    assert rc == 0
    assert json.loads(reward_out.read_text(encoding="utf-8")) == {"reward": 1.0}
