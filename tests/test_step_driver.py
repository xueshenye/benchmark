"""Unit tests for StepDriver (Design B between-step logic; no Harbor)."""

from __future__ import annotations

import asyncio

from benchmark.controller import TurnController
from benchmark.scenario import Scenario
from benchmark.step_driver import StepDriver, workspace_diff
from benchmark.user_simulator import TurnDecision


class _FakeSimulator:
    """Records judge/render/start calls and serves scripted decisions."""

    def __init__(self, decisions: list[TurnDecision], renders: list[str] | None = None) -> None:
        self._decisions = list(decisions)
        self._renders = list(renders or [])
        self.judge_calls: list[dict] = []
        self.render_calls: list[int] = []
        self.turns: list[tuple[str, str]] = []
        self.started: str | None = None

    def start(self, text: str) -> None:
        self.started = text

    async def judge_and_speak(self, current, nxt, agent_output, *, workspace_evidence=""):
        self.judge_calls.append(
            {
                "current": current.index,
                "nxt": nxt.index if nxt else None,
                "agent_output": agent_output,
                "workspace_evidence": workspace_evidence,
            }
        )
        return self._decisions.pop(0)

    async def render_milestone(self, milestone, *, agent_output="", workspace_evidence=""):
        self.render_calls.append(milestone.index)
        return self._renders.pop(0)

    def record_turn(self, agent_output: str, message: str) -> None:
        self.turns.append((agent_output, message))

    @property
    def transcript(self) -> list[dict[str, str]]:
        return []


def _driver(
    decisions: list[TurnDecision],
    n: int = 3,
    *,
    max_rounds: int = 6,
    max_corrections: int = 1,
    renders: list[str] | None = None,
):
    scenario = Scenario.model_validate(
        {
            "user_persona": "pm",
            "milestones": [
                {"index": i, "requirement": f"req{i}", "user_intent": f"intent{i}", "test_id": f"t{i}"}
                for i in range(1, n + 1)
            ],
            "max_rounds": max_rounds,
            "max_corrections": max_corrections,
        }
    )
    sim = _FakeSimulator(decisions, renders=renders)
    controller = TurnController(scenario, sim)
    driver = StepDriver(scenario, sim, controller)
    return driver, sim, controller


def test_on_step_started_consumes_round_1() -> None:
    driver, sim, controller = _driver([])
    driver.on_step_started("INITIAL TASK")
    assert controller.round_count == 1  # step-1 already ran the initial task
    assert sim.started == "INITIAL TASK"
    assert driver.is_done is False


def test_snapshot_diff_flows_into_judge_evidence() -> None:
    driver, sim, _ = _driver([TurnDecision(satisfied=True, message="next request")])
    driver.on_step_started("INITIAL TASK")

    snapshot = {"/workspace/src/stats.py": "print('stats')\n"}
    next_instruction = asyncio.run(driver.on_step_completed("agent out", snapshot))

    assert sim.judge_calls[0]["agent_output"] == "agent out"
    assert "print('stats')" in sim.judge_calls[0]["workspace_evidence"]
    assert "/workspace/src/stats.py" in sim.judge_calls[0]["workspace_evidence"]
    assert next_instruction == "next request"


def test_advance_correct_and_force_advance_through_controller() -> None:
    # Round 2: satisfied → advance. Round 3: unsatisfied (in budget) → correct.
    # Round 4: unsatisfied again → force-advance + re-render next milestone.
    driver, sim, controller = _driver(
        decisions=[
            TurnDecision(satisfied=True, message="next request"),
            TurnDecision(satisfied=False, message="fix it"),
            TurnDecision(satisfied=False, message="still wrong (discarded)"),
        ],
        renders=["next milestone request"],
    )
    driver.on_step_started("INITIAL TASK")

    asyncio.run(driver.on_step_completed("out1", {"/workspace/a.py": "x"}))
    assert controller.milestone_ptr == 1

    asyncio.run(driver.on_step_completed("out2", {"/workspace/a.py": "y"}))
    assert controller.milestone_ptr == 1  # stayed, corrective
    assert controller.correction_count == 1

    asyncio.run(driver.on_step_completed("out3", {"/workspace/a.py": "z"}))
    assert controller.milestone_ptr == 2  # force-advanced past the abandoned milestone 2
    # The re-request targets the *new* current milestone (index 3), not the abandoned one.
    assert sim.render_calls == [3]


def test_returns_none_when_all_milestones_done() -> None:
    driver, sim, controller = _driver(
        decisions=[
            TurnDecision(satisfied=True, message="milestone 2 request"),
            TurnDecision(satisfied=True, message="all done"),
        ],
        n=2,
    )
    driver.on_step_started("INITIAL TASK")

    asyncio.run(driver.on_step_completed("out1", {}))
    assert controller.is_done is False

    result = asyncio.run(driver.on_step_completed("out2", {}))
    assert controller.is_done is True
    assert result is None  # no next step


def test_returns_none_when_round_cap_reached() -> None:
    driver, _, controller = _driver(
        decisions=[TurnDecision(satisfied=False, message="fix it")],
        n=1,
        max_rounds=2,
        max_corrections=1,
    )
    driver.on_step_started("INITIAL TASK")

    result = asyncio.run(driver.on_step_completed("out1", {}))
    assert controller.is_done is True  # round budget spent
    assert result is None


def test_workspace_diff_reports_change() -> None:
    assert workspace_diff({}, {"/workspace/a.py": "print(1)"}).startswith("新增: /workspace/a.py")
    assert "print(1)" in workspace_diff({}, {"/workspace/a.py": "print(1)"})
    assert workspace_diff({"/workspace/a.py": "print(1)"}, {"/workspace/a.py": "print(1)"}) == "(本轮无文件改动)"
