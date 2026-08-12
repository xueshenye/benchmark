#!/bin/bash
# Reference solution implementing all four rounds of the devteam scenario
# (final state: projects/members/roles, mini-VCS, schedule+UI, quality/autocomplete,
# and the M4 permission reversal — viewer can commit).
set -euo pipefail

mkdir -p /workspace/src
cat > /workspace/src/devteam.py <<'PY'
#!/usr/bin/env python3
"""devteam — team collaborative development CLI.

Final state for all four milestones:
- M1 projects + members + roles (owner/member/viewer), non-member gate,
  owner-only member management.
- M2 mini-VCS over projects/<proj>/code/: commit / history / rollback / file-history.
- M3 schedule events + status overview + self-contained HTML dashboard + --output-json.
- M4 check (syntax / undefined vars / TODO) + autocomplete, and the permission
  reversal: viewer can now commit (all members can).
"""
import ast
import builtins
import io
import json
import os
import re
import shutil
import sys
import tokenize
from datetime import date, datetime, timedelta

STORE = os.path.join(os.getcwd(), "devteam.json")
PROJECTS_ROOT = os.path.join(os.getcwd(), "projects")
ROLES = ("owner", "member", "viewer")
BUILTINS = set(dir(builtins))
USAGE = (
    "用法: devteam project <create|list|remove> | member <add|remove|list> "
    "| commit <项目> -m <消息> | history <项目> | rollback <项目> <id> "
    "| file-history <项目> <文件> | event <add|list|remove> | status <项目> "
    "| dashboard <项目> | check <项目> | autocomplete <项目> <前缀>"
)


class DevTeamError(Exception):
    pass


