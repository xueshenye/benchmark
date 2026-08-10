"""Tests for the simulated user (UserSimulator)."""

from __future__ import annotations

import asyncio

import pytest

from benchmark.scenario import Scenario
from benchmark.user_simulator import UserSimulator

SCENARIO = Scenario.model_validate(
    {
        "num_rounds": 3,
        "user_persona": "一位简洁的产品经理",
        "rounds": [
            {"index": 1, "requirement": "basic", "user_intent": "从零构建 stats CLI", "test_id": "t1"},
            {"index": 2, "requirement": "json", "user_intent": "加一个 --output-json 选项", "test_id": "t2"},
            {"index": 3, "requirement": "multi", "user_intent": "支持多个文件", "test_id": "t3"},
        ],
    }
)


def test_message_conditioned_on_intent_and_agent_output() -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "  现在加一个 --output-json 选项  "

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("initial task description")

    message = asyncio.run(sim.next_message(2, "Agent built a basic stats CLI."))

    # The generated message is the LLM's output, trimmed.
    assert message == "现在加一个 --output-json 选项"
    # The prompt references the round's intent and the agent's actual output.
    assert "--output-json" in prompts[0]
    assert "Agent built a basic stats CLI." in prompts[0]
    # Conversation transcript advanced: [user initial, assistant output, user message].
    assert sim.transcript[0] == {"role": "user", "content": "initial task description"}
    assert sim.transcript[-2] == {"role": "assistant", "content": "Agent built a basic stats CLI."}
    assert sim.transcript[-1]["role"] == "user"


def test_empty_llm_message_raises() -> None:
    async def fake_llm(prompt: str) -> str:
        return "   "

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")
    with pytest.raises(RuntimeError):
        asyncio.run(sim.next_message(2, "out"))


def test_missing_model_raises_when_no_llm_injected(monkeypatch) -> None:
    monkeypatch.delenv("USER_LLM_MODEL", raising=False)
    sim = UserSimulator(SCENARIO)
    sim.start("task")
    with pytest.raises(RuntimeError, match="USER_LLM_MODEL"):
        asyncio.run(sim.next_message(2, "out"))
