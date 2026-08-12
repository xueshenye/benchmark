#!/bin/bash
# Reference solution for the ticket-system task: writes the full final package
# implementing all four milestones (HTTP service: CRUD, workflow, SQLite + SLA,
# soft-delete/restore + stats). Stdlib only (http.server + sqlite3).
set -euo pipefail

mkdir -p /workspace/ticket_system /workspace/tests

cat > /workspace/ticket_system/__init__.py <<'PYEOF'
"""ticket_system — 云服客服工单系统(内部 HTTP 服务)。"""
PYEOF

cat > /workspace/ticket_system/store.py <<'PYEOF'
"""SQLite 存储层:工单 CRUD、软删除/恢复、过滤查询。"""
from __future__ import annotations

import datetime
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    reporter TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    priority TEXT DEFAULT 'medium',
    assignee TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    deleted INTEGER DEFAULT 0
)
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["deleted"] = bool(d["deleted"])
    return d


def create(conn, *, title, description="", reporter="", priority="medium",
           assignee=None, created_at=None, status="open") -> dict:
    if created_at is None:
        created_at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "INSERT INTO tickets (title, description, reporter, status, priority, assignee, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, description, reporter, status, priority, assignee, created_at),
    )
    conn.commit()
    return get(conn, cur.lastrowid)


