"""Schema validation for the multi-round Scenario model."""

from __future__ import annotations

import json

import pytest

from benchmark.scenario import Round, Scenario


def _rounds(n: int) -> list[Round]:
    return [
        Round(index=i, requirement=f"req{i}", user_intent=f"intent{i}", test_id=f"test{i}")
        for i in range(1, n + 1)
    ]


def test_valid_scenario_rounds_are_1_based_and_findable() -> None:
    scenario = Scenario(num_rounds=3, user_persona="数据产品经理", rounds=_rounds(3))
    assert scenario.num_rounds == 3
    assert scenario.round_by_index(2).test_id == "test2"


def test_num_rounds_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        Scenario(num_rounds=2, user_persona="pm", rounds=_rounds(3))


def test_non_consecutive_indices_rejected() -> None:
    rounds = _rounds(3)
    rounds[-1].index = 4
    with pytest.raises(ValueError):
        Scenario(num_rounds=3, user_persona="pm", rounds=rounds)


def test_duplicate_test_ids_rejected() -> None:
    rounds = _rounds(2)
    rounds[1].test_id = "test1"
    with pytest.raises(ValueError):
        Scenario(num_rounds=2, user_persona="pm", rounds=rounds)


def test_missing_round_raises_key_error() -> None:
    scenario = Scenario(num_rounds=1, user_persona="pm", rounds=_rounds(1))
    with pytest.raises(KeyError):
        scenario.round_by_index(2)


def test_load_from_json_file(tmp_path) -> None:
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "num_rounds": 2,
                "user_persona": "p",
                "rounds": [
                    {"index": 1, "requirement": "a", "user_intent": "x", "test_id": "t1"},
                    {"index": 2, "requirement": "b", "user_intent": "y", "test_id": "t2"},
                ],
            }
        )
    )
    scenario = Scenario.load(path)
    assert scenario.round_by_index(2).requirement == "b"


def test_parse_from_json_string() -> None:
    text = json.dumps(
        {"num_rounds": 1, "user_persona": "p",
         "rounds": [{"index": 1, "requirement": "a", "user_intent": "x", "test_id": "t"}]}
    )
    scenario = Scenario.parse(text)
    assert scenario.round_by_index(1).test_id == "t"
