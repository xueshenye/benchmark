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
        "user_persona": "产品经理",
        "milestones": [
            {"index": 1, "requirement": "basic", "user_intent": "从零构建 stats CLI", "test_id": "t1"},
            {"index": 2, "requirement": "json", "user_intent": "加 --output-json", "test_id": "t2"},
            {"index": 3, "requirement": "multi", "user_intent": "支持多个文件", "test_id": "t3"},
        ],
        "max_rounds": 6,
        "max_corrections": 1,
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


def _run_loop(
    tmp_path, monkeypatch, *, fake_user_llm, scenario_text: str = SCENARIO_JSON, initial: str = "INITIAL TASK"
):
    run_instructions: list[str] = []

    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        run_instructions.append(instruction)

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)

    agent = InteractiveUserClaude(logs_dir=str(tmp_path), user_llm=fake_user_llm)
    env = _FakeEnvironment(scenario_text, AGENT_STREAM)
    context = AgentContext()
    asyncio.run(agent.run(instruction=initial, environment=env, context=context))
    return run_instructions, context


def test_round_loop_drives_claude_per_round_with_user_message_only(
    tmp_path, monkeypatch
) -> None:
    user_llm_calls: list[str] = []

    async def fake_user_llm(prompt: str) -> str:
        user_llm_calls.append(prompt)
        return json.dumps({"satisfied": True, "message": f"user message for round {len(user_llm_calls) + 1}"})

    run_instructions, context = _run_loop(tmp_path, monkeypatch, fake_user_llm=fake_user_llm)

    # Round 1 gets the initial instruction; rounds 2-3 get ONLY the simulated user's message.
    assert run_instructions == [
        "INITIAL TASK",
        "user message for round 2",
        "user message for round 3",
    ]
    # The user-LLM was invoked once per follow-up round (3: two advancing
    # judgements + one closing judgement that confirms the last milestone), and
    # each prompt observed the previous round's actual agent output.
    assert len(user_llm_calls) == 3
    assert "Finished the round." in user_llm_calls[0]
    assert "Finished the round." in user_llm_calls[1]

    # Transcript artifact written with the full conversation for analysis/RLVR.
    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    assert artifact["num_rounds"] == 3
    assert artifact["max_rounds"] == 6
    assert artifact["transcript"][0]["content"] == "INITIAL TASK"
    assert artifact["transcript"][-1]["role"] == "user"
    assert len(artifact["agent_outputs"]) == 3
    # The decisions log records a satisfied=True per follow-up round (incl. the closing one).
    assert [d["satisfied"] for d in artifact["decisions"]] == [True, True, True]

    # Context reflects the interactive protocol.
    assert context.metadata["num_rounds"] == 3
    assert context.metadata["max_rounds"] == 6
    assert context.metadata["num_milestones"] == 3


def test_correction_round_inserted_when_unsatisfied(tmp_path, monkeypatch) -> None:
    """An unsatisfied judgement keeps the milestone and inserts a corrective round."""
    user_llm_calls: list[str] = []
    script = [
        {"satisfied": False, "message": "corrective 2a"},
        {"satisfied": True, "message": "user message for round 3"},
        {"satisfied": True, "message": "user message for round 4"},
        {"satisfied": True, "message": "thanks, all done"},  # closing judgement for milestone 3
    ]

    async def fake_user_llm(prompt: str) -> str:
        user_llm_calls.append(prompt)
        return json.dumps(script[len(user_llm_calls) - 1])

    run_instructions, context = _run_loop(tmp_path, monkeypatch, fake_user_llm=fake_user_llm)

    # One extra corrective round before the milestone is accepted.
    assert run_instructions == [
        "INITIAL TASK",
        "corrective 2a",
        "user message for round 3",
        "user message for round 4",
    ]
    assert len(user_llm_calls) == 4  # correct, correct-then-accept, advance, closing
    assert context.metadata["num_rounds"] == 4

    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    decisions = artifact["decisions"]
    assert decisions[0]["satisfied"] is False
    assert decisions[0]["forced_advance"] is False
    assert decisions[0]["milestone_index"] == 1
    assert all(d["satisfied"] for d in decisions[1:])
    assert decisions[1]["milestone_index"] == 1  # same milestone, accepted on the next judge