def get(conn, tid: int) -> dict | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_all(conn, *, q=None, status=None, priority=None, assignee=None, include_deleted=False) -> list[dict]:
    sql = "SELECT * FROM tickets"
    clauses: list[str] = []
    params: list = []
    if not include_deleted:
        clauses.append("deleted = 0")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if assignee:
        clauses.append("assignee = ?")
        params.append(assignee)
    if q:
        clauses.append("(title LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def update(conn, tid: int, **fields) -> dict | None:
    sets = []
    params = []
    for key in ("status", "priority", "assignee", "description", "reporter", "resolved_at"):
        if key in fields:
            sets.append(f"{key} = ?")
            params.append(fields[key])
    if sets:
        params.append(tid)
        conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    return get(conn, tid)


def soft_delete(conn, tid: int) -> dict | None:
    conn.execute("UPDATE tickets SET deleted = 1 WHERE id = ?", (tid,))
    conn.commit()
    return get(conn, tid)


def restore(conn, tid: int) -> dict | None:
    conn.execute("UPDATE tickets SET deleted = 0 WHERE id = ?", (tid,))
    conn.commit()
    return get(conn, tid)
PYEOF

cat > /workspace/ticket_system/stats.py <<'PYEOF'
"""超时(SLA)与统计。"""
from __future__ import annotations

import datetime
import os

STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("high", "medium", "low")
_DEFAULT_SLA_HOURS = 48.0


def sla_hours() -> float:
    return float(os.environ.get("TICKET_SLA_HOURS", _DEFAULT_SLA_HOURS))


def _parse_ts(value) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def is_overdue(created_at: str, status: str, now: datetime.datetime | None = None) -> bool:
    if status not in ("open", "in_progress"):
        return False
    created = _parse_ts(created_at)
    if created is None:
        return False
    now = now or datetime.datetime.now()
    hours = (now - created).total_seconds() / 3600.0
    return hours > sla_hours()


def compute_stats(store, conn) -> dict:
    rows = store.list_all(conn, include_deleted=False)
    by_status = {s: 0 for s in STATUSES}
    by_priority = {p: 0 for p in PRIORITIES}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
    resolutions: list[float] = []
    overdue = 0
    for r in rows:
        if is_overdue(r["created_at"], r["status"]):
            overdue += 1
        if r["status"] == "resolved" and r.get("resolved_at") and r.get("created_at"):
            c = _parse_ts(r["created_at"])
            rs = _parse_ts(r["resolved_at"])
            if c and rs:
                resolutions.append((rs - c).total_seconds() / 3600.0)
    avg = (sum(resolutions) / len(resolutions)) if resolutions else None
    return {
        "by_status": by_status,
        "by_priority": by_priority,
        "avg_resolution_hours": round(avg, 1) if avg is not None else None,
        "overdue_count": overdue,
    }
PYEOF

cat > /workspace/ticket_system/app.py <<'PYEOF'
"""HTTP 服务(仅用标准库)。"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import stats, store

DEFAULT_PORT = 8123
DEFAULT_DB = "/workspace/data/tickets.db"
STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("high", "medium", "low")

# 严格状态机:open→in_progress→resolved→closed 逐级;resolved 可回 in_progress、closed 可回 open。
VALID_TRANSITIONS = {
    "open": {"in_progress"},
    "in_progress": {"open", "resolved"},
    "resolved": {"closed", "in_progress"},
    "closed": {"open"},
}


def now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def apply_status_machine(current: str, new: str, resolved_at) -> tuple[bool, str | None, object]:
    if new == current:
        return True, None, resolved_at
    allowed = VALID_TRANSITIONS.get(current, set())
    if new not in allowed:
        return False, "invalid status transition", resolved_at
    if new == "resolved":
        resolved_at = resolved_at or now_iso()
    elif new in ("open", "in_progress"):
        resolved_at = None
    return True, None, resolved_at


class Handler(BaseHTTPRequestHandler):
    def _conn(self):
        return store.connect(self.server.db_path)  # type: ignore[attr-defined]

    def _ticket_id(self, path: str) -> int | None:
        prefix = "/api/tickets/"
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if rest and rest.isdigit():
                return int(rest)
        return None

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _attach_overdue(self, row: dict) -> dict:
        row["overdue"] = stats.is_overdue(row.get("created_at", ""), row.get("status", ""))
        return row

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self) -> None:
        body = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            "<title>云服客服工单系统</title></head><body>"
            "<h1>云服客服</h1><div id=\"tickets\">工单列表</div></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/api/health":
            self._json({"status": "ok"})
            return
        if path == "/":
            self._html()
            return
        if path == "/api/tickets":
            conn = self._conn()
            try:
                rows = store.list_all(
                    conn,
                    q=params.get("q", [None])[0],
                    status=params.get("status", [None])[0],
                    priority=params.get("priority", [None])[0],
                    assignee=params.get("assignee", [None])[0],
                    include_deleted=params.get("include_deleted", ["0"])[0] == "1",
                )
                self._json([self._attach_overdue(r) for r in rows])
            finally:
                conn.close()
            return
        if path == "/api/tickets/stats":
            conn = self._conn()
            try:
                self._json(stats.compute_stats(store, conn))
            finally:
                conn.close()
            return
        if path.startswith("/api/tickets/"):
            tid = self._ticket_id(path)
            if tid is None:
                self._json({"error": "not found"}, status=404)
                return
            conn = self._conn()
            try:
                row = store.get(conn, tid)
                if row is None:
                    self._json({"error": "not found"}, status=404)
                    return
                self._json(self._attach_overdue(row))
            finally:
                conn.close()
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/tickets":
            payload = self._read_json()
            title = (payload.get("title") or "").strip()
            if not title:
                self._json({"error": "title is required"}, status=400)
                return
            conn = self._conn()
            try:
                row = store.create(
                    conn,
                    title=title,
                    description=payload.get("description", ""),
                    reporter=payload.get("reporter", ""),
                    priority=payload.get("priority") or "medium",
                    assignee=payload.get("assignee"),
                    created_at=payload.get("created_at") or now_iso(),
                )
                self._json(self._attach_overdue(row), status=201)
            finally:
                conn.close()
            return
        if path.startswith("/api/tickets/") and path.endswith("/restore"):
            tid = self._ticket_id(path[: -len("/restore")])
            if tid is None:
                self._json({"error": "not found"}, status=404)
                return
            conn = self._conn()
            try:
                row = store.get(conn, tid)
                if row is None:
                    self._json({"error": "not found"}, status=404)
                    return
                if not row["deleted"]:
                    self._json({"error": "ticket is not deleted"}, status=409)
                    return
                self._json(self._attach_overdue(store.restore(conn, tid)))
            finally:
                conn.close()
            return
        self._json({"error": "not found"}, status=404)

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/tickets/"):
            self._json({"error": "not found"}, status=404)
            return
        tid = self._ticket_id(path)
        if tid is None:
            self._json({"error": "not found"}, status=404)
            return
        payload = self._read_json()
        conn = self._conn()
        try:
            row = store.get(conn, tid)
            if row is None:
                self._json({"error": "not found"}, status=404)
                return
            fields: dict = {}
            if "status" in payload:
                new_status = payload["status"]
                if new_status not in STATUSES:
                    self._json({"error": "invalid status"}, status=400)
                    return
                ok, err, resolved_at = apply_status_machine(row["status"], new_status, row.get("resolved_at"))
                if not ok:
                    self._json({"error": err}, status=400)
                    return
                fields["status"] = new_status
                fields["resolved_at"] = resolved_at
            for key in ("priority", "assignee", "description", "reporter"):
                if key in payload:
                    fields[key] = payload[key]
            self._json(self._attach_overdue(store.update(conn, tid, **fields)))
        finally:
            conn.close()

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/tickets/"):
            self._json({"error": "not found"}, status=404)
            return
        tid = self._ticket_id(path)
        if tid is None:
            self._json({"error": "not found"}, status=404)
            return
        conn = self._conn()
        try:
            row = store.get(conn, tid)
            if row is None:
                self._json({"error": "not found"}, status=404)
                return
            store.soft_delete(conn, tid)
            self._json({"ok": True})
        finally:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    db = os.environ.get("TICKET_DB", DEFAULT_DB)
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.db_path = db  # type: ignore[attr-defined]
    print(f"ticket system listening on http://127.0.0.1:{port} db={db}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

cat > /workspace/ticket_system/__main__.py <<'PYEOF'
import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
PYEOF

cat > /workspace/tests/test_ticket_system.py <<'PYEOF'
"""工单系统的进程内 pytest(不启服务,不占端口)。"""
from __future__ import annotations

import datetime

import ticket_system.stats as stats
import ticket_system.store as store


def test_create_defaults_and_get(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "t.db"))
    t = store.create(conn, title="登录问题", description="a", reporter="x")
    assert t["id"] >= 1
    assert t["status"] == "open"
    assert t["priority"] == "medium"
    assert t["deleted"] is False
    assert store.get(conn, t["id"])["title"] == "登录问题"
    conn.close()


def test_overdue_logic() -> None:
    old = (datetime.datetime.now() - datetime.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
    fresh = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    assert stats.is_overdue(old, "open") is True
    assert stats.is_overdue(fresh, "open") is False
    assert stats.is_overdue(old, "resolved") is False


def test_soft_delete_and_restore(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "t.db"))
    t = store.create(conn, title="x", reporter="r")
    store.soft_delete(conn, t["id"])
    assert store.get(conn, t["id"])["deleted"] is True
    assert store.list_all(conn) == []  # deleted excluded by default
    store.restore(conn, t["id"])
    assert store.get(conn, t["id"])["deleted"] is False
    assert len(store.list_all(conn)) == 1
    conn.close()


def test_stats_math(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "t.db"))
    old = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    resolved = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    store.create(conn, title="a", reporter="r", priority="high", created_at=old)
    store.create(conn, title="b", reporter="r", priority="medium")
    c = store.create(conn, title="c", reporter="r", priority="low", created_at=old)
    store.update(conn, c["id"], status="resolved", resolved_at=resolved)
    s = stats.compute_stats(store, conn)
    assert s["by_status"] == {"open": 2, "in_progress": 0, "resolved": 1, "closed": 0}
    assert s["by_priority"] == {"high": 1, "medium": 1, "low": 1}
    assert s["overdue_count"] == 1
    assert isinstance(s["avg_resolution_hours"], float)
    conn.close()
PYEOF
