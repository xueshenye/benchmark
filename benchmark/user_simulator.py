"""Simulated interactive user backed by an LLM.

Drives the multi-turn protocol: for each round >= 2, the user-LLM is asked to
judge whether the agent's previous output satisfied the current milestone and
to produce the next natural user message as strict JSON ``{"satisfied": bool,
"message": str}``. The controller (`benchmark.controller.TurnController`)
decides advance / correct / force-advance from that decision.

The judge/render methods are *pure* (they never touch the transcript); the
controller records each actually-delivered message via ``record_turn`` so the
transcript reflects only what the user really said. Uses Harbor's ``LiteLLM``
by default; an LLM callable can be injected for tests.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel

from benchmark.prompt_templates import (
    build_turn_decision_prompt,
    build_user_message_prompt,
)
from benchmark.scenario import Milestone, Scenario

logger = logging.getLogger(__name__)

# A callable that takes a prompt string and returns the LLM's text output.
LLMCall = Callable[[str], Awaitable[str]]


class TurnDecision(BaseModel):
    """The user-LLM's structured decision for one turn.

    ``action`` selects the controller's branch:
    - ``"judge"`` (default): normal judgment — advance / correct / force-advance
      based on ``satisfied``.
    - ``"answer"``: the agent's output was primarily clarifying questions; the
      user answers them. The message becomes the next instruction on the SAME
      milestone (clarification sub-loop; no correction consumed, no advance).
    """

    message: str
    satisfied: bool = False
    action: Literal["judge", "answer"] = "judge"


def parse_turn_decision(raw: str) -> TurnDecision:
    """Parse the user-LLM's strict-JSON reply into a ``TurnDecision``.

    Tolerates an optional ``````json```` fence. Raises ``ValueError`` on
    malformed JSON or a non-object payload. Unknown/absent ``action`` falls back
    to ``"judge"`` (conservative — never advance or answer on a misread).
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        raise ValueError("empty user-LLM output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"user-LLM output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"user-LLM output is not a JSON object: {data!r}")
    action = data.get("action", "judge")
    if action not in ("judge", "answer"):
        action = "judge"
    return TurnDecision(
        action=action,
        satisfied=bool(data.get("satisfied", False)),
        message=str(data.get("message", "")).strip(),
    )


class UserSimulator:
    """Role-plays the interactive user across the scenario's milestones."""

    def __init__(
        self,
        scenario: Scenario,
        llm: LLMCall | None = None,
        *,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.7,
    ) -> None:
        self.scenario = scenario
        self._llm = llm  # injected callable(prompt)->str; None → Harbor LiteLLM
        self.model = model or os.environ.get("USER_LLM_MODEL")
        self.api_base = api_base or os.environ.get("USER_LLM_API_BASE")
        self.api_key = (
            api_key
            or os.environ.get("USER_LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        self.temperature = temperature
        # Conversation so far: role "user" = simulated user, role "assistant" = the agent.
        self._transcript: list[dict[str, str]] = []
        # Best-effort token/cost accounting from the user-LLM calls.
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: float = 0.0

    @property
    def transcript(self) -> list[dict[str, str]]:
        return list(self._transcript)

    def start(self, initial_task: str) -> None:
        """Record round 1: the initial task comes from the task's instruction.md."""
        self._transcript = [{"role": "user", "content": initial_task}]

    async def judge_and_speak(
        self,
        current: Milestone,
        nxt: Milestone | None,
        agent_output: str,
        *,
        workspace_evidence: str = "",
    ) -> TurnDecision:
        """Judge the agent's latest output against ``current`` and produce the
        next user message. Pure: does not mutate the transcript.

        ``workspace_evidence`` is a caller-supplied description of the files the
        agent actually changed, so the judgement is grounded in real evidence.
        """
        prompt = build_turn_decision_prompt(
            persona=self.scenario.user_persona,
            current=current,
            nxt=nxt,
            num_milestones=len(self.scenario.milestones),
            transcript=self._transcript,
            agent_output=agent_output,
            workspace_evidence=workspace_evidence,
            max_clarifications=self.scenario.max_clarifications,
        )
        raw = (await self._call_llm(prompt)).strip()
        try:
            decision = parse_turn_decision(raw)
        except ValueError as exc:
            if not raw:
                raise RuntimeError(f"user-LLM returned an empty message for milestone {current.index}") from exc
            # Conservative fallback: treat unparseable output as unsatisfied and
            # use the raw text as the user's speech (keeps the agent on the
            # current milestone rather than advancing on a misread).
            logger.warning(
                "user-LLM returned non-JSON for milestone %d; treating as unsatisfied: %s",
                current.index,
                exc,
            )
            decision = TurnDecision(satisfied=False, message=raw)
        if not decision.message:
            raise RuntimeError(
                f"user-LLM returned a decision with an empty message for milestone {current.index}"
            )
        return decision

    async def render_milestone(
        self,
        milestone: Milestone,
        *,
        agent_output: str = "",
        workspace_evidence: str = "",
    ) -> str:
        """Render a milestone's intent into a natural user message (used when a
        forced advance re-requests the next milestone). Pure: does not mutate
        the transcript."""
        prompt = build_user_message_prompt(
            persona=self.scenario.user_persona,
            milestone=milestone,
            num_milestones=len(self.scenario.milestones),
            transcript=self._transcript,
            agent_output=agent_output,
            workspace_evidence=workspace_evidence,
        )
        message = (await self._call_llm(prompt)).strip()
        if not message:
            raise RuntimeError(f"user-LLM returned an empty message for milestone {milestone.index}")
        return message

    def record_turn(self, agent_output: str, message: str) -> None:
        """Record one delivered exchange: the agent's output, then the user's message."""
        self._transcript.append({"role": "assistant", "content": agent_output})
        self._transcript.append({"role": "user", "content": message})

    async def _call_llm(self, prompt: str) -> str:
        if self._llm is not None:
            return await self._llm(prompt)
        if not self.model:
            raise RuntimeError(
                "USER_LLM_MODEL is not set; configure the user-LLM via env vars "
                "(USER_LLM_MODEL / USER_LLM_API_BASE / USER_LLM_API_KEY)"
            )
        from harbor.llms.lite_llm import LiteLLM

        llm = LiteLLM(
            model_name=self.model,
            api_base=self.api_base,
            temperature=self.temperature,
        )
        if self.api_key:
            llm._llm_kwargs["api_key"] = self.api_key  # type: ignore[attr-defined]
        resp = await llm.call(prompt=prompt)
        self._accumulate_usage(resp)
        return resp.content or ""

    def _accumulate_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
        cost = getattr(usage, "cost_usd", None)
        if cost:
            self.cost_usd += float(cost)
