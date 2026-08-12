"""Discriminator agent for the devteam task: implements ONLY milestones 1–2.

``PartialDevteamClaude`` writes a single-file devteam CLI that covers M1
(projects / members / roles / non-member gate / owner-only member management)
and M2 (commit / history / rollback / file-history) but deliberately NOT:
- M3: event schedule, status overview, HTML dashboard, ``--output-json``.
- M4: ``check``, ``autocomplete``, and the permission reversal — this partial
  still enforces the M1 rule "viewer is read-only, cannot commit".

Used to confirm on a real run that an agent stopping mid-suite scores
``round_1=1 round_2=1 round_3=0 round_4=0 -> reward=0`` (the product reward
collapses on the missing milestones), matching the suite's discriminator
pattern for todo/repofix/pkg/support-bot/ticket-system.
"""

from __future__ import annotations

import base64

from benchmark.interactive_agent import InteractiveUserClaude

_PARTIAL = r'''#!/usr/bin/env python3
"""devteam — partial implementation (milestones 1-2 only)."""
import ast
import builtins
import json
import os
import shutil
import sys
from datetime import datetime

STORE = os.path.join(os.getcwd(), "devteam.json")
PROJECTS_ROOT = os.path.join(os.getcwd(), "projects")
ROLES = ("owner", "member", "viewer")
BUILTINS = set(dir(builtins))


class DevTeamError(Exception):
    pass


def load():
    if not os.path.exists(STORE):
        return {"projects": {}, "members": {}, "commits": {}}
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("projects", "members", "commits"):
            data.setdefault(key, {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"projects": {}, "members": {}, "commits": {}}


def save(store):
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _opt(args, name, default=None):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
    return default


def code_dir(proj):
    return os.path.join(PROJECTS_ROOT, proj, "code")


def snap_dir(proj, cid):
    return os.path.join(PROJECTS_ROOT, proj, ".snapshots", str(cid))


def list_code_files(proj):
    d = code_dir(proj)
    if not os.path.isdir(d):
        return []
    out = []
    for root, _, files in os.walk(d):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            out.append(os.path.relpath(full, d))
    return out


def role_of(store, proj, user):
    if user == "root":
        return "owner"
    return store.get("members", {}).get(proj, {}).get(user)


def require_member(store, proj, user):
    if proj not in store["projects"]:
        raise DevTeamError(f"project not found: {proj}")
    if role_of(store, proj, user) is None:
        raise DevTeamError(f"{user} is not a member of {proj}")


def require_owner(store, proj, user):
    require_member(store, proj, user)
    if role_of(store, proj, user) != "owner":
        raise DevTeamError("only owner can do that")


def cmd_project(store, user, args):
    if not args:
        raise DevTeamError("project <create|list|remove>")
    action = args[0]
    if action == "create":
        name = args[1]
        if name in store["projects"]:
            raise DevTeamError(f"project exists: {name}")
        store["projects"][name] = {"owner": user, "created_at": datetime.now().isoformat(timespec="seconds")}
        store["members"][name] = {user: "owner"}
        store["commits"][name] = []
        os.makedirs(code_dir(name), exist_ok=True)
        save(store)
        print(f"created {name}")
        return 0
    if action == "list":
        for name in sorted(store["projects"]):
            print(name)
        return 0
    if action == "remove":
        name = args[1]
        require_owner(store, name, user)
        del store["projects"][name]
        store["members"].pop(name, None)
        store["commits"].pop(name, None)
        shutil.rmtree(os.path.join(PROJECTS_ROOT, name), ignore_errors=True)
        save(store)
        print(f"removed {name}")
        return 0
    raise DevTeamError(f"unknown project op: {action}")


def cmd_member(store, user, args):
    if not args:
        raise DevTeamError("member <add|remove|list>")
    action = args[0]
    if action == "add":
        name = args[1]
        proj = _opt(args, "--project")
        role = _opt(args, "--role", "member")
        if proj is None:
            raise DevTeamError("missing --project")
        if role not in ROLES:
            raise DevTeamError(f"bad role: {role}")
        require_owner(store, proj, user)
        store["members"][proj][name] = role
        save(store)
        print(f"added {name}")
        return 0
    if action == "remove":
        name = args[1]
        proj = _opt(args, "--project")
        if proj is None:
            raise DevTeamError("missing --project")
        require_owner(store, proj, user)
        if name == store["projects"][proj]["owner"]:
            raise DevTeamError("cannot remove owner")
        del store["members"][proj][name]
        save(store)
        print(f"removed {name}")
        return 0
    if action == "list":
        proj = _opt(args, "--project")
        if proj is None:
            raise DevTeamError("missing --project")
        require_member(store, proj, user)
        for n, r in sorted(store["members"].get(proj, {}).items()):
            print(f"{n}: {r}")
        return 0
    raise DevTeamError(f"unknown member op: {action}")


def snapshot(proj, cid):
    d, s = code_dir(proj), snap_dir(proj, cid)
    os.makedirs(s, exist_ok=True)
    for rel in list_code_files(proj):
        src, dst = os.path.join(d, rel), os.path.join(s, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def restore(proj, cid):
    d, s = code_dir(proj), snap_dir(proj, cid)
    if not os.path.isdir(s):
        raise DevTeamError(f"snapshot missing: {cid}")
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    for root, _, files in os.walk(s):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, s)
            dst = os.path.join(d, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)


def cmd_commit(store, user, args):
    proj = args[0]
    require_member(store, proj, user)
    # M1 rule still enforced here: viewer is read-only (the M4 reversal is absent).
    if role_of(store, proj, user) == "viewer":
        raise DevTeamError("viewer is read-only, cannot commit")
    msg = _opt(args, "-m")
    if not msg:
        raise DevTeamError("commit needs -m <msg>")
    commits = store["commits"].setdefault(proj, [])
    cid = max((c["id"] for c in commits), default=0) + 1
    files = list_code_files(proj)
    snapshot(proj, cid)
    commits.append({
        "id": cid, "message": msg, "author": user,
        "time": datetime.now().isoformat(timespec="seconds"), "files": files,
    })
    save(store)
    print(f"committed {cid}")
    return 0


def cmd_history(store, user, args):
    proj = args[0]
    require_member(store, proj, user)
    commits = sorted(store["commits"].get(proj, []), key=lambda c: c["id"], reverse=True)
    for c in commits:
        print(f"{c['id']} {c['author']} {c['time']} {c['message']}")
    return 0


def cmd_rollback(store, user, args):
    proj, cid = args[0], args[1]
    require_member(store, proj, user)
    if not any(c["id"] == int(cid) for c in store["commits"].get(proj, [])):
        raise DevTeamError(f"commit not found: {cid}")
    restore(proj, int(cid))
    print(f"restored to {cid}")
    return 0


def cmd_file_history(store, user, args):
    proj, fn = args[0], args[1]
    require_member(store, proj, user)
    commits = sorted(
        (c for c in store["commits"].get(proj, []) if fn in c.get("files", [])),
        key=lambda c: c["id"], reverse=True,
    )
    for c in commits:
        print(f"{c['id']} {c['author']} {c['message']}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: devteam project|member|commit|history|rollback|file-history")
        return 0
    cmd = argv[0]
    user = os.environ.get("DEVTEAM_USER", "root")
    store = load()
    try:
        if cmd == "project":
            return cmd_project(store, user, argv[1:])
        if cmd == "member":
            return cmd_member(store, user, argv[1:])
        if cmd == "commit":
            return cmd_commit(store, user, argv[1:])
        if cmd == "history":
            return cmd_history(store, user, argv[1:])
        if cmd == "rollback":
            return cmd_rollback(store, user, argv[1:])
        if cmd == "file-history":
            return cmd_file_history(store, user, argv[1:])
        print(f"error: unknown command {cmd}", file=sys.stderr)
        return 1
    except DevTeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError:
        print("error: bad args", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''


class PartialDevteamClaude(InteractiveUserClaude):
    """Deterministic 'only milestones 1-2' agent for the devteam task.

    Expected end-to-end: ``round_1=1 round_2=1 round_3=0 round_4=0 -> reward=0``
    because the verifier checks every milestone against the final workspace and
    the product reward collapses on the missing M3/M4 features (and the absent
    viewer-commit reversal).
    """

    @staticmethod
    def name() -> str:
        return "partial-devteam-claude"

    async def run(self, instruction, environment, context) -> None:
        # Write the partial implementation into the container via base64 to avoid
        # shell-quoting issues, then skip the interactive loop entirely.
        b64 = base64.b64encode(_PARTIAL.encode()).decode()
        command = (
            "printf '%s' '{b64}' | base64 -d > /workspace/src/devteam.py "
            "&& chmod +x /workspace/src/devteam.py "
            "&& ln -sf /workspace/src/devteam.py /workspace/devteam".format(b64=b64)
        )
        await environment.exec(command=command)
        if context.metadata is None:
            context.metadata = {}
        context.metadata["deliberately_partial"] = True
