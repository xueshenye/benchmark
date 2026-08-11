#!/bin/bash
# Reference solution implementing all four rounds of the scenario.
set -euo pipefail

mkdir -p /workspace/src
cat > /workspace/src/todo.py <<'PY'
#!/usr/bin/env python3
"""todo — persistent command-line task tracker."""
import argparse
import json
import os
import sys
from datetime import datetime

PRIORITIES = ("high", "medium", "low")
STORE = os.path.join(os.getcwd(), "todos.json")


def load():
    if not os.path.exists(STORE):
        return []
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save(tasks):
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def fmt_line(t):
    suffix = " [done]" if t["status"] == "done" else ""
    return f"{t['id']}: {t['description']}{suffix}"


def task_dict(t):
    return {k: t[k] for k in ("id", "description", "status", "created_at", "priority")}


def emit(items, as_json):
    if as_json:
        print(json.dumps([task_dict(t) for t in items], ensure_ascii=False))
    else:
        for t in items:
            print(fmt_line(t))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="todo", description="persistent task tracker")
    sub = parser.add_subparsers(dest="cmd")

    pa = sub.add_parser("add")
    pa.add_argument("description")
    pa.add_argument("--priority", choices=PRIORITIES, default="medium")

    pl = sub.add_parser("list")
    pl.add_argument("--all", action="store_true")
    pl.add_argument("--status", choices=("pending", "done"))
    pl.add_argument("--priority", choices=PRIORITIES)
    pl.add_argument("--output-json", action="store_true")

    pd = sub.add_parser("done")
    pd.add_argument("id", type=int)

    sub.add_parser("stats")

    pr = sub.add_parser("report")
    pr.add_argument("--output-json", action="store_true")

    psr = sub.add_parser("search")
    psr.add_argument("keyword")
    psr.add_argument("--output-json", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd is None:
        print('usage: todo add "<task>" | list [--all] [--status S] [--priority P] '
              '[--output-json] | done <id> | stats | report [--output-json] | '
              'search "<kw>" [--output-json]')
        return 0

    tasks = load()

    if args.cmd == "add":
        tasks.append({
            "id": max((t["id"] for t in tasks), default=0) + 1,
            "description": args.description,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "priority": args.priority,
        })
        save(tasks)
        return 0

    if args.cmd == "done":
        for t in tasks:
            if t["id"] == args.id:
                t["status"] = "done"
                save(tasks)
                return 0
        print(f"error: no task with id {args.id}", file=sys.stderr)
        return 1

    if args.cmd == "list":
        if args.all:
            items = list(tasks)
        elif args.status:
            items = [t for t in tasks if t["status"] == args.status]
        else:
            items = [t for t in tasks if t["status"] == "pending"]
        if args.priority:
            items = [t for t in items if t.get("priority") == args.priority]
        items.sort(key=lambda t: t["id"])
        emit(items, args.output_json)
        return 0

    if args.cmd == "stats":
        total = len(tasks)
        done = sum(1 for t in tasks if t["status"] == "done")
        print(f"total={total} pending={total - done} done={done}")
        return 0

    if args.cmd == "report":
        counts = {p: sum(1 for t in tasks if t.get("priority") == p) for p in PRIORITIES}
        if args.output_json:
            print(json.dumps(
                [{"priority": p, "count": counts[p]} for p in PRIORITIES],
                ensure_ascii=False,
            ))
        else:
            for p in PRIORITIES:
                print(f"{p}: {counts[p]}")
        return 0

    if args.cmd == "search":
        kw = args.keyword.lower()
        items = [t for t in tasks if kw in t["description"].lower()]
        items.sort(key=lambda t: t["id"])
        emit(items, args.output_json)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

chmod +x /workspace/src/todo.py
ln -sf /workspace/src/todo.py /workspace/todo
