"""Host-side unit tests for the todo-tracker task's scorer.

Follows the project rule "don't hardcode test inputs / don't write answers into
test scripts": parse-helper tests use synthetic strings, and the scenario vs
checker consistency test reads the task's own files (a typo/coverage check, not
an answer check).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "todo-tracker" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "todo-tracker" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("todo_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_stats_extracts_counts(scorer) -> None:
    assert scorer._parse_stats("total=7 pending=3 done=4") == (7, 3, 4)


def test_parse_stats_none_on_garbage(scorer) -> None:
    assert scorer._parse_stats("no stats here") is None


def test_parse_report_reads_all_three_priorities(scorer) -> None:
    assert scorer._parse_report("high: 2\nmedium: 0\nlow: 5") == {
        "high": 2,
        "medium": 0,
        "low": 5,
    }


def test_parse_report_none_when_priority_missing(scorer) -> None:
    assert scorer._parse_report("high: 2\nlow: 5") is None


def test_task_dicts_parses_json_array(scorer) -> None:
    class _R:
        stdout = '[{"id": 1, "description": "x"}]'
        returncode = 0
    assert scorer._task_dicts(_R()) == [{"id": 1, "description": "x"}]


def test_task_dicts_none_on_non_array(scorer) -> None:
    class _R:
        stdout = '{"id": 1}'
        returncode = 0
    assert scorer._task_dicts(_R()) is None


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
    monkeypatch.setattr(
        scorer,
        "CHECKERS",
        {m["test_id"]: (lambda base_dir: 1.0) for m in scenario["milestones"]},
    )
    # One synthetic failing checker -> product drops to 0 while keys stay.
    failing = scenario["milestones"][1]["test_id"]

    def _mk(test_id: str):
        def _check(base_dir: str) -> float:
            return 0.0 if test_id == failing else 1.0
        return _check

    monkeypatch.setattr(scorer, "CHECKERS", {
        m["test_id"]: _mk(m["test_id"]) for m in scenario["milestones"]
    })
    result = scorer.score(scenario, "/tmp/ignored")
    assert result["round_1"] == 1.0
    assert result[f"round_{scenario['milestones'][1]['index']}"] == 0.0
    assert result["reward"] == 0.0
