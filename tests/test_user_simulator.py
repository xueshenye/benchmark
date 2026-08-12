"""Tests for the simulated user (UserSimulator): turn decisions + rendering."""

from __future__ import annotations

import asyncio
import json

import pytest

from benchmark.scenario import Scenario
from benchmark.user_simulator import UserSimulator, parse_turn_decision

SCENARIO = Scenario.model_validate(
    {
        "user_persona": "一位简洁的产品经理",
        "milestones": [
            {"index": 1, "requirement": "basic", "user_intent": "从零构建 stats CLI", "test_id": "t1"},
            {"index": 2, "requirement": "json", "user_intent": "加一个 --output-json 选项", "test_id": "t2"},
            {"index": 3, "requirement": "multi", "user_intent": "支持多个文件", "test_id": "t3"},
        ],
        "max_rounds": 6,
        "max_corrections": 1,
    }
)


def test_judge_and_speak_returns_parsed_decision() -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"satisfied": True, "message": "做得好,现在加 --output-json 吧"})

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("initial task description")

    decision = asyncio.run(sim.judge_and_speak(SCENARIO.milestones[0], SCENARIO.milestones[1], "Agent built a basic stats CLI."))

    assert decision.satisfied is True
    assert decision.message == "做得好,现在加 --output-json 吧"
    # The prompt references the current milestone's requirement and the agent output.
    assert "basic" in prompts[0]
    assert "Agent built a basic stats CLI." in prompts[0]
    # The faithfulness guard discourages inventing requirements beyond the ground truth.
    assert "不要凭空添加" in prompts[0]
    # judge_and_speak is pure: it must not mutate the transcript.
    assert len(sim.transcript) == 1
    assert sim.transcript[0] == {"role": "user", "content": "initial task description"}


def test_judge_and_speak_includes_workspace_evidence() -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"satisfied": True, "message": "看到了改动,继续"})

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")

    evidence = "+ /workspace/src/stats.py\nprint('stats')"
    asyncio.run(
        sim.judge_and_speak(
            SCENARIO.milestones[0], SCENARIO.milestones[1], "Agent out", workspace_evidence=evidence
        )
    )

    # The user-LLM actually sees the changed files, not just the agent's words.
    assert evidence in prompts[0]


def test_record_turn_appends_assistant_then_user() -> None:
    sim = UserSimulator(SCENARIO, llm=lambda p: p)  # never called here
    sim.start("task")
    sim.record_turn("agent out", "user msg")
    assert sim.transcript[-2] == {"role": "assistant", "content": "agent out"}
    assert sim.transcript[-1] == {"role": "user", "content": "user msg"}


def test_render_milestone_returns_natural_request() -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "  现在加一个 --output-json 选项  "

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")

    message = asyncio.run(sim.render_milestone(SCENARIO.milestones[1], agent_output="Agent output."))

    assert message == "现在加一个 --output-json 选项"
    assert "--output-json" in prompts[0]
    assert "Agent output." in prompts[0]
    # Pure: transcript untouched.
    assert len(sim.transcript) == 1


def test_malformed_llm_output_falls_back_to_unsatisfied() -> None:
    async def fake_llm(prompt: str) -> str:
        return "这个实现不行,count 算错了,请修正"

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")

    decision = asyncio.run(sim.judge_and_speak(SCENARIO.milestones[0], SCENARIO.milestones[1], "out"))
    assert decision.satisfied is False
    assert decision.message == "这个实现不行,count 算错了,请修正"


def test_parse_turn_decision_strips_fences() -> None:
    raw = '```json\n{"satisfied": true, "message": "很好"}\n```'
    decision = parse_turn_decision(raw)
    assert decision.satisfied is True
    assert decision.message == "很好"


def test_parse_turn_decision_defaults_action_judge() -> None:
    decision = parse_turn_decision(json.dumps({"satisfied": True, "message": "hi"}))
    assert decision.action == "judge"
    assert decision.satisfied is True


def test_parse_turn_decision_parses_action_answer() -> None:
    decision = parse_turn_decision(
        json.dumps({"action": "answer", "message": "知识库在 /workspace/knowledge_base"})
    )
    assert decision.action == "answer"
    assert decision.satisfied is False


def test_parse_turn_decision_unknown_action_falls_back_to_judge() -> None:
    decision = parse_turn_decision(
        json.dumps({"action": "banana", "satisfied": False, "message": "x"})
    )
    assert decision.action == "judge"


def test_judge_prompt_includes_clarification_rule_and_user_knowledge() -> None:
    scenario = Scenario.model_validate(
        {
            "user_persona": "运营",
            "milestones": [
                {
                    "index": 1,
                    "requirement": "kb",
                    "user_intent": "做个客服机器人",
                    "test_id": "kb",
                    "user_knowledge": "知识库在 /workspace/knowledge_base",
                },
            ],
            "max_rounds": 4,
            "max_clarifications": 2,
        }
    )
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"action": "judge", "satisfied": True, "message": "好"})

    sim = UserSimulator(scenario, llm=fake_llm)
    sim.start("task")
    asyncio.run(sim.judge_and_speak(scenario.milestones[0], None, "out"))

    # The user-LLM can answer clarifying questions from its own knowledge…
    assert "知识库在 /workspace/knowledge_base" in prompts[0]
    assert "action" in prompts[0]  # JSON schema includes the new field
    # …while the faithfulness guard (rule 5) stays intact.
    assert "不要凭空添加" in prompts[0]


def test_render_prompt_includes_user_knowledge() -> None:
    scenario = Scenario.model_validate(
        {
            "user_persona": "运营",
            "milestones": [
                {
                    "index": 1,
                    "requirement": "kb",
                    "user_intent": "做个客服机器人",
                    "test_id": "kb",
                    "user_knowledge": "知识库在 /workspace/knowledge_base",
                },
            ],
            "max_rounds": 4,
        }
    )
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "现在做吧"

    sim = UserSimulator(scenario, llm=fake_llm)
    sim.start("task")
    asyncio.run(sim.render_milestone(scenario.milestones[0], agent_output="out"))

    assert "知识库在 /workspace/knowledge_base" in prompts[0]


def test_judge_prompt_without_user_knowledge_omits_section() -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"satisfied": True, "message": "好"})

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")
    asyncio.run(sim.judge_and_speak(SCENARIO.milestones[0], SCENARIO.milestones[1], "out"))

    # No knowledge section header (the milestones carry no user_knowledge).
    assert "【你作为用户自己掌握的信息" not in prompts[0]


def test_parse_turn_decision_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_turn_decision("not json at all")


def test_empty_llm_message_raises() -> None:
    async def fake_llm(prompt: str) -> str:
        return "   "

    sim = UserSimulator(SCENARIO, llm=fake_llm)
    sim.start("task")
    with pytest.raises(RuntimeError):
        asyncio.run(sim.judge_and_speak(SCENARIO.milestones[0], SCENARIO.milestones[1], "out"))


def test_missing_model_raises_when_no_llm_injected(monkeypatch) -> None:
    monkeypatch.delenv("USER_LLM_MODEL", raising=False)
    sim = UserSimulator(SCENARIO)
    sim.start("task")
    with pytest.raises(RuntimeError, match="USER_LLM_MODEL"):
        asyncio.run(sim.judge_and_speak(SCENARIO.milestones[0], SCENARIO.milestones[1], "out"))