def load():
    if not os.path.exists(STORE):
        return {"projects": {}, "members": {}, "commits": {}, "events": {}}
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("projects", "members", "commits", "events"):
            data.setdefault(key, {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"projects": {}, "members": {}, "commits": {}, "events": {}}


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


# ------------------------------------------------------------- permissions

def role_of(store, proj, user):
    if user == "root":
        return "owner"
    return store.get("members", {}).get(proj, {}).get(user)


def require_member(store, proj, user):
    if proj not in store["projects"]:
        raise DevTeamError(f"项目不存在: {proj}")
    if role_of(store, proj, user) is None:
        raise DevTeamError(f"{user} 不是 {proj} 的成员,无权操作")


def require_owner(store, proj, user):
    require_member(store, proj, user)
    if role_of(store, proj, user) != "owner":
        raise DevTeamError("只有 owner 才能执行此操作")


def require_editor(store, proj, user):
    """owner/member can edit; viewer is read-only (can view, cannot manage)."""
    require_member(store, proj, user)
    if role_of(store, proj, user) == "viewer":
        raise DevTeamError("viewer 只读,不能执行此操作")


# ------------------------------------------------------------- commands

def cmd_project(store, user, args):
    if not args:
        raise DevTeamError("用法: devteam project <create|list|remove> ...")
    action = args[0]
    if action == "create":
        if len(args) < 2:
            raise DevTeamError("用法: devteam project create <项目名>")
        name = args[1]
        if name in store["projects"]:
            raise DevTeamError(f"项目已存在: {name}")
        store["projects"][name] = {"owner": user, "created_at": datetime.now().isoformat(timespec="seconds")}
        store["members"][name] = {user: "owner"}
        store["commits"][name] = []
        store["events"][name] = []
        os.makedirs(code_dir(name), exist_ok=True)
        save(store)
        print(f"created {name}")
        return 0
    if action == "list":
        for name in sorted(store["projects"]):
            print(name)
        return 0
    if action == "remove":
        if len(args) < 2:
            raise DevTeamError("用法: devteam project remove <项目名>")
        name = args[1]
        require_owner(store, name, user)
        del store["projects"][name]
        store["members"].pop(name, None)
        store["commits"].pop(name, None)
        store["events"].pop(name, None)
        shutil.rmtree(os.path.join(PROJECTS_ROOT, name), ignore_errors=True)
        save(store)
        print(f"removed {name}")
        return 0
    raise DevTeamError(f"未知 project 操作: {action}")


def cmd_member(store, user, args):
    if not args:
        raise DevTeamError("用法: devteam member <add|remove|list> ...")
    action = args[0]
    if action == "add":
        if len(args) < 2:
            raise DevTeamError("用法: devteam member add <成员名> --project <项目> [--role owner|member|viewer]")
        name = args[1]
        proj = _opt(args, "--project")
        role = _opt(args, "--role", "member")
        if proj is None:
            raise DevTeamError("缺少 --project")
        if role not in ROLES:
            raise DevTeamError(f"非法角色: {role}")
        require_owner(store, proj, user)
        store["members"][proj][name] = role
        save(store)
        print(f"added {name}")
        return 0
    if action == "remove":
        if len(args) < 2:
            raise DevTeamError("用法: devteam member remove <成员名> --project <项目>")
        name = args[1]
        proj = _opt(args, "--project")
        if proj is None:
            raise DevTeamError("缺少 --project")
        require_owner(store, proj, user)
        if name == store["projects"][proj]["owner"]:
            raise DevTeamError("不能移除 owner")
        if name not in store["members"][proj]:
            raise DevTeamError(f"成员不存在: {name}")
        del store["members"][proj][name]
        save(store)
        print(f"removed {name}")
        return 0
    if action == "list":
        proj = _opt(args, "--project")
        if proj is None:
            raise DevTeamError("缺少 --project")
        require_member(store, proj, user)
        members = store["members"].get(proj, {})
        if "--output-json" in args:
            print(json.dumps([{"name": n, "role": r} for n, r in sorted(members.items())], ensure_ascii=False))
        else:
            for n, r in sorted(members.items()):
                print(f"{n}: {r}")
        return 0
    raise DevTeamError(f"未知 member 操作: {action}")


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
        raise DevTeamError(f"提交快照不存在: {cid}")
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
    if len(args) < 1:
        raise DevTeamError("用法: devteam commit <项目> -m <消息>")
    proj = args[0]
    require_member(store, proj, user)
    msg = _opt(args, "-m") or _opt(args, "--message")
    if not msg:
        raise DevTeamError("commit 需要 -m <消息>")
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
    if len(args) < 1:
        raise DevTeamError("用法: devteam history <项目>")
    proj = args[0]
    require_member(store, proj, user)
    commits = sorted(store["commits"].get(proj, []), key=lambda c: c["id"], reverse=True)
    if "--output-json" in args:
        print(json.dumps(
            [{"id": c["id"], "author": c["author"], "message": c["message"], "time": c["time"]} for c in commits],
            ensure_ascii=False,
        ))
    else:
        for c in commits:
            print(f"{c['id']} {c['author']} {c['time']} {c['message']}")
    return 0


def cmd_rollback(store, user, args):
    if len(args) < 2:
        raise DevTeamError("用法: devteam rollback <项目> <提交id>")
    proj, cid = args[0], args[1]
    require_member(store, proj, user)
    if not any(c["id"] == int(cid) for c in store["commits"].get(proj, [])):
        raise DevTeamError(f"提交不存在: {cid}")
    restore(proj, int(cid))
    print(f"restored to {cid}")
    return 0


def cmd_file_history(store, user, args):
    if len(args) < 2:
        raise DevTeamError("用法: devteam file-history <项目> <文件名>")
    proj, fn = args[0], args[1]
    require_member(store, proj, user)
    commits = sorted(
        (c for c in store["commits"].get(proj, []) if fn in c.get("files", [])),
        key=lambda c: c["id"], reverse=True,
    )
    for c in commits:
        print(f"{c['id']} {c['author']} {c['message']}")
    return 0


def cmd_event(store, user, args):
    if not args:
        raise DevTeamError("用法: devteam event <add|list|remove> ...")
    action = args[0]
    if action == "add":
        if len(args) < 3:
            raise DevTeamError("用法: devteam event add <项目> <标题> --date <YYYY-MM-DD> [--member <成员名>]")
        proj, title = args[1], args[2]
        require_editor(store, proj, user)
        d = _opt(args, "--date")
        m = _opt(args, "--member")
        if d is None:
            raise DevTeamError("event add 需要 --date <YYYY-MM-DD>")
        events = store["events"].setdefault(proj, [])
        eid = max((e["id"] for e in events), default=0) + 1
        events.append({"id": eid, "title": title, "date": d, "member": m})
        save(store)
        print(f"added event {eid}")
        return 0
    if action == "list":
        if len(args) < 2:
            raise DevTeamError("用法: devteam event list <项目> [--date <YYYY-MM-DD>]")
        proj = args[1]
        require_member(store, proj, user)
        d = _opt(args, "--date")
        events = sorted(store["events"].get(proj, []), key=lambda e: (e["date"], e["id"]))
        if d:
            events = [e for e in events if e["date"] == d]
        if "--output-json" in args:
            print(json.dumps([{"id": e["id"], "date": e["date"], "title": e["title"]} for e in events], ensure_ascii=False))
        else:
            for e in events:
                print(f"{e['id']} {e['date']} {e['title']}")
        return 0
    if action == "remove":
        if len(args) < 3:
            raise DevTeamError("用法: devteam event remove <项目> <id>")
        proj, eid = args[1], args[2]
        require_editor(store, proj, user)
        events = store["events"].get(proj, [])
        new = [e for e in events if e["id"] != int(eid)]
        if len(new) == len(events):
            raise DevTeamError(f"日程不存在: {eid}")
        store["events"][proj] = new
        save(store)
        print("removed")
        return 0
    raise DevTeamError(f"未知 event 操作: {action}")


def cmd_status(store, user, args):
    if len(args) < 1:
        raise DevTeamError("用法: devteam status <项目>")
    proj = args[0]
    require_member(store, proj, user)
    n_members = len(store["members"].get(proj, {}))
    n_files = len(list_code_files(proj))
    n_commits = len(store["commits"].get(proj, []))
    today = date.today()
    up = [
        e for e in store["events"].get(proj, [])
        if e["date"] and today <= date.fromisoformat(e["date"]) <= today + timedelta(days=7)
    ]
    up.sort(key=lambda e: e["date"])
    print(f"项目: {proj}")
    print(f"成员数: {n_members}")
    print(f"代码文件数: {n_files}")
    print(f"提交数: {n_commits}")
    print("未来 7 天日程:")
    for e in up:
        print(f"  {e['date']} {e['title']}")
    if not up:
        print("  (无)")
    return 0


def cmd_dashboard(store, user, args):
    if len(args) < 1:
        raise DevTeamError("用法: devteam dashboard <项目>")
    proj = args[0]
    require_member(store, proj, user)
    members = store["members"].get(proj, {})
    events = sorted(store["events"].get(proj, []), key=lambda e: e["date"])
    rows_m = "".join(f"<li>{n} ({r})</li>" for n, r in sorted(members.items()))
    rows_e = "".join(f"<li>{e['date']} {e['title']}</li>" for e in events)
    html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>devteam 概览 — {proj}</title></head>
<body>
<h1>项目: {proj}</h1>
<h2>成员</h2><ul>{rows_m}</ul>
<h2>日程</h2><ul>{rows_e}</ul>
</body>
</html>"""
    path = os.path.join(os.getcwd(), f"dashboard-{proj}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(path)
    return 0


# ------------------------------------------------------- quality helpers

def _file_issues(path):
    rel = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [(rel, exc.lineno or 1, f"语法错误: {exc.msg}")]
    issues = []
    # TODO markers are detected from real COMMENT tokens only, so a literal
    # '# TODO' inside a string (e.g. msg = "# TODO: not a marker") is not flagged.
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and re.search(r"TODO", tok.string):
                issues.append((rel, tok.start[0], "TODO 标记"))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined and node.id not in BUILTINS and node.id not in ("True", "False", "None"):
                issues.append((rel, node.lineno, f"未定义变量: {node.id}"))
    return issues


def _collect_identifiers(proj):
    names = set()
    d = code_dir(proj)
    if not os.path.isdir(d):
        return names
    for root, _, files in os.walk(d):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
    return names


def cmd_check(store, user, args):
    if len(args) < 1:
        raise DevTeamError("用法: devteam check <项目>")
    proj = args[0]
    require_member(store, proj, user)
    d = code_dir(proj)
    if os.path.isdir(d):
        for root, _, files in os.walk(d):
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                for rel, lineno, msg in _file_issues(path):
                    print(f"{rel}:{lineno}: {msg}")
    return 0  # always exit 0; issues are printed, not errors


def cmd_autocomplete(store, user, args):
    if len(args) < 2:
        raise DevTeamError("用法: devteam autocomplete <项目> <前缀>")
    proj, prefix = args[0], args[1]
    require_member(store, proj, user)
    for name in sorted(_collect_identifiers(proj)):
        if name.startswith(prefix):
            print(name)
    return 0


# ------------------------------------------------------------------ main

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE)
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
        if cmd == "event":
            return cmd_event(store, user, argv[1:])
        if cmd == "status":
            return cmd_status(store, user, argv[1:])
        if cmd == "dashboard":
            return cmd_dashboard(store, user, argv[1:])
        if cmd == "check":
            return cmd_check(store, user, argv[1:])
        if cmd == "autocomplete":
            return cmd_autocomplete(store, user, argv[1:])
        print(f"error: 未知命令 {cmd}", file=sys.stderr)
        return 1
    except DevTeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError:
        print("error: 参数格式错误", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
PY

chmod +x /workspace/src/devteam.py
ln -sf /workspace/src/devteam.py /workspace/devteam
