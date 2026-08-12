"""Host-side unit tests for the devteam task's scorer.

Follows the project rule "don't hardcode test inputs / don't write answers into
test scripts": helper tests use synthetic strings, and the scenario vs checker
consistency test reads the task's own files (a typo/coverage check, not an
answer check).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "devteam" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "devteam" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("devteam_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_array_parses_array(scorer) -> None:
    class _R:
        stdout = '[{"id": 1, "name": "a"}, {"id": 2}]'
        returncode = 0
    assert scorer._json_array(_R()) == [{"id": 1, "name": "a"}, {"id": 2}]


def test_json_array_none_on_object_or_garbage(scorer) -> None:
    class _Obj:
        stdout = '{"id": 1}'
        returncode = 0
    assert scorer._json_array(_Obj()) is None

    class _Garbage:
        stdout = "not json"
        returncode = 0
    assert scorer._json_array(_Garbage()) is None


def test_pick_returns_distinct_names(scorer) -> None:
    rng = scorer.random.Random(scorer._SEED)
    names = scorer._pick(rng, 5)
    assert len(names) == 5 == len(set(names))
    assert all(n in scorer._NAME_POOL for n in names)


def test_scenario_and_checkers_are_consistent(scorer) -> None:
    """Every milestone test_id has a checker and no checker is orphaned."""
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario_ids = [m["test_id"] for m in scenario["milestones"]]
    assert len(scenario_ids) == len(set(scenario_ids))  # test_id unique
    assert set(scenario_ids) == set(scorer.CHECKERS)
    # Milestone indices are 1..N consecutive and max_rounds >= N.
    assert [m["index"] for m in scenario["milestones"]] == list(
        range(1, len(scenario["milestones"]) + 1)
    )
    assert scenario["max_rounds"] >= len(scenario["milestones"])


def test_score_iterates_and_products(scorer, monkeypatch) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    # One synthetic failing checker -> product drops to 0 while keys stay.
    failing = scenario["milestones"][1]["test_id"]

    def _mk(test_id: str):
        def _check(base_dir: str) -> float:  # noqa: ARG001
            return 0.0 if test_id == failing else 1.0
        return _check

    monkeypatch.setattr(scorer, "CHECKERS", {
        m["test_id"]: _mk(m["test_id"]) for m in scenario["milestones"]
    })
    result = scorer.score(scenario, "/tmp/ignored")
    assert result["round_1"] == 1.0
    assert result[f"round_{scenario['milestones'][1]['index']}"] == 0.0
    assert result["reward"] == 0.0


def test_main_writes_reward_json(scorer, monkeypatch, tmp_path) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        scorer, "CHECKERS",
        {m["test_id"]: (lambda base_dir: 1.0) for m in scenario["milestones"]},  # noqa: ARG005
    )
    out = tmp_path / "sub" / "reward.json"
    rc = scorer.main([
        "--base-dir", str(tmp_path),
        "--scenario", str(_SCENARIO_PATH),
        "--reward-out", str(out),
    ])
    assert rc == 0
    rewards = json.loads(out.read_text(encoding="utf-8"))
    assert all(rewards[f"round_{m['index']}"] == 1.0 for m in scenario["milestones"])
    assert rewards["reward"] == 1.0
