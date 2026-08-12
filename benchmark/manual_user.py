"""Manual 'human plays the user' simulator for the interactive benchmark.

Replaces the user-LLM: a real person reads each milestone's requirement /
user_intent / user_knowledge plus the agent's actual output and workspace diff,
then types the next user message (and the satisfied judgement) by hand. This is
how a task author runs a demo like a human user, inputs test instructions, and
judges pass/fail against the milestone's evaluation criteria before trusting a
user-LLM.

Enabled by setting ``USER_SIMULATOR=manual`` in the environment. The agent still
needs its own LLM backend (``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN``),
but no user-LLM credentials are required.

Duck-types ``benchmark.user_simulator.UserSimulator`` (start / judge_and_speak /
render_milestone / record_turn / transcript) so ``TurnController`` drives it
unchanged. The controller's ``action`` protocol is preserved: ``judge`` (advance
or correct by ``satisfied``) vs ``answer`` (clarification sub-loop, stays on the
same milestone without consuming a correction).
"""

from __future__ import annotations

import asyncio
import sys

from benchmark.scenario import Milestone, Scenario
from benchmark.user_simulator import TurnDecision, parse_turn_decision


class ManualUser:
    """Interactive human-in-the-loop stand-in for the simulated user."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.model = "manual"  # so the agent's metadata/reporting stays uniform
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self._transcript: list[dict[str, str]] = []

    @property
    def transcript(self) -> list[dict[str, str]]:
        return list(self._transcript)

    def start(self, initial_task: str) -> None:
        """Record round 1 (the task's instruction.md) and welcome the human."""
        self._transcript = [{"role": "user", "content": initial_task}]
        print(
            "\n===== 真人扮演用户模式 ====="
            "\n你正在扮演模拟用户。每轮会显示当前里程碑的意图/评价标准/你掌握的信息,"
            "\n以及 agent 的实际输出和工作区改动,请你据此判断并输入下一条用户消息。"
            "\n输入格式(三选一,或严格 JSON):"
            "\n  s <消息>     满意,推进到下一里程碑(消息=下一需求/收尾)"
            "\n  c <消息>     不满意,给出纠正(留在当前里程碑)"
            "\n  a <消息>     agent 在提问澄清,你回答它(留在当前里程碑)"
            "\n  或 JSON: {\"action\": \"judge\"|\"answer\", \"satisfied\": true|false, \"message\": \"...\"}"
            f"\n澄清预算:每个里程碑最多 {self.scenario.max_clarifications} 次问答。"
            "\n==========================================",
            flush=True,
        )

    # ------------------------------------------------------------- protocol

    async def judge_and_speak(
        self,
        current: Milestone,
        nxt: Milestone | None,
        agent_output: str,
        *,
        workspace_evidence: str = "",
    ) -> TurnDecision:
        """Show the milestone's contract + the agent's real work, then ask the human."""
        self._print_context(current, agent_output, workspace_evidence)
        next_hint = ""
        if nxt is not None:
            next_hint = f"\n【下一个里程碑的意图】{nxt.user_intent}"
        while True:
            line = await self._read_line(
                "\n你(用户)的判定和消息"
                + next_hint
                + "\n  s/c/a <消息>,或 JSON(回车重输) > "
            )
            decision = self._parse_human_input(line)
            if decision is not None and decision.message:
                return decision
            print("  !! 无法解析,请按 s/c/a <消息> 或严格 JSON 输入。")

    async def render_milestone(
        self,
        milestone: Milestone,
        *,
        agent_output: str = "",
        workspace_evidence: str = "",
    ) -> str:
        """Forced advance: re-request the milestone from the human (default: user_intent)."""
        self._print_context(milestone, agent_output, workspace_evidence)
        print("\n[强制推进] 该里程碑将由 verifier 判 0,请给出推进到它的自然用户消息。", flush=True)
        line = await self._read_line("  输入消息(回车默认用 user_intent)> ")
        return line.strip() or milestone.user_intent

    def record_turn(self, agent_output: str, message: str) -> None:
        self._transcript.append({"role": "assistant", "content": agent_output})
        self._transcript.append({"role": "user", "content": message})

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _parse_human_input(text: str) -> TurnDecision | None:
        """Accept ``s/c/a <message>`` shorthand or strict JSON (via the LLM parser)."""
        text = (text or "").strip()
        if not text:
            return None
        if len(text) > 1 and text[0] in ("s", "c", "a") and text[1] in (" ", "\t"):
            kind, rest = text[0], text[1:].strip()
            if not rest:
                return None
            if kind == "s":
                return TurnDecision(action="judge", satisfied=True, message=rest)
            if kind == "c":
                return TurnDecision(action="judge", satisfied=False, message=rest)
            return TurnDecision(action="answer", satisfied=False, message=rest)
        try:
            return parse_turn_decision(text)
        except ValueError:
            return None

    @staticmethod
    async def _read_line(prompt: str) -> str:
        try:
            return await asyncio.to_thread(input, prompt)
        except (EOFError, KeyboardInterrupt):
            print("\n[manual user] 输入流结束,退出。", file=sys.stderr)
            raise SystemExit(0)

    def _print_context(
        self, milestone: Milestone, agent_output: str, workspace_evidence: str
    ) -> None:
        print("\n" + "=" * 70)
        print(f"【里程碑 {milestone.index}/{len(self.scenario.milestones)}】")
        print("-" * 70)
        print("【用户意图(user_intent)】")
        print(milestone.user_intent)
        print("【评价标准(requirement,ground truth,仅供你判断)】")
        print(milestone.requirement)
        if milestone.user_knowledge:
            print("【你作为用户掌握的信息(user_knowledge,agent 问到时才透露)】")
            print(milestone.user_knowledge)
        print("-" * 70)
        print("【agent 上一轮输出】")
        print(agent_output or "(空)")
        print("【agent 本轮工作区改动】")
        print(workspace_evidence or "(本轮无文件改动)")
        print("=" * 70)
