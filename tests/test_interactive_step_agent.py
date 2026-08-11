"""Tests for InteractiveStepClaude (Design B per-step agent; no Docker)."""

from __future__ import annotations

import asyncio
import json

import pytest
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.agent.context import AgentContext

from benchmark.interactive_step_agent import InteractiveStepClaude

AGENT_STREAM = (
    '{"type":"assistant","message":{"role":"assistant","content":'
    '[{"type":"text","text":"building the CLI"}]}}\n'
    '{"type":"result","subtype":"success","result":"Step done.","total_cost_usd":0.05}\n'
)


class _FakeResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class _FakeEnvironment:
    """Serves claude-code.txt (agent output) + a small /workspace."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, str] = {"/workspace/src/stats.py": "print('stats')\n"}

    async def exec(self, command: str, **kwargs):  # noqa: ANN001
        self.commands.append(command)
        if "claude-code.txt" in command:
            return _FakeResult(AGENT_STREAM)
        if command.startswith("find /workspace"):
            return _FakeResult(
                "\n".join(f"{path}\t{len(content)}" for path, content in self.files.items())
            )
        if command.startswith("cat -- "):
            path = command[len("cat -- "):].strip().strip("'")
            return _FakeResult(self.files.get(path, ""))
        return _FakeResult("")


def test_run_runs_one_claude_and_dumps_summary_and_snapshot(tmp_path, monkeypatch) -> None:
    run_instructions: list[str] = []

    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        run_instructions.append(instruction)

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)

    agent = InteractiveStepClaude(logs_dir=str(tmp_path))
    env = _FakeEnvironment()
    asyncio.run(agent.run(instruction="step instruction", environment=env, context=AgentContext()))

    # Exactly one claude invocation with the step's instruction.
    assert run_instructions == ["step instruction"]

    # The extracted agent summary is dumped for the between-step runner.
    summary = (tmp_path / "agent_summary.txt").read_text(encoding="utf-8")
    assert "Step done." in summary

    # The workspace snapshot is dumped (real files the agent changed).
    snapshot = json.loads((tmp_path / "workspace_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {"/workspace/src/stats.py": "print('stats')\n"}


def test_run_tolerates_empty_agent_output(tmp_path, monkeypatch) -> None:
    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        pass

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)

    class _NoOutputEnv(_FakeEnvironment):
        async def exec(self, command: str, **kwargs):  # noqa: ANN001
            if "claude-code.txt" in command:
                return _FakeResult("")
            return await super().exec(command, **kwargs)

    agent = InteractiveStepClaude(logs_dir=str(tmp_path))
    asyncio.run(agent.run(instruction="x", environment=_NoOutputEnv(), context=AgentContext()))

    assert (tmp_path / "agent_summary.txt").read_text(encoding="utf-8") == ""
    snapshot = json.loads((tmp_path / "workspace_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {"/workspace/src/stats.py": "print('stats')\n"}


def test_name() -> None:
    assert InteractiveStepClaude.name() == "interactive-step-claude"


def test_constructible_like_interactive_agent(tmp_path) -> None:
    agent = InteractiveStepClaude(logs_dir=str(tmp_path))
    assert agent is not None
