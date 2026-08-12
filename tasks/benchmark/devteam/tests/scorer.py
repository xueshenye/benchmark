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
SCENARIO_PATH_DEFAULT = "/scenario.json"
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


def _not_ok(r) -> bool:
    """True when the command failed (or couldn't run) — used for negative checks."""
    return not _ok(r)


def _out_has(r, *needles) -> bool:
    """True when rc==0 and every needle is in stdout (loose substring match)."""
    return _ok(r) and all(n in r.stdout for n in needles)


def _out_absent(r, *needles) -> bool:
    """True when rc==0 and none of the needles appear in stdout."""
    return _ok(r) and all(n not in r.stdout for n in needles)


# Reward mode: "dense" (default) returns a continuous 0-1 per-round score (fraction
# of sub-checks passed); "binary" returns 0/1 as before. Select via the REWARD_MODE
# env the verifier receives, e.g. `harbor run ... --ve REWARD_MODE=binary`.
REWARD_MODE = os.environ.get("REWARD_MODE", "dense")
if REWARD_MODE not in ("dense", "binary"):
    REWARD_MODE = "dense"


def _score(subs: list) -> tuple[int, int]:
    """Run each sub-check (callable -> bool, or ``(label, callable)`` pair), catching
    crashes; return (passed, total)."""
    passed = 0
    for entry in subs:
        fn = entry[1] if isinstance(entry, tuple) else entry
        try:
            if fn():
                passed += 1
        except Exception:
            pass
    return passed, len(subs)


def _finalize(passed: int, total: int) -> float:
    if total == 0:
        return 0.0
    if REWARD_MODE == "binary":
        return 1.0 if passed == total else 0.0
    return passed / total


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

def _past_before_up(r, past: str, up: str) -> bool:
    """True when the event list is sorted ascending (past before upcoming)."""
    if not _ok(r):
        return False
    i_past, i_up = r.stdout.find(past), r.stdout.find(up)
    return i_past != -1 and i_up != -1 and i_past < i_up


def _date_filter_ok(r, present: str, absent: str) -> bool:
    """True when --date filtering keeps only that day's events."""
    return _ok(r) and present in r.stdout and absent not in r.stdout


def _ev_id(r, title: str) -> str:
    """First token of the event-list line containing ``title`` (or a bogus id)."""
    if not _ok(r):
        return "999999"
    for line in r.stdout.splitlines():
        if title in line:
            parts = line.split()
            return parts[0] if parts else "999999"
    return "999999"


