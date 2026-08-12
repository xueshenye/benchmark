"""Host-side unit tests for the manual human-user mode.

Covers the ``ManualUser`` shorthand parser, the protocol methods (with
``_read_line`` monkeypatched so no real stdin is needed), and the
``USER_SIMULATOR=manual`` wiring into ``InteractiveUserClaude.run`` via a fake
environment (mirrors ``test_round_loop.py``).
"""

from __future__ import annotations

import asyncio
import json

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.agent.context import AgentContext

import benchmark.manual_user as manual_user_mod
from benchmark.interactive_agent import InteractiveUserClaude, _make_simulator
from benchmark.scenario import Scenario
from benchmark.user_simulator import UserSimulator

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
    '[{"type":"text","text":"working"}]}}\n'
    '{"type":"result","subtype":"success","result":"Finished the round."}\n'
)


def _scenario() -> Scenario:
    return Scenario.parse(SCENARIO_JSON)


def _canned_read_line(replies: list[str]):
    """Monkeypatch target for ``ManualUser._read_line``: yields ``replies`` in order."""
    it = iter(replies)

    async def _read(self, prompt: str) -> str:  # noqa: ANN001
        return next(it)

    return _read


# ------------------------------------------------------------- shorthand parser

def test_parse_human_input_shorthands() -> None:
    m = manual_user_mod.ManualUser(_scenario())
    s = m._parse_human_input("s 好的,继续")
    assert s.action == "judge" and s.satisfied is True and s.message == "好的,继续"
    c = m._parse_human_input("c 你没做X")
    assert c.action == "judge" and c.satisfied is False and c.message == "你没做X"
    a = m._parse_human_input("a 知识库在 /workspace")
    assert a.action == "answer" and a.satisfied is False and a.message == "知识库在 /workspace"


def test_parse_human_input_json_and_garbage() -> None:
    m = manual_user_mod.ManualUser(_scenario())
    j = m._parse_human_input('{"action": "judge", "satisfied": true, "message": "ok"}')
    assert j is not None and j.satisfied is True and j.message == "ok"
    assert m._parse_human_input("hello world") is None
    assert m._parse_human_input("") is None
    assert m._parse_human_input("s") is None  # shorthand without a message


# ------------------------------------------------------------- protocol methods

def test_judge_and_speak_reprompts_on_malformed(monkeypatch) -> None:
    m = manual_user_mod.ManualUser(_scenario())
    monkeypatch.setattr(
        manual_user_mod.ManualUser, "_read_line",
        _canned_read_line(["garbage not json", "s 现在达标了,继续"]),
    )
    current = _scenario().milestones[0]

    async def run():
        return await m.judge_and_speak(current, None, "agent output", workspace_evidence="x")

    decision = asyncio.run(run())
    # First reply was malformed → reprompted; the second (valid) one wins.
    assert decision.satisfied is True and decision.message == "现在达标了,继续"


def test_render_milestone_defaults_to_user_intent(monkeypatch) -> None:
    m = manual_user_mod.ManualUser(_scenario())
    monkeypatch.setattr(manual_user_mod.ManualUser, "_read_line", _canned_read_line([""]))
    milestone = _scenario().milestones[1]

    async def run():
        return await m.render_milestone(milestone)

    assert asyncio.run(run()) == milestone.user_intent


def test_record_turn_appends_transcript() -> None:
    m = manual_user_mod.ManualUser(_scenario())
    m.start("INITIAL TASK")
    m.record_turn("agent round 2", "用户消息 2")
    assert m.transcript[0] == {"role": "user", "content": "INITIAL TASK"}
    assert m.transcript[-2] == {"role": "assistant", "content": "agent round 2"}
    assert m.transcript[-1] == {"role": "user", "content": "用户消息 2"}


# ------------------------------------------------------------- wiring

def test_make_simulator_swaps_on_env(monkeypatch) -> None:
    monkeypatch.setenv("USER_SIMULATOR", "manual")
    assert isinstance(_make_simulator(_scenario(), None), manual_user_mod.ManualUser)
    monkeypatch.delenv("USER_SIMULATOR")
    assert isinstance(_make_simulator(_scenario(), None), UserSimulator)


class _FakeResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class _FakeEnvironment:
    def __init__(self, scenario_text: str, agent_text: str) -> None:
        self.scenario_text = scenario_text
        self.agent_text = agent_text

    async def exec(self, command: str, **kwargs):  # noqa: ANN001
        if "scenario.json" in command:
            return _FakeResult(self.scenario_text)
        if "claude-code.txt" in command:
            return _FakeResult(self.agent_text)
        return _FakeResult("")


def test_manual_user_drives_the_round_loop(tmp_path, monkeypatch) -> None:
    """With USER_SIMULATOR=manual, the loop is driven by the human's typed messages."""
    monkeypatch.setattr(
        manual_user_mod.ManualUser, "_read_line",
        _canned_read_line(["s 好的,继续", "s 下一个需求", "s 收尾"]),
    )
    monkeypatch.setenv("USER_SIMULATOR", "manual")
    run_instructions: list[str] = []

    async def fake_claude_run(self, instruction, environment, context):  # noqa: ANN001
        run_instructions.append(instruction)

    monkeypatch.setattr(ClaudeCode, "run", fake_claude_run)

    agent = InteractiveUserClaude(logs_dir=str(tmp_path))
    env = _FakeEnvironment(SCENARIO_JSON, AGENT_STREAM)
    asyncio.run(agent.run(instruction="INITIAL TASK", environment=env, context=AgentContext()))

    # Round 1 = instruction.md; rounds 2-3 = the human's typed advancing messages.
    # The final "收尾" judgement closes milestone 3 but does NOT run as a round.
    assert run_instructions == ["INITIAL TASK", "好的,继续", "下一个需求"]
    artifact = json.loads((tmp_path / "interactive_transcript.json").read_text())
    assert artifact["num_rounds"] == 3
    assert artifact["transcript"][-1]["content"] == "收尾"  # closing, recorded not run
    assert [d["satisfied"] for d in artifact["decisions"]] == [True, True, True]
