"""Schema validation for the multi-milestone Scenario model."""

from __future__ import annotations

import json

import pytest

from benchmark.scenario import Milestone, Scenario


def _milestones(n: int) -> list[Milestone]:
    return [
        Milestone(index=i, requirement=f"req{i}", user_intent=f"intent{i}", test_id=f"test{i}")
        for i in range(1, n + 1)
    ]


def _scenario(n: int = 3, **overrides) -> Scenario:
    params = {"user_persona": "数据产品经理", "milestones": _milestones(n), "max_rounds": n}
    params.update(overrides)
    return Scenario(**params)


def test_valid_scenario_milestones_are_1_based_and_findable() -> None:
    scenario = _scenario(3)
    assert len(scenario.milestones) == 3
    assert scenario.milestone_by_index(2).test_id == "test2"
    assert scenario.max_rounds == 3
    assert scenario.max_corrections == 1  # default


def test_max_rounds_less_than_milestones_rejected() -> None:
    with pytest.raises(ValueError, match="max_rounds"):
        _scenario(3, max_rounds=2)


def test_non_consecutive_indices_rejected() -> None:
    milestones = _milestones(3)
    milestones[-1].index = 4
    with pytest.raises(ValueError):
        _scenario(3, milestones=milestones)


def test_duplicate_test_ids_rejected() -> None:
    milestones = _milestones(2)
    milestones[1].test_id = "test1"
    with pytest.raises(ValueError):
        _scenario(2, milestones=milestones)


def test_negative_max_corrections_rejected() -> None:
    with pytest.raises(ValueError):
        _scenario(3, max_corrections=-1)


def test_missing_milestone_raises_key_error() -> None:
    scenario = _scenario(1)
    with pytest.raises(KeyError):
        scenario.milestone_by_index(2)


def test_load_from_json_file(tmp_path) -> None:
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "user_persona": "p",
                "milestones": [
                    {"index": 1, "requirement": "a", "user_intent": "x", "test_id": "t1"},
                    {"index": 2, "requirement": "b", "user_intent": "y", "test_id": "t2"},
                ],
                "max_rounds": 4,
                "max_corrections": 1,
            }
        )
    )
    scenario = Scenario.load(path)
    assert scenario.milestone_by_index(2).requirement == "b"
    assert scenario.max_rounds == 4


def test_parse_from_json_string() -> None:
    text = json.dumps(
        {
            "user_persona": "p",
            "milestones": [{"index": 1, "requirement": "a", "user_intent": "x", "test_id": "t"}],
            "max_rounds": 3,
        }
    )
    scenario = Scenario.parse(text)
    assert scenario.milestone_by_index(1).test_id == "t"
