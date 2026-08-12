#!/usr/bin/env python3
"""Score the interactive multi-turn ticket-system task against the FINAL workspace.

The verifier starts the built HTTP service itself on an ephemeral port with its
own temporary ``TICKET_DB``, then runs hidden end-to-end checks per milestone and
writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_4": 1.0, "reward": 1.0}

Design notes (see docs/task-suite-design.md and the plan file):

- **No hardcoded test inputs**: inputs are generated at grading time from the
  hidden ``ground_truth/facts.json`` (seeded RNG); the verifier's data lives in
  a temp DB it fully controls, so the agent's dev data / leftover servers cannot
  leak in.
- **Execution, not LLM-judge**: every check hits the running service over HTTP
  (urllib) and asserts status codes, JSON shapes and persisted state (including
  across a server restart). No browser automation, no UI-structure assertions.
- **Cumulative regression**: all four milestone checks run against the final
  service; ``reward = product(round_N)`` stays sparse. The M4 delete-policy
  reversal is graded by soft-delete/restore semantics replacing the v1 permanent
  delete.

Run from the verifier: ``python3 /tests/scorer.py``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR_DEFAULT = "/workspace"
SCENARIO_PATH_DEFAULT = "/workspace/scenario.json"
REWARD_PATH = "/logs/verifier/reward.json"
GROUND_TRUTH_DEFAULT = "/var/ground_truth"

_SQLITE_MAGIC = b"SQLite format 3\x00"


# ------------------------------------------------------------------ HTTP helpers

def _request(method: str, url: str, payload=None, timeout: int = 5):
    """urllib request → (status_code_or_None, body_bytes). Never raises for net errors."""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, b""


def _get_json(base: str, path: str, params: dict | None = None, timeout: int = 5):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    code, body = _request("GET", url, timeout=timeout)
    if code is None:
        return None, None
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, None


def _post_ticket(base: str, payload: dict) -> dict | None:
    code, body = _request("POST", f"{base}/api/tickets", payload=payload)
    if code not in (200, 201):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _patch(base: str, tid: int, payload: dict):
    code, body = _request("PATCH", f"{base}/api/tickets/{tid}", payload=payload)
    try:
        return code, json.loads(body) if body else None
    except json.JSONDecodeError:
        return code, None


def _iso_days_ago(days: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------ app lifecycle

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _candidates(base_dir: str) -> list[tuple[list[str], str | None]]:
    """(argv, precheck-path) pairs; a None precheck means 'assume exists'."""
    return [
        ([sys.executable, "-m", "ticket_system"], os.path.join(base_dir, "ticket_system", "__main__.py")),
        ([sys.executable, os.path.join(base_dir, "app.py")], os.path.join(base_dir, "app.py")),
        ([sys.executable, os.path.join(base_dir, "server.py")], os.path.join(base_dir, "server.py")),
        ([sys.executable, os.path.join(base_dir, "main.py")], os.path.join(base_dir, "main.py")),
        ([os.path.join(base_dir, "run.sh")], os.path.join(base_dir, "run.sh")),
    ]


def _app_env(base_dir: str, port: int, db_path: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = base_dir
    env["PORT"] = str(port)
    env["TICKET_DB"] = db_path
    return env


def _stop_app(proc) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _probe_app(argv: list[str], base_dir: str, port: int, db_path: str, timeout: int = 4) -> bool:
    """Start one candidate, require GET /api/health 200 with status==ok, always kill."""
    try:
        proc = subprocess.Popen(
            argv, cwd=base_dir, env=_app_env(base_dir, port, db_path),
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return False
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return False
            code, body = _request("GET", f"{base}/api/health", timeout=1)
            if code == 200:
                try:
                    if json.loads(body).get("status") == "ok":
                        return True
                except (json.JSONDecodeError, AttributeError):
                    pass
            time.sleep(0.2)
        return False
    finally:
        _stop_app(proc)


_resolve_cache: dict[str, list[str] | None] = {}


def _resolve_app(base_dir: str) -> list[str] | None:
    """Find a candidate that actually serves /api/health; skip non-existent files (§6.9)."""
    if base_dir in _resolve_cache:
        return _resolve_cache[base_dir]
    for argv, precheck in _candidates(base_dir):
        if precheck is not None and not os.path.exists(precheck):
            continue
        with tempfile.TemporaryDirectory() as td:
            if _probe_app(argv, base_dir, _free_port(), os.path.join(td, "probe.db")):
                _resolve_cache[base_dir] = argv
                return argv
    _resolve_cache[base_dir] = None
    return None


def _start_app(base_dir: str, port: int, db_path: str, timeout: int = 10):
    argv = _resolve_app(base_dir)
    if argv is None:
        return None, None
    try:
        proc = subprocess.Popen(
            argv, cwd=base_dir, env=_app_env(base_dir, port, db_path),
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return None, None
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            _stop_app(proc)
            return None, None
        code, _ = _request("GET", f"{base}/api/health", timeout=1)
        if code == 200:
            return proc, base
        time.sleep(0.3)
    _stop_app(proc)
    return None, None


def _start_app_retry(base_dir: str, db_path: str, attempts: int = 3):
    """Start on a fresh ephemeral port; retry on a transient bind race."""
    for _ in range(attempts):
        proc, base = _start_app(base_dir, _free_port(), db_path)
        if proc is not None:
            return proc, base
    return None, None


# ------------------------------------------------------------------ ground truth

def _load_facts(gt: str) -> dict:
    with open(os.path.join(gt, "facts.json"), encoding="utf-8") as f:
        return json.load(f)


def _docs_tampered(base_dir: str, gt: str) -> bool:
    """True if any ground-truth doc is missing/different in the workspace
    (extra workspace files are ignored). User-provided materials are read-only."""
    gt_docs = os.path.join(gt, "docs")
    if not os.path.isdir(gt_docs):
        return False
    for name in sorted(os.listdir(gt_docs)):
        gt_path = os.path.join(gt_docs, name)
        ws_path = os.path.join(base_dir, "docs", name)
        if not os.path.exists(ws_path):
            return True
        if not Path(gt_path).read_bytes() == Path(ws_path).read_bytes():
            return True
    return False


# ----------------------------------------------------------- round checks

def check_ticket_crud(base_dir: str, gt: str) -> float:
    if _docs_tampered(base_dir, gt):
        return 0.0
    facts = _load_facts(gt)
    rng = random.Random(20260811)
    titles = rng.sample(facts["titles"], 2)
    t1 = {"title": titles[0], "description": rng.choice(facts["descriptions"]), "reporter": rng.choice(facts["reporters"])}
    t2 = {"title": titles[1], "description": rng.choice(facts["descriptions"]), "reporter": rng.choice(facts["reporters"])}
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "tickets.db")
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            code, body = _request("GET", f"{base}/api/health")
            if code != 200 or json.loads(body).get("status") != "ok":
                return 0.0
            r1 = _post_ticket(base, t1)
            if r1 is None or not isinstance(r1.get("id"), int) or r1.get("status") != "open":
                return 0.0
            if r1.get("priority") != "medium":
                return 0.0
            if not r1.get("created_at"):
                return 0.0
            r2 = _post_ticket(base, t2)
            if r2 is None:
                return 0.0
            code, _ = _request("POST", f"{base}/api/tickets", payload={"title": "   "})
            if code != 400:
                return 0.0
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or not isinstance(items, list):
                return 0.0
            listed = {i.get("title") for i in items}
            if t1["title"] not in listed or t2["title"] not in listed:
                return 0.0
            code, item = _get_json(base, f"/api/tickets/{r1['id']}")
            if code != 200 or item.get("title") != t1["title"]:
                return 0.0
            if not os.path.exists(db):
                return 0.0
            code, body = _request("GET", f"{base}/")
            if code != 200:
                return 0.0
            page = body.decode("utf-8", "ignore")
            if "云服客服" not in page or "工单" not in page:
                return 0.0
        finally:
            _stop_app(proc)
        # Persistence across restart: same DB, fresh port.
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or not isinstance(items, list):
                return 0.0
            listed = {i.get("title") for i in items}
            if t1["title"] not in listed or t2["title"] not in listed:
                return 0.0
        finally:
            _stop_app(proc)
    return 1.0


def check_ticket_workflow(base_dir: str, gt: str) -> float:
    facts = _load_facts(gt)
    rng = random.Random(20260812)
    # 4 tickets with varied priority + one ASCII-bearing title for q case-insensitivity.
    payloads = [
        {"title": facts["titles"][0], "description": facts["descriptions"][0], "reporter": rng.choice(facts["reporters"]), "priority": "high"},
        {"title": facts["titles"][1], "description": facts["descriptions"][1], "reporter": rng.choice(facts["reporters"]), "priority": "medium"},
        {"title": facts["titles"][2], "description": facts["descriptions"][2], "reporter": rng.choice(facts["reporters"]), "priority": "low"},
        {"title": facts["titles"][10], "description": facts["descriptions"][3], "reporter": rng.choice(facts["reporters"]), "priority": "medium"},  # "App端登录失败"
    ]
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "tickets.db")
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            tickets = []
            for p in payloads:
                r = _post_ticket(base, p)
                if r is None:
                    return 0.0
                tickets.append(r)
            a1, a2 = facts["assignees"][0], facts["assignees"][1]
            code, r = _patch(base, tickets[0]["id"], {"assignee": a1})
            if code != 200 or r.get("assignee") != a1:
                return 0.0
            code, r = _patch(base, tickets[1]["id"], {"assignee": a2})
            if code != 200 or r.get("assignee") != a2:
                return 0.0
            # Filter by status.
            code, r = _patch(base, tickets[0]["id"], {"status": "in_progress"})
            if code != 200 or r.get("status") != "in_progress":
                return 0.0
            code, items = _get_json(base, "/api/tickets", params={"status": "in_progress"})
            if code != 200 or [i.get("id") for i in items] != [tickets[0]["id"]]:
                return 0.0
            # Filter by priority.
            code, items = _get_json(base, "/api/tickets", params={"priority": "high"})
            if code != 200 or [i.get("id") for i in items] != [tickets[0]["id"]]:
                return 0.0
            # Filter by assignee.
            code, items = _get_json(base, "/api/tickets", params={"assignee": a1})
            if code != 200 or [i.get("id") for i in items] != [tickets[0]["id"]]:
                return 0.0
            # q search, case-insensitive substring on ASCII-bearing title.
            code, items = _get_json(base, "/api/tickets", params={"q": "app"})
            if code != 200:
                return 0.0
            if tickets[3]["id"] not in [i.get("id") for i in items]:
                return 0.0
            # Status machine on ticket[0] (in_progress): resolved sets resolved_at, closed, reopen clears.
            code, r = _patch(base, tickets[0]["id"], {"status": "resolved"})
            if code != 200 or r.get("resolved_at") is None:
                return 0.0
            code, r = _patch(base, tickets[0]["id"], {"status": "closed"})
            if code != 200:
                return 0.0
            code, r = _patch(base, tickets[0]["id"], {"status": "open"})
            if code != 200 or r.get("resolved_at") is not None:
                return 0.0
            # Crisp negatives on a fresh ticket: jump open->resolved → 400; bogus → 400; missing id → 404.
            fresh = _post_ticket(base, {"title": facts["titles"][4], "description": facts["descriptions"][4], "reporter": rng.choice(facts["reporters"])})
            if fresh is None:
                return 0.0
            code, _ = _patch(base, fresh["id"], {"status": "resolved"})
            if code != 400:
                return 0.0
            code, _ = _patch(base, fresh["id"], {"status": "bogus"})
            if code != 400:
                return 0.0
            code, _ = _get_json(base, "/api/tickets/999999")
            if code != 404:
                return 0.0
            # Lenient delete: absent from the normal list (true for hard AND soft delete).
            code, _ = _request("DELETE", f"{base}/api/tickets/{fresh['id']}")
            if code != 200:
                return 0.0
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or fresh["id"] in [i.get("id") for i in items]:
                return 0.0
        finally:
            _stop_app(proc)
    return 1.0


def check_ticket_refactor_sla(base_dir: str, gt: str) -> float:
    # Package importable.
    env = dict(os.environ)
    env["PYTHONPATH"] = base_dir
    r = subprocess.run(
        [sys.executable, "-c", "import ticket_system"],
        cwd=base_dir, capture_output=True, text=True, timeout=30, env=env,
    )
    if r.returncode != 0:
        return 0.0
    # pytest green.
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=base_dir, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return 0.0
    # SLA: a 45-day-old open ticket is overdue; a fresh one is not; data file is SQLite.
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "tickets.db")
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            old = _post_ticket(base, {"title": "历史遗留工单", "description": "很久以前提交", "reporter": "测试", "created_at": _iso_days_ago(45)})
            fresh = _post_ticket(base, {"title": "刚提交的工单", "description": "新问题", "reporter": "测试"})
            if old is None or fresh is None:
                return 0.0
            code, o = _get_json(base, f"/api/tickets/{old['id']}")
            if code != 200 or o.get("overdue") is not True:
                return 0.0
            code, f = _get_json(base, f"/api/tickets/{fresh['id']}")
            if code != 200 or f.get("overdue") is not False:
                return 0.0
            if not os.path.exists(db):
                return 0.0
            with open(db, "rb") as fh:
                if fh.read(16) != _SQLITE_MAGIC:
                    return 0.0
        finally:
            _stop_app(proc)
    return 1.0


def check_ticket_softdelete_report(base_dir: str, gt: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "tickets.db")

        def _mk():
            t1 = _post_ticket(base, {"title": "登录问题", "description": "a", "reporter": "x", "priority": "high"})
            t2 = _post_ticket(base, {"title": "支付问题", "description": "b", "reporter": "y", "priority": "medium"})
            t3 = _post_ticket(base, {"title": "历史超时", "description": "c", "reporter": "z", "priority": "low", "created_at": _iso_days_ago(45)})
            t4 = _post_ticket(base, {"title": "发票问题", "description": "d", "reporter": "w", "priority": "medium"})
            for r in (t1, t2, t3, t4):
                if r is None:
                    return None
            return t1, t2, t3, t4

        # Block 1: soft-delete semantics + stats (t2 deleted while stats computed).
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            made = _mk()
            if made is None:
                return 0.0
            t1, t2, t3, t4 = made
            code, r4 = _patch(base, t4["id"], {"status": "in_progress"})
            if code != 200:
                return 0.0
            code, r4 = _patch(base, t4["id"], {"status": "resolved"})
            if code != 200 or r4.get("resolved_at") is None:
                return 0.0
            # Soft delete t2.
            code, _ = _request("DELETE", f"{base}/api/tickets/{t2['id']}")
            if code != 200:
                return 0.0
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or t2["id"] in [i.get("id") for i in items]:
                return 0.0
            code, item = _get_json(base, f"/api/tickets/{t2['id']}")
            if code != 200 or item.get("deleted") is not True:
                return 0.0
            code, items = _get_json(base, "/api/tickets", params={"include_deleted": "1"})
            if code != 200:
                return 0.0
            by_id = {i.get("id"): i for i in items}
            if by_id.get(t2["id"], {}).get("deleted") is not True:
                return 0.0
            # Stats exclude deleted; t1(high), t3(low, overdue), t4(resolved), t2(medium, deleted).
            code, stats = _get_json(base, "/api/tickets/stats")
            if code != 200 or not isinstance(stats, dict):
                return 0.0
            if stats.get("by_status") != {"open": 2, "in_progress": 0, "resolved": 1, "closed": 0}:
                return 0.0
            if stats.get("by_priority") != {"high": 1, "medium": 1, "low": 1}:
                return 0.0
            if not isinstance(stats.get("avg_resolution_hours"), (int, float)):
                return 0.0
            if stats.get("overdue_count") != 1:
                return 0.0
            # Restore t2.
            code, _ = _request("POST", f"{base}/api/tickets/{t2['id']}/restore")
            if code != 200:
                return 0.0
            code, item = _get_json(base, f"/api/tickets/{t2['id']}")
            if code != 200 or item.get("deleted") is not False:
                return 0.0
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or t2["id"] not in [i.get("id") for i in items]:
                return 0.0
            # Negatives: restore a live ticket → 409; restore missing id → 404.
            code, _ = _request("POST", f"{base}/api/tickets/{t2['id']}/restore")
            if code != 409:
                return 0.0
            code, _ = _request("POST", f"{base}/api/tickets/999999/restore")
            if code != 404:
                return 0.0
        finally:
            _stop_app(proc)

        # Block 2: restart, delete t2 again.
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            code, _ = _request("DELETE", f"{base}/api/tickets/2")
            if code != 200:
                return 0.0
        finally:
            _stop_app(proc)

        # Block 3: deleted state survived the restart; restore still works.
        proc, base = _start_app_retry(base_dir, db)
        if proc is None:
            return 0.0
        try:
            code, item = _get_json(base, "/api/tickets/2")
            if code != 200 or item.get("deleted") is not True:
                return 0.0
            code, _ = _request("POST", f"{base}/api/tickets/2/restore")
            if code != 200:
                return 0.0
            code, items = _get_json(base, "/api/tickets")
            if code != 200 or 2 not in [i.get("id") for i in items]:
                return 0.0
        finally:
            _stop_app(proc)
    return 1.0


CHECKERS: dict[str, object] = {
    "ticket_crud": check_ticket_crud,
    "ticket_workflow": check_ticket_workflow,
    "ticket_refactor_sla": check_ticket_refactor_sla,
    "ticket_softdelete_report": check_ticket_softdelete_report,
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
