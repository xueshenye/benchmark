"""Tests for the interactive round loop in InteractiveUserClaude (no Docker).

mocks the claude-code subprocess (ClaudeCode.run) and the environment exec, so
the loop logic runs on the host.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.agent.context import AgentContext

from benchmark.interactive_agent import InteractiveUserClaude

SCENARIO_JSON = json.dumps(
    {
        "num_rounds": 3,
        "user_persona": "产品经理",
        "rounds": [
            {"index": 1, "requirement": "basic", "user_intent": "从零构建 stats CLI", "test_id": "t1"},
            {"index": 2, "requirement": "json", "user_intent": "加 --output-json", "test_id": "t2"},
            {"index": 3, "requirement": "multi", "user_intent": "支持多个文件", "test_id": "t3"},
        ],
    }
)

AGENT_STREAM = (
    '{"type":"assistant","message":{"role":"assistant","content":'
    '[{"type":"text","text":"inspecting repo"}]}}\n'
    '{"type":"result","subtype":"success","result":"Finished the round.","total_cost_usd":0.05}\n'
)


class _FakeResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class _FakeEnvironment:
    """Mimics BaseEnvironment.exec for scenario + agent-output reads."""

    def __init__(self, scenario_text: str, agent_text: str) -> None:
        self.scenario_text = scenario_text
        self.agent_text = agent_text
        self.commands: list[str] = []

    async def exec(self, command: str, **kwargs):  # noqa: ANN001
        self.commands.append(command)
        if "scenario.json" in command:
            return _FakeResult(self.scenario_text)
        if "claude-code.txt" in command:
            return _FakeResult(self.agent_text)
        return _FakeResult("")


def test_round_loop_drives_claude_per_round_with_user_message_only(
    tmp_path, monkeypatch
) -> None:
    run_instructions: list[str] = []

    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        run_instructions.append(instruction)

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)

    user_llm_calls: list[str] = []

    async def fake_user_llm(prompt: str) -> str:
        user_llm_calls.append(prompt)
        return f"user message for round {len(user_llm_calls) + 1}"

    agent = InteractiveUserClaude(logs_dir=str(tmp_path), user_llm=fake_user_llm)
    env = _FakeEnvironment(SCENARIO_JSON, AGENT_STREAM)
    context = AgentContext()

    asyncio.run(agent.run(instruction="INITIAL TASK", environment=env, context=context))

    # Round 1 gets the initial instruction; rounds 2-3 get ONLY the simulated user's message.
    assert run_instructions == [
        "INITIAL TASK",
        "user message for round 2",
        "user message for round 3",
    ]
    # The user-LLM was invoked once per follow-up round (2 follow-ups), and each
    # prompt observed the previous round's actual agent output.
    assert len(user_llm_calls) == 2
    assert "Finished the round." in user_llm_calls[0]
    assert "Finished the round." in user_llm_calls[1]

    # Transcript artifact written with the full conversation for analysis/RLVR.
    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    assert artifact["num_rounds"] == 3
    assert artifact["transcript"][0]["content"] == "INITIAL TASK"
    assert artifact["transcript"][-1]["role"] == "user"
    assert len(artifact["agent_outputs"]) == 3

    # Context reflects the interactive protocol.
    assert context.metadata["num_rounds"] == 3


def test_scenario_missing_in_environment_raises(tmp_path, monkeypatch) -> None:
    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        pass

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)
    agent = InteractiveUserClaude(logs_dir=str(tmp_path), user_llm=lambda p: None)
    env = _FakeEnvironment("", AGENT_STREAM)

    async def run():
        await agent.run(
            instruction="task", environment=env, context=AgentContext()
        )

    with pytest.raises(RuntimeError, match="scenario.json"):
        asyncio.run(run())
