"""Host-side unit tests for the pkg-wordcount task's scorer.

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
_SCORER_PATH = _REPO / "tasks" / "benchmark" / "pkg-wordcount" / "tests" / "scorer.py"
_SCENARIO_PATH = _REPO / "tasks" / "benchmark" / "pkg-wordcount" / "environment" / "scenario.json"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("pkg_scorer_under_test", _SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_cli_counts_parses_lines(scorer) -> None:
    assert scorer.parse_cli_counts("a: 2\nb: 1\n") == {"a": 2, "b": 1}


def test_parse_cli_counts_skips_garbage(scorer) -> None:
    assert scorer.parse_cli_counts("hello world\na: 3\n") == {"a": 3}


def test_parse_cli_counts_empty(scorer) -> None:
    assert scorer.parse_cli_counts("") == {}


def test_scenario_and_checkers_are_consistent(scorer) -> None:
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario_ids = [m["test_id"] for m in scenario["milestones"]]
    assert len(scenario_ids) == len(set(scenario_ids))
    assert set(scenario_ids) == set(scorer.CHECKERS)
    assert [m["index"] for m in scenario["milestones"]] == list(
        range(1, len(scenario["milestones"]) + 1)
    )
    assert scenario["max_rounds"] >= len(scenario["milestones"])


def test_score_product_catches_missing_middle(scorer, monkeypatch) -> None:
    """A synthetic mid-milestone failure must zero the product while keeping
    the per-round keys (the discriminator property for pkg-wordcount)."""
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    missing = scenario["milestones"][1]["test_id"]

    def _mk(test_id: str):
        return lambda base_dir: 0.0 if test_id == missing else 1.0

    monkeypatch.setattr(scorer, "CHECKERS", {
        m["test_id"]: _mk(m["test_id"]) for m in scenario["milestones"]
    })
    result = scorer.score(scenario, "/tmp/ignored")
    assert result["round_1"] == 1.0
    assert result["round_2"] == 0.0
    assert result["round_3"] == 1.0
    assert result["reward"] == 0.0
