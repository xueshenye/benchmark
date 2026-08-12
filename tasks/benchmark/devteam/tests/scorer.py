#!/usr/bin/env python3
"""Score the interactive multi-turn devteam task against the FINAL workspace.

Reads ``scenario.json`` and runs each milestone's ground-truth check against the
final code in the workspace, then writes ``/logs/verifier/reward.json``::

    {"round_1": 1.0, ..., "round_4": 1.0, "reward": 1.0}

Each per-milestone check verifies that milestone's requirement is STILL
satisfied in the final state (cumulative regression), and re-exercises earlier
commands so a regression-free implementation is required for reward 1.

No hardcoded test inputs: project/member names are sampled from a name pool with
a seeded RNG, event dates are computed relative to ``date.today()``, and the
code files under check are generated at grading time (seeded function names /
injected issues). The verifier drives the real CLI in a temp cwd and asserts its
actual stdout / exit codes / persisted state.

Run from the verifier: ``python3 /tests/scorer.py [--base-dir DIR]``.
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
from datetime import date, timedelta
from pathlib import Path

BASE_DIR_DEFAULT = "/workspace"
SCENARIO_PATH_DEFAULT = "/workspace/scenario.json"
REWARD_PATH = "/logs/verifier/reward.json"

_SEED = 20260811
_NAME_POOL = [
    "alpha", "beta", "gamma", "delta", "bob", "carol", "dave", "erin",
    "frank", "gina", "hugo", "iris", "leo", "maya", "nora", "omar",
    "pia", "quin", "ruth", "seth",
]


def _pick(rng: random.Random, n: int) -> list[str]:
    """Sample ``n`` distinct names from the pool (inputs generated at grade time)."""
    pool = list(_NAME_POOL)
    return [pool.pop(rng.randrange(len(pool))) for _ in range(n)]


# Entry points the devteam CLI may live behind; first that launches wins.
# Absolute paths are used so the scorer can run the tool from an isolated cwd
# (the store file ``devteam.json`` then lands in that cwd, as ground-truth).
def _devteam_candidates(base_dir: str) -> list[list[str]]:
    return [
        [sys.executable, os.path.join(base_dir, "src", "devteam.py")],
        [sys.executable, os.path.join(base_dir, "devteam.py")],
        [os.path.join(base_dir, "devteam")],
    ]


def run_devteam(
    base_dir: str,
    cwd: str,
    args: list[str],
    *,
    user: str = "root",
    timeout: int = 30,
):
    """Run the devteam CLI in ``cwd`` as ``user`` with ``args``.

    Skips candidate entry points whose file does not exist — otherwise
    ``python3 src/devteam.py`` on a missing file exits rc=2 (not
    FileNotFoundError) and would short-circuit before the real entry is tried.
    Returns CompletedProcess or None.
    """
    for cmd in _devteam_candidates(base_dir):
        if not os.path.exists(cmd[-1]):
            continue
        env = dict(os.environ)
        env["PYTHONPATH"] = base_dir
        env["DEVTEAM_USER"] = user
        try:
            result = subprocess.run(
                cmd + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=env,
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


def _json_array(r):
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


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _bootstrap(base_dir: str, cwd: str, proj: str, owner: str, member: str, viewer: str) -> bool:
    """Create a project + two members via the real CLI (doubles as regression)."""
    if not _ok(run_devteam(base_dir, cwd, ["project", "create", proj], user=owner)):
        return False
    for name, role in ((member, "member"), (viewer, "viewer")):
        r = run_devteam(
            base_dir, cwd, ["member", "add", name, "--project", proj, "--role", role], user=owner
        )
        if not _ok(r):
            return False
    return True


# ----------------------------------------------------------- round checks

def check_devteam_org(base_dir: str) -> float:
    rng = random.Random(_SEED)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    with tempfile.TemporaryDirectory() as td:
        # Create a project; the creator becomes owner.
        r = run_devteam(base_dir, td, ["project", "create", proj], user=owner)
        if not _ok(r):
            return 0.0
        # Data must be persisted to a visible devteam.json in the working directory.
        if not os.path.exists(os.path.join(td, "devteam.json")):
            return 0.0
        # Fresh process sees the project (persistence across invocations).
        r = run_devteam(base_dir, td, ["project", "list"], user=owner)
        if not _ok(r) or proj not in r.stdout:
            return 0.0
        # Add members with roles; member list shows 'name: role' incl. the owner.
        for name, role in ((member, "member"), (viewer, "viewer")):
            r = run_devteam(
                base_dir, td, ["member", "add", name, "--project", proj, "--role", role], user=owner
            )
            if not _ok(r):
                return 0.0
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner)
        if not _ok(r):
            return 0.0
        for needle in (f"{owner}: owner", f"{member}: member", f"{viewer}: viewer"):
            if needle not in r.stdout:
                return 0.0
        # Roles persist across processes.
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner)
        if not _ok(r) or f"{member}: member" not in r.stdout:
            return 0.0
        # Permission gates (never reversed by M4): non-member blocked on BOTH
        # read and write ops; only owner manages members / removes projects.
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=stranger)
        if _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["member", "remove", viewer, "--project", proj], user=stranger)
        if _ok(r):
            return 0.0
        r = run_devteam(
            base_dir, td, ["member", "add", stranger, "--project", proj, "--role", "member"], user=member
        )
        if _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["project", "remove", proj], user=member)
        if _ok(r):
            return 0.0
        # Removing a non-existent member errors (no silent success).
        r = run_devteam(base_dir, td, ["member", "remove", stranger, "--project", proj], user=owner)
        if _ok(r):
            return 0.0
        # Owner can remove a member.
        r = run_devteam(base_dir, td, ["member", "remove", member, "--project", proj], user=owner)
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner)
        if not _ok(r) or f"{member}: member" in r.stdout:
            return 0.0
        return 1.0


def check_devteam_vcs(base_dir: str) -> float:
    rng = random.Random(_SEED + 1)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    with tempfile.TemporaryDirectory() as td:
        if not _bootstrap(base_dir, td, proj, owner, member, viewer):
            return 0.0
        code_dir = os.path.join(td, "projects", proj, "code")
        a_path = os.path.join(code_dir, "a.py")
        a_orig = "def a():\n    return 1\n"
        a_edit = "def a():\n    return 2\n"

        # A commit on an empty workspace is valid (snapshots nothing, no crash).
        r = run_devteam(base_dir, td, ["commit", proj, "-m", "empty"], user=member)
        if not _ok(r):
            return 0.0
        # Unicode + nested files participate in snapshots and file-history.
        _write(a_path, a_orig)
        _write(os.path.join(code_dir, "b.py"), "x = 1\n")
        _write(os.path.join(code_dir, "数据.py"), "print('hi')\n")
        _write(os.path.join(code_dir, "sub", "lib.py"), "def lib():\n    return 0\n")

        # Commit snapshots the code workspace; history records the author.
        r = run_devteam(base_dir, td, ["commit", proj, "-m", "init"], user=member)
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["history", proj], user=owner)
        if not _ok(r) or "init" not in r.stdout or member not in r.stdout:
            return 0.0

        # Second commit; history is newest-first.
        _write(a_path, a_edit)
        r = run_devteam(base_dir, td, ["commit", proj, "-m", "edit a"], user=owner)
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["history", proj], user=owner)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        if not lines or "edit a" not in lines[0] or "init" not in r.stdout:
            return 0.0

        # Roll back to the first commit restores the file content.
        init_id = next((l.split()[0] for l in lines if "init" in l), None)
        if init_id is None:
            return 0.0
        r = run_devteam(base_dir, td, ["rollback", proj, init_id], user=member)
        if not _ok(r):
            return 0.0
        if _read(a_path) != a_orig:
            return 0.0

        # Per-file history covers plain, unicode, and nested paths.
        r = run_devteam(base_dir, td, ["file-history", proj, "a.py"], user=owner)
        if not _ok(r) or "init" not in r.stdout or "edit a" not in r.stdout:
            return 0.0
        r = run_devteam(base_dir, td, ["file-history", proj, "数据.py"], user=owner)
        if not _ok(r) or "init" not in r.stdout:
            return 0.0
        r = run_devteam(base_dir, td, ["file-history", proj, "sub/lib.py"], user=owner)
        if not _ok(r) or "init" not in r.stdout:
            return 0.0

        # A non-member is blocked on BOTH read and write.
        r = run_devteam(base_dir, td, ["history", proj], user=stranger)
        if _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["commit", proj, "-m", "hack"], user=stranger)
        if _ok(r):
            return 0.0

        # Rollback to a non-existent commit errors.
        r = run_devteam(base_dir, td, ["rollback", proj, "999999"], user=member)
        if _ok(r):
            return 0.0

        # Commits persist across processes.
        r = run_devteam(base_dir, td, ["history", proj], user=owner)
        if not _ok(r) or "init" not in r.stdout:
            return 0.0
        return 1.0


def check_devteam_schedule(base_dir: str) -> float:
    rng = random.Random(_SEED + 2)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    with tempfile.TemporaryDirectory() as td:
        if not _bootstrap(base_dir, td, proj, owner, member, viewer):
            return 0.0
        # Code + a commit so status has files/commits to summarize.
        code_dir = os.path.join(td, "projects", proj, "code")
        _write(os.path.join(code_dir, "a.py"), "def a():\n    return 1\n")
        _write(os.path.join(code_dir, "b.py"), "x = 1\n")
        if not _ok(run_devteam(base_dir, td, ["commit", proj, "-m", "init"], user=member)):
            return 0.0

        # Events with dates generated at grading time (relative to today).
        today = date.today()
        d_past = (today - timedelta(days=2)).isoformat()
        d_upcoming = (today + timedelta(days=3)).isoformat()
        title_up, title_past = "站会", "评审"
        r = run_devteam(
            base_dir, td, ["event", "add", proj, title_up, "--date", d_upcoming, "--member", member], user=member
        )
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["event", "add", proj, title_past, "--date", d_past], user=owner)
        if not _ok(r):
            return 0.0

        # List is sorted by date (ascending): the past event precedes the upcoming one.
        r = run_devteam(base_dir, td, ["event", "list", proj], user=owner)
        if not _ok(r) or title_up not in r.stdout or title_past not in r.stdout:
            return 0.0
        idx_past, idx_up = r.stdout.find(title_past), r.stdout.find(title_up)
        if idx_past == -1 or idx_up == -1 or idx_past > idx_up:
            return 0.0
        # Date filter keeps only that day's events.
        r = run_devteam(base_dir, td, ["event", "list", proj, "--date", d_past], user=owner)
        if not _ok(r) or title_past not in r.stdout or title_up in r.stdout:
            return 0.0
        # A date with no events is a valid empty list (exit 0, no error).
        r = run_devteam(
            base_dir, td,
            ["event", "list", proj, "--date", (today - timedelta(days=30)).isoformat()], user=owner
        )
        if not _ok(r):
            return 0.0
        # Remove an event by its id.
        r = run_devteam(base_dir, td, ["event", "list", proj], user=owner)
        ev_id = next((l.split()[0] for l in r.stdout.splitlines() if title_past in l), None)
        if ev_id is None:
            return 0.0
        r = run_devteam(base_dir, td, ["event", "remove", proj, ev_id], user=member)
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["event", "list", proj], user=owner)
        if not _ok(r) or title_past in r.stdout:
            return 0.0
        # Removing a non-existent event errors.
        r = run_devteam(base_dir, td, ["event", "remove", proj, "999999"], user=member)
        if _ok(r):
            return 0.0

        # status: EXACT member/file/commit counts + upcoming event (date and title).
        r = run_devteam(base_dir, td, ["status", proj], user=owner)
        if not _ok(r) or proj not in r.stdout:
            return 0.0
        for needle in ("成员数: 3", "代码文件数: 2", "提交数: 1", d_upcoming, title_up):
            if needle not in r.stdout:
                return 0.0

        # Non-member is blocked on schedule read AND the dashboard write.
        r = run_devteam(base_dir, td, ["event", "list", proj], user=stranger)
        if _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["dashboard", proj], user=stranger)
        if _ok(r):
            return 0.0

        # dashboard writes a self-contained HTML page in the current dir.
        r = run_devteam(base_dir, td, ["dashboard", proj], user=owner)
        if not _ok(r):
            return 0.0
        dash_path = os.path.join(td, f"dashboard-{proj}.html")
        if not os.path.exists(dash_path):
            return 0.0
        html = _read(dash_path)
        for needle in (proj, member, title_up):
            if needle not in html:
                return 0.0

        # --output-json on member list / event list / history.
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj, "--output-json"], user=owner)
        items = _json_array(r)
        if items is None or not all(("name" in e and "role" in e) for e in items):
            return 0.0
        if not any(e.get("name") == member and e.get("role") == "member" for e in items):
            return 0.0
        r = run_devteam(base_dir, td, ["event", "list", proj, "--output-json"], user=owner)
        items = _json_array(r)
        if items is None or not all(("id" in e and "date" in e and "title" in e) for e in items):
            return 0.0
        r = run_devteam(base_dir, td, ["history", proj, "--output-json"], user=owner)
        items = _json_array(r)
        if items is None or not all(("id" in e and "author" in e and "message" in e) for e in items):
            return 0.0

        # Regression: commit still works after the schedule features.
        if not _ok(run_devteam(base_dir, td, ["commit", proj, "-m", "after schedule"], user=member)):
            return 0.0
        return 1.0


def check_devteam_quality(base_dir: str) -> float:
    rng = random.Random(_SEED + 3)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    with tempfile.TemporaryDirectory() as td:
        if not _bootstrap(base_dir, td, proj, owner, member, viewer):
            return 0.0
        code_dir = os.path.join(td, "projects", proj, "code")
        # Code files generated at grading time: real defects + decoys that a
        # precise analyzer must NOT flag.
        _write(os.path.join(code_dir, "bug.py"), "def broken(:\n    pass\n")  # syntax error
        _write(os.path.join(code_dir, "todo_comment.py"), "# TODO: add auth\nvalue = 1\n")  # comment TODO
        _write(
            os.path.join(code_dir, "todo_string.py"),
            'msg = "# TODO: not a marker"\nprint(msg)\n',  # string literal, must NOT flag
        )
        _write(os.path.join(code_dir, "undef.py"), "def f():\n    return missing_name\n")  # undefined var
        _write(os.path.join(code_dir, "clean.py"), "def compute(x):\n    return x * 2\n")  # clean
        func_name = "calculate_total"
        _write(
            os.path.join(code_dir, "sample.py"),
            f"def {func_name}(items):\n    total = sum(items)\n    return total\n",
        )

        # check: reports the syntax error + undefined var + comment TODO as
        # 'file:line'; is SILENT on the string-TODO and the clean file.
        r = run_devteam(base_dir, td, ["check", proj], user=owner)
        if not _ok(r):
            return 0.0
        if not re.search(r"bug\.py:\d+", r.stdout):
            return 0.0
        if not re.search(r"undef\.py:\d+", r.stdout):
            return 0.0
        if "TODO" not in r.stdout:
            return 0.0
        if "todo_string.py" in r.stdout or "clean.py" in r.stdout:
            return 0.0

        # autocomplete matches the defined identifier by prefix.
        r = run_devteam(base_dir, td, ["autocomplete", proj, "calc"], user=owner)
        if not _ok(r) or func_name not in r.stdout:
            return 0.0

        # PERMISSION REVERSAL (scoped): viewer CAN commit/rollback ...
        r = run_devteam(base_dir, td, ["commit", proj, "-m", "viewer edit"], user=viewer)
        if not _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["history", proj], user=owner)
        if not _ok(r) or "viewer edit" not in r.stdout or viewer not in r.stdout:
            return 0.0
        # ... but viewer is STILL read-only for event management (the reversal
        # opened code write access only — events stay owner/member).
        r = run_devteam(base_dir, td, ["event", "add", proj, "hack", "--date", "2026-08-20"], user=viewer)
        if _ok(r):
            return 0.0

        # Regression: the non-member gate and member management still hold.
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=stranger)
        if _ok(r):
            return 0.0
        r = run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner)
        if not _ok(r) or f"{owner}: owner" not in r.stdout:
            return 0.0
        return 1.0


CHECKERS: dict[str, object] = {
    "devteam_org": check_devteam_org,
    "devteam_vcs": check_devteam_vcs,
    "devteam_schedule": check_devteam_schedule,
    "devteam_quality": check_devteam_quality,
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
