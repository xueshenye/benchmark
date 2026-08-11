#!/usr/bin/env python3
"""Score the interactive multi-turn pkg-wordcount task against the FINAL workspace.

Reads ``scenario.json`` and runs each milestone's ground-truth check against the
final package in the workspace, then writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_3": 1.0, "reward": 1.0}

Checks exercise the real artifact: the public API imported in-process, a real
``pip install -e`` + console-script invocation from an unrelated cwd, and the
package's own pytest suite (hidden inputs guard against "building to the test").

Run from the verifier: ``python3 /tests/scorer.py [--base-dir DIR]``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR_DEFAULT = "/workspace"
SCENARIO_PATH_DEFAULT = "/workspace/scenario.json"
REWARD_PATH = "/logs/verifier/reward.json"

_CLI_COUNT_RE = re.compile(r"^(\S+):\s*(\d+)\s*$")


# ------------------------------------------------------------------ helpers

def _import_wordcount(base_dir: str):
    """Import the wordcount package from the workspace (in-process checks)."""
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    try:
        return importlib.import_module("wordcount")
    except ImportError:
        return None


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


def pip_install(base_dir: str, timeout: int = 180) -> bool:
    """Editable-install the package (system, then --user fallback)."""
    attempts = [
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", base_dir],
        [sys.executable, "-m", "pip", "install", "--quiet", "--user", "-e", base_dir],
    ]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return True
    return False


def cli_cmd() -> list[str] | None:
    """Resolve the installed ``wordcount`` console script; fall back to -m."""
    exe = shutil.which("wordcount")
    if exe:
        return [exe]
    # The console script usually lands in the same Python's bin dir (e.g. the
    # venv that installed it), which may not be on PATH.
    candidates = [
        os.path.join(os.path.dirname(sys.executable), "wordcount"),
        os.path.expanduser("~/.local/bin/wordcount"),
        "/usr/local/bin/wordcount",
    ]
    for p in candidates:
        if os.path.exists(p):
            return [p]
    return None


def parse_cli_counts(stdout: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in stdout.splitlines():
        m = _CLI_COUNT_RE.match(line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


# ----------------------------------------------------------- round checks

def check_pkg_api(base_dir: str) -> float:
    if not os.path.exists(os.path.join(base_dir, "pyproject.toml")):
        return 0.0
    wc = _import_wordcount(base_dir)
    if wc is None:
        return 0.0
    if not hasattr(wc, "count"):
        return 0.0
    if wc.count("Hello world! Hello, WORLD.") != {"hello": 2, "world": 2}:
        return 0.0
    if wc.count("one, two; three one") != {"one": 2, "two": 1, "three": 1}:
        return 0.0
    if wc.count("") != {}:
        return 0.0
    return 1.0


def check_pkg_tests(base_dir: str) -> float:
    wc = _import_wordcount(base_dir)
    if wc is None or not hasattr(wc, "top_words"):
        return 0.0
    # count must be unchanged.
    if wc.count("A a a B") != {"a": 3, "b": 1}:
        return 0.0
    # top_words with tie-break by lexicographic order.
    if wc.top_words("a a a b b c", 2) != ["a", "b"]:
        return 0.0
    if wc.top_words("x x y y z", 2) != ["x", "y"]:
        return 0.0
    if wc.top_words("", 3) != []:
        return 0.0
    # The package's own pytest suite passes.
    r = run_pytest(base_dir)
    if r is None or r.returncode != 0:
        return 0.0
    return 1.0


def check_pkg_cli(base_dir: str) -> float:
    if not pip_install(base_dir):
        return 0.0
    # After a real editable install, the API must import from an unrelated cwd
    # (this fails if the package is only importable via cwd path tricks).
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, "-c",
             "import wordcount; print(sorted(wordcount.count('x x y').items()))"],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0 or "[('x', 2), ('y', 1)]" not in r.stdout:
            return 0.0
        # The installed console script runs wordcount <file>.
        data = os.path.join(td, "t.txt")
        with open(data, "w", encoding="utf-8") as f:
            f.write("a a b\n")
        cmd = cli_cmd()
        if cmd is None:
            return 0.0
        r2 = subprocess.run(cmd + [data], cwd=td, capture_output=True, text=True, timeout=60)
        if r2.returncode != 0:
            return 0.0
        if parse_cli_counts(r2.stdout) != {"a": 2, "b": 1}:
            return 0.0
    # The test suite still passes after install.
    r3 = run_pytest(base_dir)
    if r3 is None or r3.returncode != 0:
        return 0.0
    return 1.0


CHECKERS: dict[str, object] = {
    "pkg_api": check_pkg_api,
    "pkg_tests": check_pkg_tests,
    "pkg_cli": check_pkg_cli,
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
