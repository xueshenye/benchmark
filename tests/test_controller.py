"""Unit tests for the TurnController state machine (no LLM, no Harbor)."""

from __future__ import annotations

import asyncio

from benchmark.controller import TurnController
from benchmark.scenario import Scenario
from benchmark.user_simulator import TurnDecision


class _FakeSimulator:
    """Records judge/render/record_turn calls and serves scripted decisions."""

    def __init__(self, decisions: list[TurnDecision], renders: list[str] | None = None) -> None:
        self._decisions = list(decisions)
        self._renders = list(renders or [])
        self.judge_calls: list[dict] = []
        self.render_calls: list[dict] = []
        self.turns: list[tuple[str, str]] = []

    async def judge_and_speak(self, current, nxt, agent_output, *, workspace_evidence="") -> TurnDecision:
        self.judge_calls.append(
            {
                "current": current.index,
                "nxt": nxt.index if nxt else None,
                "agent_output": agent_output,
                "workspace_evidence": workspace_evidence,
            }
        )
        return self._decisions.pop(0)

    async def render_milestone(self, milestone, *, agent_output="", workspace_evidence="") -> str:
        self.render_calls.append(
            {"milestone_index": milestone.index, "agent_output": agent_output, "workspace_evidence": workspace_evidence}
        )
        return self._renders.pop(0)

    def record_turn(self, agent_output: str, message: str) -> None:
        self.turns.append((agent_output, message))


def _scenario(n: int, *, max_rounds: int | None = None, max_corrections: int = 1) -> Scenario:
    return Scenario.model_validate(
        {
            "user_persona": "pm",
            "milestones": [
                {"index": i, "requirement": f"req{i}", "user_intent": f"intent{i}", "test_id": f"t{i}"}
                for i in range(1, n + 1)
            ],
            "max_rounds": max_rounds or (n * (1 + max_corrections)),
            "max_corrections": max_corrections,
        }
    )


def test_first_call_returns_initial_task_and_sets_round_count_1() -> None:
    sim = _FakeSimulator([])
    ctrl = TurnController(_scenario(3), sim)

    msg = asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))

    assert msg == "INITIAL TASK"
    assert ctrl.round_count == 1
    assert sim.judge_calls == []  # no LLM call on round 1


def test_satisfied_advances_milestone_ptr_and_returns_decision_message() -> None:
    sim = _FakeSimulator([TurnDecision(satisfied=True, message="next request")])
    ctrl = TurnController(_scenario(3), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))  # round 1

    msg = asyncio.run(ctrl.next_user_message("agent out", "INITIAL TASK"))  # round 2

    assert ctrl.milestone_ptr == 1
    assert ctrl.correction_count == 0
    assert msg == "next request"
    assert sim.judge_calls == [
        {"current": 1, "nxt": 2, "agent_output": "agent out", "workspace_evidence": ""}
    ]
    assert sim.turns == [("agent out", "next request")]  # only the delivered message recorded
    assert ctrl.decisions[0]["satisfied"] is True
    assert ctrl.decisions[0]["forced_advance"] is False


def test_workspace_evidence_flows_to_judge_and_decisions() -> None:
    sim = _FakeSimulator([TurnDecision(satisfied=True, message="next request")])
    ctrl = TurnController(_scenario(3), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))

    evidence = "+ /workspace/src/stats.py\nprint('stats')"
    asyncio.run(ctrl.next_user_message("agent out", "INITIAL TASK", workspace_evidence=evidence))

    # The judge saw the evidence and it is logged with the decision (auditable).
    assert sim.judge_calls[0]["workspace_evidence"] == evidence
    assert ctrl.decisions[0]["workspace_evidence"] == evidence


def test_unsatisfied_within_budget_returns_corrective_and_keeps_ptr() -> None:
    sim = _FakeSimulator([TurnDecision(satisfied=False, message="fix the count")])
    ctrl = TurnController(_scenario(3), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))

    msg = asyncio.run(ctrl.next_user_message("agent out", "INITIAL TASK"))

    assert ctrl.milestone_ptr == 0  # stays on the current milestone
    assert ctrl.correction_count == 1
    assert msg == "fix the count"
    assert ctrl.decisions[0]["forced_advance"] is False


def test_corrections_exhausted_force_advances_and_rerenders_next() -> None:
    sim = _FakeSimulator(
        decisions=[
            TurnDecision(satisfied=False, message="first correction"),
            TurnDecision(satisfied=False, message="second correction (discarded)"),
        ],
        renders=["next milestone request"],
    )
    ctrl = TurnController(_scenario(3, max_corrections=1), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))

    asyncio.run(ctrl.next_user_message("out1", "INITIAL TASK"))  # round 2: corrective
    msg = asyncio.run(ctrl.next_user_message("out2", "INITIAL TASK"))  # round 3: forced advance

    assert ctrl.milestone_ptr == 1  # advanced past the abandoned milestone
    assert ctrl.correction_count == 0
    assert msg == "next milestone request"
    # The re-render targets the *new* current milestone (post-advance, index 2).
    assert sim.render_calls == [{"milestone_index": 2, "agent_output": "out2", "workspace_evidence": ""}]
    # Only the delivered message is in the transcript; the discarded correction is in decisions.
    assert sim.turns == [("out1", "first correction"), ("out2", "next milestone request")]
    assert [d["forced_advance"] for d in ctrl.decisions] == [False, True]


def test_satisfied_on_last_milestone_sets_should_stop_before_run() -> None:
    sim = _FakeSimulator(
        decisions=[
            TurnDecision(satisfied=True, message="milestone 2 request"),
            TurnDecision(satisfied=True, message="thanks, all done"),
        ]
    )
    ctrl = TurnController(_scenario(2), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))  # round 1 (milestone 1)

    asyncio.run(ctrl.next_user_message("out1", "INITIAL TASK"))  # round 2 → advance to milestone 2
    assert ctrl.should_stop_before_run is False
    assert ctrl.current_milestone.index == 2

    msg = asyncio.run(ctrl.next_user_message("out2", "INITIAL TASK"))  # round 3 → all done
    assert ctrl.milestone_ptr == 2
    assert ctrl.should_stop_before_run is True
    assert ctrl.is_done is True
    assert msg == "thanks, all done"  # closing remark (never run by the caller)


def test_round_cap_allows_max_rounds_actual_runs() -> None:
    # 1 milestone, cap 2: the 2nd round (a correction) must actually run.
    sim = _FakeSimulator([TurnDecision(satisfied=False, message="fix it")])
    ctrl = TurnController(_scenario(1, max_rounds=2, max_corrections=1), sim)

    issued: list[str] = []
    while not ctrl.is_done:
        msg = asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))
        if ctrl.should_stop_before_run:
            break
        issued.append(msg)

    assert issued == ["INITIAL TASK", "fix it"]  # both rounds ran, none discarded by the cap
    assert ctrl.round_count == 2
    assert ctrl.is_done is True


def test_max_corrections_zero_force_advances_on_first_unsatisfied() -> None:
    sim = _FakeSimulator(
        decisions=[TurnDecision(satisfied=False, message="nope")],
        renders=["next milestone request"],
    )
    ctrl = TurnController(_scenario(2, max_corrections=0), sim)
    asyncio.run(ctrl.next_user_message("", "INITIAL TASK"))

    msg = asyncio.run(ctrl.next_user_message("out", "INITIAL TASK"))

    assert ctrl.milestone_ptr == 1
    assert ctrl.decisions[0]["forced_advance"] is True
    assert msg == "next milestone request"
