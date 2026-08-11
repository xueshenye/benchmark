"""Host-side unit tests for the repofix task's scorer.

Follows the project rule "don't hardcode test inputs / don't write answers into
test scripts": parse-helper tests use synthetic strings; the scenario vs checker
consistency test reads the task's own files (a typo/coverage check).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "repofix" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "repofix" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("repofix_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_totals_multiline(scorer) -> None:
    assert scorer.parse_totals("food: 7.00\ndrinks: 7.50\n") == {
        "food": 7.0,
        "drinks": 7.5,
    }


def test_parse_totals_skips_garbage_lines(scorer) -> None:
    assert scorer.parse_totals("not a total\nfood: 1.0\n") == {"food": 1.0}


def test_parse_totals_empty(scorer) -> None:
    assert scorer.parse_totals("") == {}


def test_count_functions(tmp_path, scorer) -> None:
    (tmp_path / "pipeline.py").write_text(
        "def a():\n    pass\n\n\ndef b():\n    pass\n\n\ndef c():\n    pass\n",
        encoding="utf-8",
    )
    assert scorer.count_functions(str(tmp_path)) == 3


def test_count_functions_zero_when_missing(scorer, tmp_path) -> None:
    assert scorer.count_functions(str(tmp_path)) == 0


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
    failing = scenario["milestones"][0]["test_id"]

    def _mk(test_id: str):
        return lambda base_dir: 0.0 if test_id == failing else 1.0

    monkeypatch.setattr(scorer, "CHECKERS", {
        m["test_id"]: _mk(m["test_id"]) for m in scenario["milestones"]
    })
    result = scorer.score(scenario, "/tmp/ignored")
    assert result["round_1"] == 0.0
    assert result["reward"] == 0.0
