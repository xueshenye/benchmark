#!/usr/bin/env python3
"""Score the interactive multi-turn repofix task against the FINAL workspace.

Reads ``scenario.json`` and runs each milestone's ground-truth check against the
final state of the repo (which starts seeded-broken), then writes
``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_3": 1.0, "reward": 1.0}

Each per-milestone check verifies the requirement is STILL satisfied in the
final state. The verifier checks the real artifact (CLI output / the repo's own
test suite / an AST structural property), never the agent's self-report, and
uses hidden inputs so an agent that "built to the visible test" still fails.

Run from the verifier: ``python3 /tests/scorer.py [--base-dir DIR]``.
"""

from __future__ import annotations

import argparse
import ast
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

_TOTAL_RE = re.compile(r"^([^:]+):\s*([-+0-9.]+)\s*$")


def run_pipeline(base_dir: str, args: list[str], timeout: int = 60):
    """Run the sales pipeline against an input file (absolute entry point)."""
    entry = os.path.join(base_dir, "pipeline.py")
    try:
        return subprocess.run(
            [sys.executable, entry] + args,
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def parse_totals(stdout: str) -> dict[str, float]:
    """Parse ``category: amount`` lines into {category: float}."""
    totals: dict[str, float] = {}
    for line in stdout.splitlines():
        m = _TOTAL_RE.match(line.strip())
        if not m:
            continue
        try:
            totals[m.group(1)] = float(m.group(2))
        except ValueError:
            continue
    return totals


def run_pytest(base_dir: str, timeout: int = 120):
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def write_csv(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def count_functions(base_dir: str) -> int:
    """Number of FunctionDef nodes in pipeline.py (structural refactor proxy)."""
    path = Path(base_dir) / "pipeline.py"
    if not path.exists():
        return 0
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return 0
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


# ----------------------------------------------------------- round checks

def check_repofix_basic(base_dir: str) -> float:
    # 1. The visible test suite passes against the fixed code.
    pt = run_pytest(base_dir)
    if pt is None or pt.returncode != 0:
        return 0.0
    # 2. Hidden behavioral check: a fresh CSV with different categories/numbers
    #    must produce the correct per-category totals (anti hardcoding).
    with tempfile.TemporaryDirectory() as td:
        csv_path = write_csv(
            os.path.join(td, "h.csv"),
            "date,category,amount\n"
            "2024-05-01,food,3.0\n"
            "2024-05-02,drinks,7.5\n"
            "2024-05-03,food,4.0\n"
            "2024-05-04,toys,2.0\n",
        )
        r = run_pipeline(base_dir, [csv_path])
        if r is None or r.returncode != 0:
            return 0.0
        totals = parse_totals(r.stdout)
        if totals != {"food": 7.0, "drinks": 7.5, "toys": 2.0}:
            return 0.0
    return 1.0


def check_repofix_edge(base_dir: str) -> float:
    cases = [
        # (input, expected_totals, must_have_keyword)
        ("", {}, None),                                   # empty file
        ("date,category,amount\n", {}, None),             # header only
        ("date,category,amount\n\n2024-01-01,books,5.0\n\n", {"books": 5.0}, None),
        ("date,category,amount\n2024-01-01,books,abc\n2024-01-02,food,3.0\n",
         {"food": 3.0}, "books"),                          # non-numeric skipped
        ("date,category,amount\n2024-01-01,电子,10.5\n", {"电子": 10.5}, None),  # unicode
    ]
    for text, expected, absent in cases:
        with tempfile.TemporaryDirectory() as td:
            csv_path = write_csv(os.path.join(td, "e.csv"), text)
            r = run_pipeline(base_dir, [csv_path])
            if r is None or r.returncode != 0:
                return 0.0
            totals = parse_totals(r.stdout)
            if expected != {} and totals != expected:
                return 0.0
            if absent and absent in r.stdout:
                return 0.0
    return 1.0


def check_repofix_regression(base_dir: str) -> float:
    # 1. A regression test file covering the fixed behaviors was added.
    if not (Path(base_dir) / "tests" / "test_regression.py").exists():
        return 0.0
    # 2. The whole suite (visible + regression) passes.
    pt = run_pytest(base_dir)
    if pt is None or pt.returncode != 0:
        return 0.0
    # 3. pipeline.py was structurally refactored (>= 3 function definitions).
    if count_functions(base_dir) < 3:
        return 0.0
    # 4. Behavior is unchanged on a hidden input.
    with tempfile.TemporaryDirectory() as td:
        csv_path = write_csv(
            os.path.join(td, "h.csv"),
            "date,category,amount\n2024-06-01,alpha,1.5\n2024-06-02,beta,2.5\n",
        )
        r = run_pipeline(base_dir, [csv_path])
        if r is None or r.returncode != 0:
            return 0.0
        if parse_totals(r.stdout) != {"alpha": 1.5, "beta": 2.5}:
            return 0.0
    return 1.0


CHECKERS: dict[str, object] = {
    "repofix_basic": check_repofix_basic,
    "repofix_edge": check_repofix_edge,
    "repofix_regression": check_repofix_regression,
}


# ------------------------------------------------------------------ main

def score(scenario: dict, base_dir: str) -> dict[str, float]:
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
