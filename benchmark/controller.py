"""TurnController: transport-agnostic state machine for the dynamic interaction.

Decides, after each agent round, what the simulated user does next:

- ``action == "answer"`` (clarification sub-loop) within the per-milestone
  clarification budget → the user answers the agent's questions; the message
  becomes the next instruction on the SAME milestone (no correction consumed,
  no advance). Past the budget such a round degrades into a normal correction.
- ``satisfied`` → advance to the next milestone (the decision's message is the
  next milestone's request).
- ``unsatisfied`` within the per-milestone correction budget → repeat the
  current milestone with corrective feedback.
- ``unsatisfied`` past the budget → force-advance: move to the next milestone
  and re-render its request (the verifier will score the abandoned milestone 0).

The controller has no knowledge of Harbor or claude-code — it drives any
``simulator`` exposing ``judge_and_speak`` / ``render_milestone`` /
``record_turn``. That keeps it unit-testable with a fake simulator and lets it
be lifted into a Harbor multi-step hook later (Design B) without change.
"""

from __future__ import annotations

from benchmark.scenario import Milestone, Scenario
from benchmark.user_simulator import UserSimulator


class TurnController:
    """Owns milestone/round/correction state and produces the next user message."""

    def __init__(self, scenario: Scenario, simulator: UserSimulator) -> None:
        self.scenario = scenario
        self.simulator = simulator
        self.milestone_ptr = 0  # 0-based index into scenario.milestones
        self.correction_count = 0
        self.clarification_count = 0  # answer rounds used on the current milestone
        self.round_count = 0  # user messages issued to the agent
        # Per-turn decisions, for the transcript artifact / RLVR diagnostics.
        self.decisions: list[dict] = []

    # ------------------------------------------------------------------ state

    @property
    def current_milestone(self) -> Milestone | None:
        if self.milestone_ptr >= len(self.scenario.milestones):
            return None
        return self.scenario.milestones[self.milestone_ptr]

    @property
    def next_milestone(self) -> Milestone | None:
        nxt = self.milestone_ptr + 1
        if nxt >= len(self.scenario.milestones):
            return None
        return self.scenario.milestones[nxt]

    @property
    def is_done(self) -> bool:
        """Loop-top guard: stop when all milestones are done or the round budget is spent."""
        return (
            self.milestone_ptr >= len(self.scenario.milestones)
            or self.round_count >= self.scenario.max_rounds
        )

    @property
    def should_stop_before_run(self) -> bool:
        """A terminal message (all milestones done) must not run as an agent round."""
        return self.milestone_ptr >= len(self.scenario.milestones)

    # ------------------------------------------------------------- main entry

    async def next_user_message(
        self,
        agent_output: str,
        initial_task: str,
        *,
        workspace_evidence: str = "",
    ) -> str:
        """Return the instruction (user message) for the next agent round.

        - Round 1 returns ``initial_task`` (the task's instruction.md).
        - Later rounds call the user-LLM to judge the previous output and speak;
          the milestone pointer advances / stays / force-advances accordingly.
        - ``workspace_evidence`` is an opaque, caller-supplied description of the
          files the agent actually changed (so the user judges real evidence).
        """
        if self.round_count == 0:
            self.round_count = 1
            return initial_task

        self.round_count += 1
        current = self.current_milestone
        assert current is not None  # is_done guards the caller's loop
        nxt = self.next_milestone

        decision = await self.simulator.judge_and_speak(
            current, nxt, agent_output, workspace_evidence=workspace_evidence
        )
        forced = False
        if decision.action == "answer" and self.clarification_count < self.scenario.max_clarifications:
            # Clarification sub-loop: the agent's output was primarily questions;
            # the user answers them. Stays on the SAME milestone, consumes a round
            # but NOT a correction, does NOT advance. The answer is the next
            # instruction.
            self.clarification_count += 1
            message = decision.message
        elif decision.satisfied:
            self.milestone_ptr += 1
            self.correction_count = 0
            self.clarification_count = 0
            message = decision.message
        else:
            # Over-clarification (action="answer" past the budget) degrades into a
            # normal correction: the message is still delivered as feedback.
            self.correction_count += 1
            if self.correction_count > self.scenario.max_corrections:
                forced = True
                self.milestone_ptr += 1
                self.correction_count = 0
                self.clarification_count = 0
                new_current = self.current_milestone  # post-advance = milestone to re-request
                message = (
                    await self.simulator.render_milestone(
                        new_current,
                        agent_output=agent_output,
                        workspace_evidence=workspace_evidence,
                    )
                    if new_current is not None
                    else decision.message
                )
            else:
                message = decision.message

        self.decisions.append(
            {
                "round": self.round_count,
                "milestone_index": current.index,
                "action": decision.action,
                "satisfied": decision.satisfied,
                "clarification_count": self.clarification_count,
                "forced_advance": forced,
                "message": message,
                "workspace_evidence": workspace_evidence,
            }
        )
        # Record only the message actually delivered to the agent (a discarded
        # corrective message stays visible in ``decisions`` for analysis).
        self.simulator.record_turn(agent_output, message)
        return message
