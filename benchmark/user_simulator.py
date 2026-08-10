"""Simulated interactive user backed by an LLM.

Drives the multi-turn protocol: for each round >= 2, turns the scenario's
scripted ``user_intent`` into a natural user message conditioned on the agent's
actual output from the previous round. Uses Harbor's ``LiteLLM`` by default; an
LLM callable can be injected for tests.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable

from benchmark.prompt_templates import build_user_message_prompt
from benchmark.scenario import Scenario

# A callable that takes a prompt string and returns the LLM's text output.
LLMCall = Callable[[str], Awaitable[str]]


class UserSimulator:
    """Role-plays the interactive user across the scenario's rounds."""

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

    async def next_message(self, round_index: int, agent_output: str) -> str:
        """Generate the user message for ``round_index`` (>= 2) from the agent's
        latest output. Advances the internal conversation transcript."""
        round_spec = self.scenario.round_by_index(round_index)
        prompt = build_user_message_prompt(
            persona=self.scenario.user_persona,
            round_spec=round_spec,
            num_rounds=self.scenario.num_rounds,
            transcript=self._transcript,
            agent_output=agent_output,
        )
        message = (await self._call_llm(prompt)).strip()
        if not message:
            raise RuntimeError(f"user-LLM returned an empty message for round {round_index}")
        self._transcript.append({"role": "assistant", "content": agent_output})
        self._transcript.append({"role": "user", "content": message})
        return message

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