def test_clarification_round_does_not_advance(tmp_path, monkeypatch) -> None:
    """An action=answer round (agent asks → user answers) stays on the SAME milestone."""
    script = [
        {"action": "answer", "message": "知识库在 /workspace/knowledge_base"},
        {"satisfied": True, "message": "user message for round 3"},
        {"satisfied": True, "message": "user message for round 4"},
        {"satisfied": True, "message": "thanks, all done"},  # closing judgement for milestone 3
    ]
    user_llm_calls: list[str] = []

    async def fake_user_llm(prompt: str) -> str:
        user_llm_calls.append(prompt)
        return json.dumps(script[len(user_llm_calls) - 1])

    run_instructions, context = _run_loop(tmp_path, monkeypatch, fake_user_llm=fake_user_llm)

    # The user's answer becomes the round-2 instruction; the milestone does not advance.
    assert run_instructions == [
        "INITIAL TASK",
        "知识库在 /workspace/knowledge_base",
        "user message for round 3",
        "user message for round 4",
    ]
    assert len(user_llm_calls) == 4  # answer, advance m1, advance m2, closing

    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    decisions = artifact["decisions"]
    assert decisions[0]["action"] == "answer"
    assert decisions[0]["milestone_index"] == 1
    assert decisions[0]["satisfied"] is False
    assert decisions[1]["milestone_index"] == 1  # still milestone 1 when it finally advances
    assert artifact["num_clarifications"] == 1
    assert context.metadata["num_clarifications"] == 1
    assert context.metadata["max_clarifications"] == 2


def test_max_rounds_cap_bounds_the_loop(tmp_path, monkeypatch) -> None:
    """max_rounds=3 with a persistently-unsatisfied user stops after exactly 3 runs."""
    user_llm_calls: list[str] = []
    responses = iter(
        [
            json.dumps({"satisfied": False, "message": "corrective"}),
            json.dumps({"satisfied": False, "message": "corrective again"}),
            "rendered next milestone request",  # render_milestone path after force-advance
        ]
    )

    async def fake_user_llm(prompt: str) -> str:
        user_llm_calls.append(prompt)
        return next(responses)

    capped = json.loads(SCENARIO_JSON)
    capped["max_rounds"] = 3

    run_instructions, context = _run_loop(
        tmp_path, monkeypatch, fake_user_llm=fake_user_llm, scenario_text=json.dumps(capped)
    )

    assert len(run_instructions) == 3  # the cap round runs; nothing is silently dropped
    assert context.metadata["num_rounds"] == 3
    assert len(user_llm_calls) == 3  # judge, judge, then the forced re-render

    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    # The second correction exhausted the budget → forced advance.
    assert [d["forced_advance"] for d in artifact["decisions"]] == [False, True]


class _WorkspaceFakeEnvironment(_FakeEnvironment):
    """Fake env whose /workspace grows as the agent "writes" files."""

    def __init__(self, scenario_text: str, agent_text: str) -> None:
        super().__init__(scenario_text, agent_text)
        self.active_files: dict[str, str] = {}

    def add_files(self, files: dict[str, str]) -> None:
        self.active_files.update(files)

    async def exec(self, command: str, **kwargs):  # noqa: ANN001
        self.commands.append(command)
        if "scenario.json" in command:
            return _FakeResult(self.scenario_text)
        if "claude-code.txt" in command:
            return _FakeResult(self.agent_text)
        if command.startswith("find /workspace"):
            return _FakeResult(
                "\n".join(f"{path}\t{len(content)}" for path, content in self.active_files.items())
            )
        if command.startswith("cat -- "):
            path = command[len("cat -- "):].strip().strip("'")
            return _FakeResult(self.active_files.get(path, ""))
        return _FakeResult("")


def test_workspace_evidence_grounds_the_judgement(tmp_path, monkeypatch) -> None:
    """The user-LLM sees the real files the agent changed, not just its words."""
    prompts: list[str] = []

    async def fake_user_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"satisfied": True, "message": "看到了改动,继续"})

    env = _WorkspaceFakeEnvironment(SCENARIO_JSON, AGENT_STREAM)
    run_instructions: list[str] = []
    call_count = 0

    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        nonlocal call_count
        run_instructions.append(instruction)
        call_count += 1
        if call_count == 1:  # the agent "writes" a file during round 1
            environment.add_files({"/workspace/src/stats.py": "print('stats')"})

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)
    agent = InteractiveUserClaude(logs_dir=str(tmp_path), user_llm=fake_user_llm)

    asyncio.run(agent.run(instruction="INITIAL TASK", environment=env, context=AgentContext()))

    # The judge for round 2 saw the diff of what round 1 actually changed.
    assert "print('stats')" in prompts[0]

    # The evidence is auditable in the transcript's decisions log.
    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    assert "/workspace/src/stats.py" in artifact["decisions"][0]["workspace_evidence"]


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
