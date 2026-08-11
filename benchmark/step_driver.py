"""StepDriver: transport-agnostic between-step logic for native multi-step (Design B).

Drives the interaction across Harbor steps: after each step's agent runs, it
diffs the workspace snapshot vs the previous step, asks the user-LLM to judge
the agent's output and produce the next message, advances/corrects/force-
advances the milestone via ``TurnController``, and returns the next step's
instruction (or ``None`` when the interaction is finished).

Has zero Harbor imports — it works on any ``scenario``/``simulator``/
``controller``, so it is unit-testable with fakes and reusable if the
multi-step integration seam changes.
"""

from __future__ import annotations

import difflib

from benchmark.controller import TurnController
from benchmark.scenario import Scenario
from benchmark.user_simulator import UserSimulator

MAX_WORKSPACE_EVIDENCE_CHARS = 6000


def workspace_diff(
    prev: dict[str, str],
    cur: dict[str, str],
    max_chars: int = MAX_WORKSPACE_EVIDENCE_CHARS,
) -> str:
    """Compact diff of what changed between two workspace snapshots."""
    added = sorted(cur.keys() - prev.keys())
    removed = sorted(prev.keys() - cur.keys())
    changed = sorted(k for k in cur.keys() & prev.keys() if prev[k] != cur[k])
    parts: list[str] = []
    if removed:
        parts.append("已删除: " + ", ".join(removed))
    if added:
        parts.append("新增: " + ", ".join(added))
    if changed:
        parts.append("修改: " + ", ".join(changed))
    for f in added + changed:
        before = (prev.get(f, "") or "").splitlines()
        after = (cur.get(f, "") or "").splitlines()
        parts.append(
            "\n".join(
                difflib.unified_diff(before, after, fromfile=f, tofile=f, lineterm="", n=1)
            )
        )
    text = "\n".join(parts).strip()
    if not text:
        return "(本轮无文件改动)"
    return text[:max_chars]


class StepDriver:
    """Owns the milestone state and produces the next step's instruction."""

    def __init__(
        self,
        scenario: Scenario,
        simulator: UserSimulator,
        controller: TurnController,
    ) -> None:
        self.scenario = scenario
        self.simulator = simulator
        self.controller = controller
        self._prev_snapshot: dict[str, str] = {}
        self._first_instruction = ""

    @property
    def is_done(self) -> bool:
        return self.controller.is_done

    def on_step_started(self, first_instruction: str) -> None:
        """Called before step 1: the initial task. Marks round 1 as consumed
        (step 1 runs the initial task directly, without a user-LLM call)."""
        self._first_instruction = first_instruction
        self.controller.round_count = 1
        self.simulator.start(first_instruction)

    async def on_step_completed(
        self,
        agent_output: str,
        workspace_snapshot: dict[str, str],
    ) -> str | None:
        """Judge step N's agent output and return step N+1's instruction.

        Returns ``None`` when the interaction is finished (all milestones done
        or the round budget is spent) — the caller should stop scheduling steps.
        """
        evidence = workspace_diff(self._prev_snapshot, workspace_snapshot)
        self._prev_snapshot = workspace_snapshot

        next_message = await self.controller.next_user_message(
            agent_output, self._first_instruction, workspace_evidence=evidence
        )
        if self.controller.is_done:
            return None
        return next_message
