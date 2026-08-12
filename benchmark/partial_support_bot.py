"""Deterministic "first-two-milestones" agent for end-to-end discriminator validation.

Writes a customer-service bot that implements ONLY milestones 1-2 (knowledge-base
Q&A + order API) as a single ``/workspace/support-bot`` script — deliberately NOT
an importable package, with no batch mode, no pytest tests, no English answers
and no escalate-to-human. Used to confirm on a real Novita run that an agent
which stops mid-suite scores ``reward=0``: the verifier checks every milestone
against the final workspace, so the product reward collapses. Expected:

    round_1=1 round_2=1 round_3=0 round_4=0 → reward=0

(An "orders-only" bot would fail round_2 too, because M2's requirement keeps KB
Q&A working — regression — so this partial bot implements KB + orders.)

Register via import path, e.g.::

    harbor run -e novita --env-file .env \\
        -p tasks/benchmark/support-bot \\
        -a benchmark.partial_support_bot:FirstTwoClaude -m deepseek-v4-flash
"""

from __future__ import annotations

import base64

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmark.interactive_agent import InteractiveUserClaude

# KB Q&A + order API only (milestones 1-2). No package, no batch, no English,
# no escalate-to-human — so milestones 3 and 4 fail the verifier's checks.
_PARTIAL = r'''#!/usr/bin/env python3
import json, os, re, sys, urllib.error, urllib.request

KB_DIR = os.environ.get("SUPPORT_KB_DIR", "/workspace/knowledge_base")
API_BASE = os.environ.get("SUPPORT_API_BASE", "http://localhost:8123").rstrip("/")
ORDER_ID = re.compile(r"YGO[\w-]+")
STATUS_ZH = {"pending":"待支付","paid":"已支付","shipped":"已发货",
             "delivered":"已送达","cancelled":"已取消"}

def kb_text():
    parts = []
    for name in ("products.md", "policies.md", "troubleshooting.md"):
        p = os.path.join(KB_DIR, name)
        if os.path.exists(p):
            parts.append(open(p, encoding="utf-8").read())
    return "\n\n".join(parts)

def grams(text, n):
    text = re.sub(r"\s+", "", text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i+n] for i in range(len(text)-n+1)}

def retrieve(q):
    kb = kb_text()
    q2, q3 = grams(q, 2), grams(q, 3)
    best, best_score = None, 0
    for para in [p.strip() for p in re.split(r"\n{2,}", kb) if p.strip()]:
        s = sum(1 for g in q2 if g in para) + 2 * sum(1 for g in q3 if g in para)
        if s > best_score:
            best, best_score = para, s
    if best is None or best_score == 0:
        return None
    return re.sub(r"\s+", " ", best).strip()

def answer(q):
    m = ORDER_ID.search(q)
    if m:
        oid = m.group(0)
        try:
            with urllib.request.urlopen(f"{API_BASE}/api/orders/{oid}", timeout=10) as r:
                order = json.loads(r.read().decode("utf-8"))
            return f"您的订单 {oid} 当前状态是:{STATUS_ZH.get(order.get('status'), order.get('status'))}。"
        except urllib.error.HTTPError:
            return "抱歉,没有查询到该订单,请核对订单号。"
        except Exception:
            return "抱歉,暂时无法查询订单。"
    para = retrieve(q)
    if para is not None:
        return para
    return "抱歉,这个问题我暂时无法回答。"

def main(argv=None):
    if len(sys.argv) < 2:
        return 0
    print(answer(sys.argv[1]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


class FirstTwoClaude(InteractiveUserClaude):
    """Deliberately incomplete agent: KB + order API only, then stops."""

    @staticmethod
    def name() -> str:
        return "first-two-claude"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Write the partial implementation via base64 to avoid shell-quoting issues.
        b64 = base64.b64encode(_PARTIAL.encode()).decode()
        command = (
            f"printf '%s' '{b64}' | base64 -d > /workspace/support-bot && "
            "chmod +x /workspace/support-bot"
        )
        await environment.exec(command=command)
        # Skip the interactive loop entirely — this agent never engages.
        if context.metadata is None:
            context.metadata = {}
        context.metadata["deliberately_partial"] = True
