"""Deterministic "last-milestone-only" agent for end-to-end discriminator validation.

Writes ONLY the multi-file (last milestone) implementation to ``/workspace`` and
ignores every user message. Used to confirm on a real Novita run that an agent
which does not complete the early milestones scores ``reward=0`` — the verifier
checks every milestone against the final workspace, so the product reward
collapses to 0.

Register via import path, e.g.::

    harbor run -e novita --env-file .env \\
        -p tasks/benchmark/multi-round-cli-demo \\
        -a benchmark.last_only_agent:LastOnlyClaude -m deepseek-v4-flash
"""

from __future__ import annotations

import base64

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmark.interactive_agent import InteractiveUserClaude

# Only the multi-file behaviour (milestone 3); single-file mode errors out so the
# early milestones fail the verifier's checks.
_MULTI_ONLY = r'''#!/usr/bin/env python3
import argparse, csv, json, sys
def summarize(path):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    values=[]
    for row in rows[1:]:
        if row:
            try: values.append(float(row[0]))
            except ValueError: pass
    return {"count":len(values),"mean":sum(values)/len(values) if values else 0.0,
            "min":min(values) if values else 0.0,"max":max(values) if values else 0.0}
def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--output-json",action="store_true")
    p.add_argument("files",nargs="+")
    a=p.parse_args(argv)
    if len(a.files)<2:
        print("error: single-file mode not supported", file=sys.stderr); return 1
    results=[summarize(f) for f in a.files]
    if a.output_json:
        print(json.dumps(results))
    else:
        for r in results:
            print(f"count={r['count']} mean={r['mean']} min={r['min']} max={r['max']}")
    return 0
if __name__=="__main__":
    sys.exit(main())
'''


class LastOnlyClaude(InteractiveUserClaude):
    """Deliberately incomplete agent: writes only the last milestone and stops."""

    @staticmethod
    def name() -> str:
        return "last-only-claude"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Write the partial implementation via base64 to avoid shell-quoting issues.
        b64 = base64.b64encode(_MULTI_ONLY.encode()).decode()
        command = (
            "mkdir -p /workspace/src && "
            f"printf '%s' '{b64}' | base64 -d > /workspace/src/stats.py && "
            "chmod +x /workspace/src/stats.py"
        )
        await environment.exec(command=command)
        # Skip the interactive loop entirely — this agent never engages.
        if context.metadata is None:
            context.metadata = {}
        context.metadata["deliberately_partial"] = True