def _dashboard_has(path: str, *needles) -> bool:
    """True when the dashboard HTML file exists and contains every needle."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    return all(n in content for n in needles)


def _json_has(r, pred) -> bool:
    """True when the --output-json payload is an array and any entry satisfies pred."""
    items = _json_array(r)
    return items is not None and any(pred(e) for e in items)


def check_devteam_org(base_dir: str) -> float:
    rng = random.Random(_SEED)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    td = tempfile.mkdtemp()
    subs = [
        ("create project (creator becomes owner)", lambda: _ok(run_devteam(base_dir, td, ["project", "create", proj], user=owner))),
        ("persist devteam.json in cwd", lambda: os.path.exists(os.path.join(td, "devteam.json"))),
        ("project list shows project (fresh process)", lambda: _out_has(run_devteam(base_dir, td, ["project", "list"], user=owner), proj)),
        ("add member + viewer", lambda: all(
            _ok(run_devteam(base_dir, td, ["member", "add", n, "--project", proj, "--role", rl], user=owner))
            for n, rl in ((member, "member"), (viewer, "viewer"))
        )),
        ("member list shows owner/member/viewer", lambda: _out_has(
            run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner),
            f"{owner}: owner", f"{member}: member", f"{viewer}: viewer",
        )),
        ("roles persist across processes", lambda: _out_has(
            run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner), f"{member}: member",
        )),
        ("non-member read blocked", lambda: _not_ok(run_devteam(base_dir, td, ["member", "list", "--project", proj], user=stranger))),
        ("non-member write blocked", lambda: _not_ok(run_devteam(base_dir, td, ["member", "remove", viewer, "--project", proj], user=stranger))),
        ("member cannot add member", lambda: _not_ok(run_devteam(base_dir, td, ["member", "add", stranger, "--project", proj, "--role", "member"], user=member))),
        ("member cannot remove project", lambda: _not_ok(run_devteam(base_dir, td, ["project", "remove", proj], user=member))),
        ("remove non-existent member errors", lambda: _not_ok(run_devteam(base_dir, td, ["member", "remove", stranger, "--project", proj], user=owner))),
        ("owner removes member", lambda: _ok(run_devteam(base_dir, td, ["member", "remove", member, "--project", proj], user=owner))),
        ("removed member gone from list", lambda: _out_absent(run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner), f"{member}: member")),
    ]
    return _finalize(*_score(subs))


def check_devteam_vcs(base_dir: str) -> float:
    rng = random.Random(_SEED + 1)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    td = tempfile.mkdtemp()
    code_dir = os.path.join(td, "projects", proj, "code")
    a_path = os.path.join(code_dir, "a.py")
    a_orig = "def a():\n    return 1\n"
    a_edit = "def a():\n    return 2\n"
    # Setup via the real CLI (doubles as regression); later sub-checks run
    # against this state and fail naturally if a prerequisite didn't hold.
    boot = _bootstrap(base_dir, td, proj, owner, member, viewer)
    r_empty = run_devteam(base_dir, td, ["commit", proj, "-m", "empty"], user=member) if boot else None
    _write(a_path, a_orig)
    _write(os.path.join(code_dir, "b.py"), "x = 1\n")
    _write(os.path.join(code_dir, "数据.py"), "print('hi')\n")
    _write(os.path.join(code_dir, "sub", "lib.py"), "def lib():\n    return 0\n")
    r_init = run_devteam(base_dir, td, ["commit", proj, "-m", "init"], user=member) if boot else None
    r_hist1 = run_devteam(base_dir, td, ["history", proj], user=owner) if boot else None
    _write(a_path, a_edit)
    r_edit = run_devteam(base_dir, td, ["commit", proj, "-m", "edit a"], user=owner) if boot else None
    r_hist2 = run_devteam(base_dir, td, ["history", proj], user=owner) if boot else None
    lines = [l for l in r_hist2.stdout.splitlines() if l.strip()] if _ok(r_hist2) else []
    init_id = next((l.split()[0] for l in lines if "init" in l), None)
    r_roll = run_devteam(base_dir, td, ["rollback", proj, init_id], user=member) if (boot and init_id) else None
    restored = _read(a_path) if r_roll and _ok(r_roll) else None
    subs = [
        ("bootstrap project+members", lambda: bool(boot)),
        ("empty commit ok", lambda: _ok(r_empty)),
        ("commit init ok", lambda: _ok(r_init)),
        ("history shows init + author", lambda: _out_has(r_hist1, "init", member)),
        ("commit edit a ok", lambda: _ok(r_edit)),
        ("history newest-first has edit a", lambda: _ok(r_hist2) and bool(lines) and "edit a" in lines[0]),
        ("rollback to init ok", lambda: _ok(r_roll)),
        ("a.py restored after rollback", lambda: restored == a_orig),
        ("file-history a.py has init+edit", lambda: _out_has(run_devteam(base_dir, td, ["file-history", proj, "a.py"], user=owner), "init", "edit a")),
        ("file-history 数据.py has init", lambda: _out_has(run_devteam(base_dir, td, ["file-history", proj, "数据.py"], user=owner), "init")),
        ("file-history sub/lib.py has init", lambda: _out_has(run_devteam(base_dir, td, ["file-history", proj, "sub/lib.py"], user=owner), "init")),
        ("non-member history blocked", lambda: _not_ok(run_devteam(base_dir, td, ["history", proj], user=stranger))),
        ("non-member commit blocked", lambda: _not_ok(run_devteam(base_dir, td, ["commit", proj, "-m", "hack"], user=stranger))),
        ("rollback bad id errors", lambda: _not_ok(run_devteam(base_dir, td, ["rollback", proj, "999999"], user=member))),
        ("commits persist (fresh process)", lambda: _out_has(run_devteam(base_dir, td, ["history", proj], user=owner), "init")),
    ]
    return _finalize(*_score(subs))


def check_devteam_schedule(base_dir: str) -> float:
    rng = random.Random(_SEED + 2)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    td = tempfile.mkdtemp()
    code_dir = os.path.join(td, "projects", proj, "code")
    _write(os.path.join(code_dir, "a.py"), "def a():\n    return 1\n")
    _write(os.path.join(code_dir, "b.py"), "x = 1\n")
    today = date.today()
    d_past = (today - timedelta(days=2)).isoformat()
    d_upcoming = (today + timedelta(days=3)).isoformat()
    title_up, title_past = "站会", "评审"
    subs = [
        ("bootstrap project+members", lambda: _bootstrap(base_dir, td, proj, owner, member, viewer)),
        ("commit init ok", lambda: _ok(run_devteam(base_dir, td, ["commit", proj, "-m", "init"], user=member))),
        ("event add (upcoming, --member)", lambda: _ok(run_devteam(base_dir, td, ["event", "add", proj, title_up, "--date", d_upcoming, "--member", member], user=member))),
        ("event add (past)", lambda: _ok(run_devteam(base_dir, td, ["event", "add", proj, title_past, "--date", d_past], user=owner))),
        ("event list sorted ascending", lambda: _past_before_up(run_devteam(base_dir, td, ["event", "list", proj], user=owner), title_past, title_up)),
        ("event list --date filter", lambda: _date_filter_ok(run_devteam(base_dir, td, ["event", "list", proj, "--date", d_past], user=owner), title_past, title_up)),
        ("event list empty-date ok", lambda: _ok(run_devteam(base_dir, td, ["event", "list", proj, "--date", (today - timedelta(days=30)).isoformat()], user=owner))),
        ("event remove ok", lambda: _ok(run_devteam(base_dir, td, ["event", "remove", proj, _ev_id(run_devteam(base_dir, td, ["event", "list", proj], user=owner), title_past)], user=member))),
        ("removed event gone", lambda: _out_absent(run_devteam(base_dir, td, ["event", "list", proj], user=owner), title_past)),
        ("event remove non-existent errors", lambda: _not_ok(run_devteam(base_dir, td, ["event", "remove", proj, "999999"], user=member))),
        ("status shows exact counts + upcoming event", lambda: _out_has(
            run_devteam(base_dir, td, ["status", proj], user=owner),
            proj, "成员数: 3", "代码文件数: 2", "提交数: 1", d_upcoming, title_up,
        )),
        ("non-member event list blocked", lambda: _not_ok(run_devteam(base_dir, td, ["event", "list", proj], user=stranger))),
        ("non-member dashboard blocked", lambda: _not_ok(run_devteam(base_dir, td, ["dashboard", proj], user=stranger))),
        ("dashboard file exists", lambda: _ok(run_devteam(base_dir, td, ["dashboard", proj], user=owner)) and os.path.exists(os.path.join(td, f"dashboard-{proj}.html"))),
        ("dashboard content (proj/member/event)", lambda: _dashboard_has(os.path.join(td, f"dashboard-{proj}.html"), proj, member, title_up)),
        ("member list --output-json array", lambda: _json_array(run_devteam(base_dir, td, ["member", "list", "--project", proj, "--output-json"], user=owner)) is not None),
        ("member json has member+role", lambda: _json_has(run_devteam(base_dir, td, ["member", "list", "--project", proj, "--output-json"], user=owner), lambda e: e.get("name") == member and e.get("role") == "member")),
        ("event list --output-json array", lambda: _json_array(run_devteam(base_dir, td, ["event", "list", proj, "--output-json"], user=owner)) is not None),
        ("history --output-json array", lambda: _json_array(run_devteam(base_dir, td, ["history", proj, "--output-json"], user=owner)) is not None),
        ("regression: commit still works", lambda: _ok(run_devteam(base_dir, td, ["commit", proj, "-m", "after schedule"], user=member))),
    ]
    return _finalize(*_score(subs))


def check_devteam_quality(base_dir: str) -> float:
    rng = random.Random(_SEED + 3)
    proj, owner, member, viewer, stranger = _pick(rng, 5)
    td = tempfile.mkdtemp()
    code_dir = os.path.join(td, "projects", proj, "code")
    _write(os.path.join(code_dir, "bug.py"), "def broken(:\n    pass\n")
    _write(os.path.join(code_dir, "todo_comment.py"), "# TODO: add auth\nvalue = 1\n")
    _write(os.path.join(code_dir, "todo_string.py"), 'msg = "# TODO: not a marker"\nprint(msg)\n')
    _write(os.path.join(code_dir, "undef.py"), "def f():\n    return missing_name\n")
    _write(os.path.join(code_dir, "clean.py"), "def compute(x):\n    return x * 2\n")
    func_name = "calculate_total"
    _write(os.path.join(code_dir, "sample.py"), f"def {func_name}(items):\n    total = sum(items)\n    return total\n")
    box: dict = {}

    def _check() -> bool:
        r = run_devteam(base_dir, td, ["check", proj], user=owner)
        box["out"] = r.stdout if r else ""
        box["ok"] = _ok(r)
        return box["ok"]

    subs = [
        ("bootstrap project+members", lambda: _bootstrap(base_dir, td, proj, owner, member, viewer)),
        ("check runs (rc 0)", _check),
        ("check reports syntax error", lambda: bool(re.search(r"bug\.py:\d+", box["out"]))),
        ("check reports undefined var", lambda: bool(re.search(r"undef\.py:\d+", box["out"]))),
        ("check reports comment TODO", lambda: "TODO" in box["out"]),
        ("check ignores string TODO", lambda: "todo_string.py" not in box["out"]),
        ("check silent on clean file", lambda: "clean.py" not in box["out"]),
        ("autocomplete prefix match", lambda: _out_has(run_devteam(base_dir, td, ["autocomplete", proj, "calc"], user=owner), func_name)),
        ("viewer can commit (reversal)", lambda: _ok(run_devteam(base_dir, td, ["commit", proj, "-m", "viewer edit"], user=viewer))),
        ("viewer commit in history", lambda: _out_has(run_devteam(base_dir, td, ["history", proj], user=owner), "viewer edit", viewer)),
        ("viewer still cannot manage events (scoped)", lambda: _not_ok(run_devteam(base_dir, td, ["event", "add", proj, "hack", "--date", "2026-08-20"], user=viewer))),
        ("non-member member list blocked", lambda: _not_ok(run_devteam(base_dir, td, ["member", "list", "--project", proj], user=stranger))),
        ("member list still works (memory)", lambda: _out_has(run_devteam(base_dir, td, ["member", "list", "--project", proj], user=owner), f"{owner}: owner")),
    ]
    return _finalize(*_score(subs))


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
