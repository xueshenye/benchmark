#!/usr/bin/env python3
"""Score the interactive multi-turn support-bot task against the FINAL workspace.

Reads ``scenario.json`` and runs each milestone's ground-truth check against the
final bot in the workspace, then writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_4": 1.0, "reward": 1.0}

Design notes (see docs/task-suite-design.md and PROJECT_STATE.md):

- **No hardcoded test inputs**: each check generates its inputs at grading time
  from the hidden ``ground_truth/facts.json`` (seeded RNG) and from synthetic
  order data served by the verifier's own mock API instance. The agent can never
  pre-compute the exact queries/orders used.
- **Anti-tamper**: the knowledge base must be byte-identical to the ground truth
  (user-provided materials are read-only). Order answers come from the
  verifier-controlled API, so workspace order data cannot be gamed.
- **Cumulative regression**: every milestone check runs against the final bot, so
  an implementation that only satisfies the last milestone scores 0 elsewhere and
  ``reward = product`` stays sparse.

Run from the verifier: ``python3 /tests/scorer.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR_DEFAULT = "/workspace"
SCENARIO_PATH_DEFAULT = "/workspace/scenario.json"
REWARD_PATH = "/logs/verifier/reward.json"
GROUND_TRUTH_DEFAULT = "/var/ground_truth"
API_PORT_PREFERRED = 8123

# order API `status` → 中文, mirrored from docs/api.md (the ground-truth spec).
STATUS_ZH = {
    "pending": "待支付",
    "paid": "已支付",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}

# ------------------------------------------------------------------ entry point

def _candidates(base_dir: str) -> list[tuple[list[str], str | None]]:
    """(argv, precheck-path) pairs; a None precheck means 'assume exists'."""
    return [
        ([sys.executable, "-m", "support_bot"], os.path.join(base_dir, "support_bot", "__main__.py")),
        ([sys.executable, os.path.join(base_dir, "support_bot", "cli.py")], os.path.join(base_dir, "support_bot", "cli.py")),
        ([sys.executable, os.path.join(base_dir, "support_bot.py")], os.path.join(base_dir, "support_bot.py")),
        ([os.path.join(base_dir, "support-bot")], os.path.join(base_dir, "support-bot")),
        ([os.path.join(base_dir, "bin", "support-bot")], os.path.join(base_dir, "bin", "support-bot")),
    ]


_bot_cache: dict[str, list[str] | None] = {}


def _resolve_bot(base_dir: str) -> list[str] | None:
    """Find a working entry point for the bot, skipping non-existent candidates.

    A missing file under ``python3 src/x.py`` exits rc=2 (not FileNotFoundError),
    so we must pre-check the path exists (PROJECT_STATE §6.9 bug lesson).
    """
    if base_dir in _bot_cache:
        return _bot_cache[base_dir]
    for argv, precheck in _candidates(base_dir):
        if precheck is not None and not os.path.exists(precheck):
            continue
        env = dict(os.environ)
        env["PYTHONPATH"] = base_dir
        env["SUPPORT_KB_DIR"] = os.path.join(base_dir, "knowledge_base")
        with tempfile.TemporaryDirectory() as td:
            try:
                r = subprocess.run(
                    argv + ["你好"],
                    cwd=td,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        if r.returncode == 0 and r.stdout.strip():
            _bot_cache[base_dir] = argv
            return argv
    _bot_cache[base_dir] = None
    return None


def _run_bot(
    base_dir: str,
    args: list[str],
    *,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
):
    """Run the bot with ``args``; return CompletedProcess or None."""
    argv = _resolve_bot(base_dir)
    if argv is None:
        return None
    env = dict(os.environ)
    env["PYTHONPATH"] = base_dir
    env.setdefault("SUPPORT_KB_DIR", os.path.join(base_dir, "knowledge_base"))
    env.update(env_extra or {})
    try:
        return subprocess.run(
            argv + args,
            cwd=cwd or base_dir,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ------------------------------------------------------------------ helpers

def _normalize(text: str) -> str:
    """Strip all whitespace and lowercase, so '7 天' == '7天' and '7 Days' == '7days'."""
    return re.sub(r"\s+", "", text).lower()


def _load_facts(gt: str) -> dict:
    path = os.path.join(gt, "facts.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _kb_tampered(base_dir: str, gt: str) -> bool:
    """True if any ground-truth KB file is missing/different in the workspace
    (extra workspace files are ignored). User-provided materials are read-only."""
    gt_kb = os.path.join(gt, "knowledge_base")
    if not os.path.isdir(gt_kb):
        return False
    for name in sorted(os.listdir(gt_kb)):
        gt_path = os.path.join(gt_kb, name)
        ws_path = os.path.join(base_dir, "knowledge_base", name)
        if not os.path.exists(ws_path):
            return True
        if not Path(gt_path).read_bytes() == Path(ws_path).read_bytes():
            return True
    return False


def _fact_ok(base_dir: str, fact: dict, *, cwd: str | None = None, env_extra=None) -> bool:
    r = _run_bot(base_dir, [fact["q"]], cwd=cwd, env_extra=env_extra)
    if r is None or r.returncode != 0 or not r.stdout.strip():
        return False
    out = _normalize(r.stdout)
    return all(_normalize(t) in out for t in fact["must_contain"])


def _kb_regression(base_dir: str, gt: str, rng: random.Random) -> bool:
    """Sample one KB fact and check the bot still answers it (cumulative)."""
    facts = _load_facts(gt)
    fact = rng.choice(facts["zh_facts"])
    return _fact_ok(base_dir, fact)


def _mentions_not_found(stdout: str) -> bool:
    return any(k in stdout for k in ("查不到", "不存在", "没有找到", "没有查询到", "无法查询"))


_LATIN = "abcdefghijklmnopqrstuvwxyz"


def _rng_suffix(rng: random.Random, n: int = 4) -> str:
    """Random lowercase-letter suffix for hidden queries.

    Latin shingles cannot collide with the Chinese KB, so an unanswerable
    question stays unanswerable (a numeric suffix would, e.g., match "30天").
    """
    return "".join(rng.choice(_LATIN) for _ in range(n))


# -------------------------------------------------------------- mock order API

class _OrderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        prefix = "/api/orders/"
        if path.startswith(prefix):
            oid = path[len(prefix):]
            for order in self.server.orders:  # type: ignore[attr-defined]
                if order["order_id"] == oid:
                    self._reply(order)
                    return
            self._reply({"error": "order not found"}, status=404)
            return
        self._reply({"error": "not found"}, status=404)

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass


class _OrderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, orders: list[dict]) -> None:
        super().__init__(addr, _OrderHandler)
        self.orders = orders


def _start_order_server(orders: list[dict]) -> tuple[_OrderServer | None, str]:
    """Bind 8123 if free, else an ephemeral port; the bot always receives the
    real address via SUPPORT_API_BASE, so it works regardless."""
    for port in (API_PORT_PREFERRED, 0):
        try:
            srv = _OrderServer(("127.0.0.1", port), orders)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return srv, f"http://127.0.0.1:{srv.server_address[1]}"
        except OSError:
            continue
    return None, ""


# ----------------------------------------------------------- round checks

def check_kb_bot(base_dir: str, gt: str) -> float:
    if _kb_tampered(base_dir, gt):
        return 0.0
    facts = _load_facts(gt)
    rng = random.Random(20260811)
    sampled = rng.sample(facts["zh_facts"], min(3, len(facts["zh_facts"])))
    for fact in sampled:
        with tempfile.TemporaryDirectory() as td:
            if not _fact_ok(base_dir, fact, cwd=td):
                return 0.0
    # Unknown question → polite non-hallucinating fallback (no fabricated facts).
    # Chosen so no KB shingle overlaps (the KB has no 会员/专属/权益 terms).
    unknown = f"你们的超级会员有什么专属权益{_rng_suffix(rng)}"
    with tempfile.TemporaryDirectory() as td:
        r = _run_bot(base_dir, [unknown], cwd=td)
        if r is None or r.returncode != 0 or not r.stdout.strip():
            return 0.0
        out = _normalize(r.stdout)
        for fact in facts["zh_facts"]:
            if any(_normalize(t) in out for t in fact["must_contain"]):
                return 0.0
    return 1.0


def check_api_orders(base_dir: str, gt: str) -> float:
    rng = random.Random(20260811)
    statuses = list(STATUS_ZH)
    orders: list[dict] = []
    for i in range(3):
        oid = f"YGO-V-{rng.randint(1000, 9999)}-{i + 1:03d}"
        orders.append(
            {
                "order_id": oid,
                "customer": "测试顾客",
                "status": rng.choice(statuses),
                "progress": rng.randint(1, 4),
                "items": [{"name": "云购智能手表 YunGo Watch S2", "qty": 1}],
                "shipped_at": "2026-08-01 10:00",
            }
        )
    srv, base = _start_order_server(orders)
    if srv is None:
        return 0.0
    try:
        env_extra = {"SUPPORT_API_BASE": base}
        for order in orders:
            q = f"帮我查一下订单 {order['order_id']} 到哪了"
            with tempfile.TemporaryDirectory() as td:
                r = _run_bot(base_dir, [q], cwd=td, env_extra=env_extra)
                if r is None or r.returncode != 0:
                    return 0.0
                if order["order_id"] not in r.stdout:
                    return 0.0
                if STATUS_ZH[order["status"]] not in r.stdout:
                    return 0.0
        # Unknown order → graceful "not found", no fabricated status.
        unknown = f"YGO-NOPE-{rng.randint(1000, 9999)}"
        with tempfile.TemporaryDirectory() as td:
            r = _run_bot(base_dir, [f"帮我查订单 {unknown}"], cwd=td, env_extra=env_extra)
            if r is None or r.returncode != 0 or not r.stdout.strip():
                return 0.0
            if not _mentions_not_found(r.stdout):
                return 0.0
        # Regression: KB question still answered.
        if not _kb_regression(base_dir, gt, rng):
            return 0.0
        return 1.0
    finally:
        srv.shutdown()
        srv.server_close()


def check_batch_refactor(base_dir: str, gt: str) -> float:
    # 1. Package is importable.
    env = dict(os.environ)
    env["PYTHONPATH"] = base_dir
    r = subprocess.run(
        [sys.executable, "-c", "import support_bot"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if r.returncode != 0:
        return 0.0
    # 2. pytest is green (agent must have written tests).
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        return 0.0
    # 3. Batch mode: one answer per line, order preserved.
    facts = _load_facts(gt)
    rng = random.Random(20260811)
    sampled = rng.sample(facts["zh_facts"], min(3, len(facts["zh_facts"])))
    with tempfile.TemporaryDirectory() as td:
        q_path = os.path.join(td, "q.txt")
        a_path = os.path.join(td, "a.txt")
        with open(q_path, "w", encoding="utf-8") as f:
            f.write("\n".join(fact["q"] for fact in sampled) + "\n")
        r = _run_bot(base_dir, ["--batch", q_path, "-o", a_path], cwd=td)
        if r is None or r.returncode != 0:
            return 0.0
        if not os.path.exists(a_path):
            return 0.0
        with open(a_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        if len(lines) != len(sampled):
            return 0.0
        for i, fact in enumerate(sampled):
            if not all(_normalize(t) in _normalize(lines[i]) for t in fact["must_contain"]):
                return 0.0
    # 4. Interactive single-question regression.
    if not _kb_regression(base_dir, gt, rng):
        return 0.0
    return 1.0


def check_lang_escalate(base_dir: str, gt: str) -> float:
    rng = random.Random(20260811)
    facts = _load_facts(gt)
    # 1. English question → English answer (no CJK) with the right facts.
    en_fact = rng.choice(facts["en_facts"])
    r = _run_bot(base_dir, [en_fact["q"]])
    if r is None or r.returncode != 0 or not r.stdout.strip():
        return 0.0
    if re.search(r"[一-鿿]", r.stdout):
        return 0.0  # answered in Chinese despite an English customer
    if not all(_normalize(t) in _normalize(r.stdout) for t in en_fact["must_contain"]):
        return 0.0
    # 2. Chinese question → Chinese answer (contextual regression).
    zh_fact = rng.choice(facts["zh_facts"])
    if not _fact_ok(base_dir, zh_fact):
        return 0.0
    # 3. Unknown → escalate-to-human + append to escalations.log (cwd).
    # Chosen so no KB shingle overlaps (the KB has no 周年庆/大促 terms).
    unknown = f"你们的周年庆大促活动什么时候开始{_rng_suffix(rng)}"
    r = _run_bot(base_dir, [unknown], cwd=base_dir)
    if r is None or r.returncode != 0 or not r.stdout.strip():
        return 0.0
    if "转人工" not in r.stdout and "人工客服" not in r.stdout:
        return 0.0
    log_path = os.path.join(base_dir, "escalations.log")
    if not os.path.exists(log_path):
        return 0.0
    if unknown not in Path(log_path).read_text(encoding="utf-8"):
        return 0.0
    return 1.0


CHECKERS: dict[str, object] = {
    "kb_bot": check_kb_bot,
    "api_orders": check_api_orders,
    "batch_refactor": check_batch_refactor,
    "lang_escalate": check_lang_escalate,
}


# ------------------------------------------------------------------ main

def score(scenario: dict, base_dir: str, gt: str) -> dict[str, float]:
    """Compute per-milestone scores + product reward from the final workspace."""
    per_round: dict[str, float] = {}
    for milestone in scenario["milestones"]:
        test_id = milestone["test_id"]
        checker = CHECKERS.get(test_id)
        if checker is None:
            raise KeyError(f"no checker implemented for test_id={test_id!r}")
        per_round[f"round_{milestone['index']}"] = float(checker(base_dir, gt))  # type: ignore[operator]
    reward = 1.0
    for v in per_round.values():
        reward *= v
    return {**per_round, "reward": reward}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=os.environ.get("BASE_DIR", BASE_DIR_DEFAULT))
    parser.add_argument("--scenario", default=os.environ.get("SCENARIO_PATH", SCENARIO_PATH_DEFAULT))
    parser.add_argument("--reward-out", default=os.environ.get("REWARD_PATH", REWARD_PATH))
    parser.add_argument("--ground-truth", default=os.environ.get("GROUND_TRUTH", GROUND_TRUTH_DEFAULT))
    args = parser.parse_args(argv)

    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    rewards = score(scenario, args.base_dir, args.ground_truth)

    out = Path(args.reward_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rewards), encoding="utf-8")
    print(json.dumps(rewards, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
