"""Per-step agent for native multi-step (Design B).

Runs ONE claude invocation for the step's instruction (via the base
``ClaudeCode.run`` — NOT the multi-round loop that ``InteractiveUserClaude``
drives), then dumps:

- ``agent_summary.txt`` — the extracted assistant text from the stream-json
- ``workspace_snapshot.json`` — the /workspace file snapshot

Both land in the agent's ``logs_dir``, which Harbor archives to
``trial_dir/steps/{name}/agent/`` after each step, so the between-step runner
can read them on the host and feed the user-LLM real workspace evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmark.interactive_agent import InteractiveUserClaude


class InteractiveStepClaude(InteractiveUserClaude):
    """Single-round claude agent that snapshots the workspace after running."""

    @staticmethod
    def name() -> str:
        return "interactive-step-claude"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # One plain claude --print run for this step's instruction.
        await ClaudeCode.run(
            self,
            instruction=instruction,
            environment=environment,
            context=context,
        )

        summary = await self._read_agent_output(environment)
        snapshot = await self._gather_workspace_snapshot(environment)

        logs = Path(self.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "agent_summary.txt").write_text(summary, encoding="utf-8")
        (logs / "workspace_snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
