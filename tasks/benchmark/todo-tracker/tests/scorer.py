#!/usr/bin/env python3
"""Score the interactive multi-turn todo-tracker task against the FINAL workspace.

Reads ``scenario.json`` and runs each milestone's ground-truth check against the
final code in the workspace, then writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_4": 1.0, "reward": 1.0}

Each per-milestone check verifies that milestone's requirement is STILL
satisfied in the final state (cumulative regression), and re-exercises earlier
commands so a regression-free implementation is required for reward 1.

Run from the verifier: ``python3 /tests/scorer.py [--base-dir DIR]``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR_DEFAULT = "/workspace"
SCENARIO_PATH_DEFAULT = "/workspace/scenario.json"
REWARD_PATH = "/logs/verifier/reward.json"

# Entry points the todo CLI may live behind; first that launches wins.
# Absolute paths are used so the scorer can run the tool from an isolated cwd
# (the store file ``todos.json`` then lands in that cwd, as ground-truth states).
def _todo_candidates(base_dir: str) -> list[list[str]]:
    return [
        [sys.executable, os.path.join(base_dir, "src", "todo.py")],
        [sys.executable, os.path.join(base_dir, "todo.py")],
        [os.path.join(base_dir, "todo")],
    ]


def run_todo(base_dir: str, cwd: str, args: list[str], timeout: int = 30):
    """Run the todo CLI in ``cwd`` with ``args``; return CompletedProcess or None.

    Skips candidate entry points whose file does not exist — otherwise
    ``python3 src/todo.py`` on a missing file exits rc=2 (not FileNotFoundError)
    and would short-circuit before the real ``/workspace/todo.py`` is tried.
    """
    for cmd in _todo_candidates(base_dir):
        if not os.path.exists(cmd[-1]):
            continue
        try:
            result = subprocess.run(
                cmd + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result  # first existing entry point that runs
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return None
    return None


def _ok(r) -> bool:
    return r is not None and r.returncode == 0


def _task_dicts(r) -> list[dict] | None:
    """Parse a ``--output-json`` stdout payload as a JSON array of objects."""
    if r is None or not r.stdout.strip():
        return None
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    if not all(isinstance(e, dict) for e in payload):
        return None
    return payload


_STATS_RE = re.compile(r"total=(\d+)\s+pending=(\d+)\s+done=(\d+)")


def _parse_stats(stdout: str) -> tuple[int, int, int] | None:
    m = _STATS_RE.search(stdout)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


_REPORT_RE = re.compile(r"^(high|medium|low):\s*(\d+)\s*$", re.MULTILINE)


def _parse_report(stdout: str) -> dict[str, int] | None:
    counts = dict(_REPORT_RE.findall(stdout))
    if set(counts) != {"high", "medium", "low"}:
        return None
    return {k: int(v) for k, v in counts.items()}


# ----------------------------------------------------------- round checks

def check_tracker_crud(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        r = run_todo(base_dir, td, ["add", "buy milk"])
        if not _ok(r):
            return 0.0
        # Data must be persisted to a visible todos.json in the working directory.
        if not os.path.exists(os.path.join(td, "todos.json")):
            return 0.0
        # Fresh process sees the task (persistence across invocations).
        r2 = run_todo(base_dir, td, ["list"])
        if not _ok(r2) or "1: buy milk" not in r2.stdout:
            return 0.0
        r3 = run_todo(base_dir, td, ["done", "1"])
        if not _ok(r3):
            return 0.0
        # A done task disappears from the default (pending) list.
        r4 = run_todo(base_dir, td, ["list"])
        if not _ok(r4) or "buy milk" in r4.stdout:
            return 0.0
        return 1.0


def check_tracker_all_stats_json(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        for a in (["add", "buy milk"], ["add", "read book"]):
            if not _ok(run_todo(base_dir, td, a)):
                return 0.0
        if not _ok(run_todo(base_dir, td, ["done", "1"])):
            return 0.0
        # list --all shows both tasks, the done one marked.
        r = run_todo(base_dir, td, ["list", "--all"])
        if not _ok(r) or "buy milk" not in r.stdout or "read book" not in r.stdout:
            return 0.0
        if "[done]" not in r.stdout:
            return 0.0
        # stats counts.
        r = run_todo(base_dir, td, ["stats"])
        if not _ok(r) or _parse_stats(r.stdout) != (2, 1, 1):
            return 0.0
        # list --output-json: array of objects with the required keys.
        r = run_todo(base_dir, td, ["list", "--output-json"])
        items = _task_dicts(r)
        if items is None or len(items) != 1:
            return 0.0
        task = items[0]
        if not all(k in task for k in ("id", "description", "status", "created_at")):
            return 0.0
        if task["description"] != "read book" or task["status"] != "pending":
            return 0.0
        # Plain text mode still works.
        r = run_todo(base_dir, td, ["list"])
        if not _ok(r) or "read book" not in r.stdout or "buy milk" in r.stdout:
            return 0.0
        return 1.0


def check_tracker_priority(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        for a in (
            ["add", "buy milk", "--priority", "high"],
            ["add", "read book"],
            ["add", "water plants", "--priority", "low"],
            ["add", "write report"],
        ):
            if not _ok(run_todo(base_dir, td, a)):
                return 0.0
        if not _ok(run_todo(base_dir, td, ["done", "2"])):  # read book -> done
            return 0.0
        # Priority filter applies to the default (pending) list.
        r = run_todo(base_dir, td, ["list", "--priority", "high"])
        if not _ok(r) or "buy milk" not in r.stdout or "read book" in r.stdout:
            return 0.0
        # Status filter.
        r = run_todo(base_dir, td, ["list", "--status", "done"])
        if not _ok(r) or "read book" not in r.stdout or "[done]" not in r.stdout:
            return 0.0
        # JSON includes priority, and bare adds default to medium.
        r = run_todo(base_dir, td, ["list", "--output-json"])
        items = _task_dicts(r)
        if items is None:
            return 0.0
        prios = {t.get("description"): t.get("priority") for t in items}
        if prios.get("buy milk") != "high" or prios.get("water plants") != "low":
            return 0.0
        if prios.get("write report") != "medium":
            return 0.0
        if not all("priority" in t for t in items):
            return 0.0
        # stats / done still work.
        r = run_todo(base_dir, td, ["stats"])
        if not _ok(r) or _parse_stats(r.stdout) != (4, 3, 1):
            return 0.0
        return 1.0


def check_tracker_report_search(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        for a in (
            ["add", "buy milk", "--priority", "high"],
            ["add", "read book"],
            ["add", "water plants", "--priority", "low"],
        ):
            if not _ok(run_todo(base_dir, td, a)):
                return 0.0
        if not _ok(run_todo(base_dir, td, ["done", "1"])):
            return 0.0
        # report: three lines, all priorities incl. zero counts.
        r = run_todo(base_dir, td, ["report"])
        if not _ok(r) or _parse_report(r.stdout) != {"high": 1, "medium": 1, "low": 1}:
            return 0.0
        # search is case-insensitive substring on description.
        r = run_todo(base_dir, td, ["search", "MILK"])
        if not _ok(r) or "buy milk" not in r.stdout:
            return 0.0
        # search --output-json is an array.
        r = run_todo(base_dir, td, ["search", "plant", "--output-json"])
        items = _task_dicts(r)
        if items is None or len(items) != 1 or items[0]["description"] != "water plants":
            return 0.0
        # report --output-json is an array.
        r = run_todo(base_dir, td, ["report", "--output-json"])
        items = _task_dicts(r)
        if items is None or len(items) != 3:
            return 0.0
        by_prio = {e.get("priority"): e.get("count") for e in items}
        if by_prio != {"high": 1, "medium": 1, "low": 1}:
            return 0.0
        # base commands still work.
        r = run_todo(base_dir, td, ["list"])
        if not _ok(r) or "read book" not in r.stdout or "buy milk" in r.stdout:
            return 0.0
        return 1.0


CHECKERS: dict[str, object] = {
    "tracker_crud": check_tracker_crud,
    "tracker_all_stats_json": check_tracker_all_stats_json,
    "tracker_priority": check_tracker_priority,
    "tracker_report_search": check_tracker_report_search,
}


# ------------------------------------------------------------------ main

def score(scenario: dict, base_dir: str) -> dict[str, float]:
    """Compute per-milestone scores + product reward from the final workspace."""
    per_round: dict[str, float] = {}
    for milestone in scenario["milestones"]:
        test_id = milestone["test_id"]
        checker = CHECKERS.get(test_id)
        if checker is None:
            raise KeyError(f"no checker implemented for test_id={test_id!r}")
        per_round[f"round_{milestone['index']}"] = float(checker(base_dir))
    reward = 1.0
    for v in per_round.values():
        reward *= v
    return {**per_round, "reward": reward}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=os.environ.get("BASE_DIR", BASE_DIR_DEFAULT))
    parser.add_argument("--scenario", default=os.environ.get("SCENARIO_PATH", SCENARIO_PATH_DEFAULT))
    parser.add_argument("--reward-out", default=os.environ.get("REWARD_PATH", REWARD_PATH))
    args = parser.parse_args(argv)

    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    rewards = score(scenario, args.base_dir)

    out = Path(args.reward_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rewards), encoding="utf-8")
    print(json.dumps(rewards, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
