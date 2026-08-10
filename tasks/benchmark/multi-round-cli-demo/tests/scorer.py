#!/usr/bin/env python3
"""Score the interactive multi-turn task against the FINAL workspace state.

Reads ``scenario.json`` and runs each round's ground-truth check against the
final code in the workspace, then writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, "round_2": 1.0, "round_3": 1.0, "reward": 1.0}

Each per-round check verifies that round's requirement is STILL satisfied in
the final state (cumulative regression). An agent that only implemented the
last round scores 0 on earlier rounds, so the product ``reward`` is 0.

Run from the verifier: ``python3 /tests/scorer.py [--base-dir DIR]``.
"""

from __future__ import annotations

import argparse
import csv
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

# Entry points the demo may live behind; first that runs cleanly wins.
CANDIDATE_COMMANDS: list[list[str]] = [
    ["python3", "src/stats.py"],
    ["python3", "stats.py"],
    ["./stats"],
]

_SUMMARY_RE = re.compile(
    r"count=(\d+)\s+mean=([-+0-9.eE]+)\s+min=([-+0-9.eE]+)\s+max=([-+0-9.eE]+)"
)


# ---------------------------------------------------------------- helpers

def run_stats(base_dir: str, args: list[str], timeout: int = 30):
    """Run the stats CLI with the given args; return CompletedProcess or None."""
    for cmd in CANDIDATE_COMMANDS:
        try:
            result = subprocess.run(
                cmd + args,
                cwd=base_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return None
    return None


def parse_summary(text: str) -> dict | None:
    m = _SUMMARY_RE.search(text)
    if not m:
        return None
    return {
        "count": int(m.group(1)),
        "mean": float(m.group(2)),
        "min": float(m.group(3)),
        "max": float(m.group(4)),
    }


def _write_csv(path: str, header: list, rows: list[list]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# ----------------------------------------------------------- round checks

def check_stats_basic(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        data = os.path.join(td, "data.csv")
        _write_csv(data, ["x", "y"], [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50]])
        result = run_stats(base_dir, [data])
        if result is None:
            return 0.0
        s = parse_summary(result.stdout)
        if s is None:
            return 0.0
        return 1.0 if (s["count"] == 5 and abs(s["mean"] - 3.0) < 1e-6
                       and abs(s["min"] - 1.0) < 1e-6 and abs(s["max"] - 5.0) < 1e-6) else 0.0


def check_stats_json(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        data = os.path.join(td, "data.csv")
        _write_csv(data, ["x", "y"], [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50]])
        json_result = run_stats(base_dir, ["--output-json", data])
        if json_result is None:
            return 0.0
        try:
            payload = json.loads(json_result.stdout)
        except json.JSONDecodeError:
            return 0.0
        if not (isinstance(payload, list) and len(payload) == 1):
            return 0.0
        entry = payload[0]
        if not all(k in entry for k in ("count", "mean", "min", "max")):
            return 0.0
        if not (entry["count"] == 5 and abs(entry["mean"] - 3.0) < 1e-6):
            return 0.0
        # Default (no flag) must still be human-readable plain text.
        plain = run_stats(base_dir, [data])
        if plain is None or parse_summary(plain.stdout) is None:
            return 0.0
        return 1.0


def check_stats_multi(base_dir: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a.csv")
        b = os.path.join(td, "b.csv")
        _write_csv(a, ["x"], [[1], [2], [3]])
        _write_csv(b, ["x"], [[10], [20]])
        result = run_stats(base_dir, [a, b])
        if result is None:
            return 0.0
        out = result.stdout
        # One summary per input file.
        matches = list(_SUMMARY_RE.finditer(out))
        if len(matches) != 2:
            return 0.0
        # Two different numeric results → both files were actually processed.
        means = {m.group(2) for m in matches}
        if len(means) != 2:
            return 0.0
        # JSON mode must still work with multiple files.
        jr = run_stats(base_dir, ["--output-json", a, b])
        if jr is None:
            return 0.0
        try:
            payload = json.loads(jr.stdout)
        except json.JSONDecodeError:
            return 0.0
        if not (isinstance(payload, list) and len(payload) == 2):
            return 0.0
        return 1.0


CHECKERS: dict[str, object] = {
    "stats_basic": check_stats_basic,
    "stats_json": check_stats_json,
    "stats_multi": check_stats_multi,
}


# ------------------------------------------------------------------ main

def score(scenario: dict, base_dir: str) -> dict[str, float]:
    """Compute per-round scores + product reward from the final workspace."""
    per_round: dict[str, float] = {}
    for round_spec in scenario["rounds"]:
        test_id = round_spec["test_id"]
        checker = CHECKERS.get(test_id)
        if checker is None:
            raise KeyError(f"no checker implemented for test_id={test_id!r}")
        per_round[f"round_{round_spec['index']}"] = float(checker(base_dir))
    reward = 1.0
    for v in per_round.values():
        reward *= v
    result = {**per_round, "reward": reward}
    return result


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
