"""Deterministic "first-two-milestones" agent for end-to-end discriminator validation.

Writes a single ``/workspace/app.py`` implementing ONLY milestones 1-2 of the
ticket-system task (ticket CRUD + workflow/search/filter + hard delete) with a
JSON-file store — deliberately NOT an importable package, no SQLite, no SLA
``overdue``, no pytest, no soft-delete/restore and no stats. Used to confirm on a
real Novita run that an agent which stops mid-suite scores ``reward=0``:

    round_1=1 round_2=1 round_3=0 round_4=0 → reward=0

Register via import path, e.g.::

    harbor run -e novita --env-file .env \\
        -p tasks/benchmark/ticket-system \\
        -a benchmark.partial_ticket_system:PartialTicketClaude -m deepseek-v4-flash
"""

from __future__ import annotations

import base64

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmark.interactive_agent import InteractiveUserClaude

# M1+M2 only: CRUD + workflow + search/filter + hard delete, JSON-file store.
# No ticket_system package, no SQLite, no overdue, no stats, no soft-delete.
_PARTIAL = r'''#!/usr/bin/env python3
import datetime
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8123
DEFAULT_DB = "/workspace/data/tickets.json"
STATUSES = ("open", "in_progress", "resolved", "closed")
TRANS = {"open": {"in_progress"}, "in_progress": {"open", "resolved"},
         "resolved": {"closed", "in_progress"}, "closed": {"open"}}


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load(db):
    if os.path.exists(db):
        try:
            with open(db, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save(db, tickets):
    with open(db, "w", encoding="utf-8") as f:
        json.dump(tickets, f, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    def _tickets(self):
        return load(self.server.db_path)

    def _save(self, tickets):
        save(self.server.db_path, tickets)

    def _ticket(self, tid):
        for t in self._tickets():
            if t["id"] == tid:
                return t
        return None

    def _next_id(self, tickets):
        return max((t["id"] for t in tickets), default=0) + 1

    def do_GET(self):
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
            tickets = self._tickets()
            q = params.get("q", [None])[0]
            status = params.get("status", [None])[0]
            priority = params.get("priority", [None])[0]
            assignee = params.get("assignee", [None])[0]
            if q:
                ql = q.lower()
                tickets = [t for t in tickets if ql in t.get("title", "").lower()
                           or ql in t.get("description", "").lower()]
            if status:
                tickets = [t for t in tickets if t.get("status") == status]
            if priority:
                tickets = [t for t in tickets if t.get("priority") == priority]
            if assignee:
                tickets = [t for t in tickets if t.get("assignee") == assignee]
            tickets.sort(key=lambda t: t["id"])
            self._json(tickets)
            return
        if path.startswith("/api/tickets/"):
            tid = self._tid(path)
            if tid is None:
                self._json({"error": "not found"}, 404)
                return
            t = self._ticket(tid)
            if t is None:
                self._json({"error": "not found"}, 404)
                return
            self._json(t)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/tickets":
            payload = self._read_json()
            title = (payload.get("title") or "").strip()
            if not title:
                self._json({"error": "title is required"}, 400)
                return
            tickets = self._tickets()
            t = {
                "id": self._next_id(tickets),
                "title": title,
                "description": payload.get("description", ""),
                "reporter": payload.get("reporter", ""),
                "status": "open",
                "priority": payload.get("priority") or "medium",
                "assignee": payload.get("assignee"),
                "created_at": payload.get("created_at") or now_iso(),
                "resolved_at": None,
            }
            tickets.append(t)
            self._save(tickets)
            self._json(t, 201)
            return
        self._json({"error": "not found"}, 404)

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/tickets/"):
            self._json({"error": "not found"}, 404)
            return
        tid = self._tid(path)
        if tid is None:
            self._json({"error": "not found"}, 404)
            return
        payload = self._read_json()
        tickets = self._tickets()
        t = None
        for _x in tickets:
            if _x["id"] == tid:
                t = _x
                break
        if t is None:
            self._json({"error": "not found"}, 404)
            return
        if "status" in payload:
            ns = payload["status"]
            if ns not in STATUSES:
                self._json({"error": "invalid status"}, 400)
                return
            if ns != t["status"]:
                if ns not in TRANS.get(t["status"], set()):
                    self._json({"error": "invalid status transition"}, 400)
                    return
                if ns == "resolved":
                    t["resolved_at"] = t.get("resolved_at") or now_iso()
                elif ns in ("open", "in_progress"):
                    t["resolved_at"] = None
                t["status"] = ns
        for k in ("priority", "assignee", "description", "reporter"):
            if k in payload:
                t[k] = payload[k]
        self._save(tickets)
        self._json(t)
        return

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/tickets/"):
            self._json({"error": "not found"}, 404)
            return
        tid = self._tid(path)
        if tid is None:
            self._json({"error": "not found"}, 404)
            return
        tickets = self._tickets()
        if self._ticket(tid) is None:
            self._json({"error": "not found"}, 404)
            return
        self._save([x for x in tickets if x["id"] != tid])
        self._json({"ok": True})
        return

    def _tid(self, path):
        rest = path[len("/api/tickets/"):]
        if rest and rest.isdigit():
            return int(rest)
        return None

    def _read_json(self):
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

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<title>云服客服工单系统</title></head><body>'
                '<h1>云服客服</h1><div id="tickets">工单列表</div></body></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    db = os.environ.get("TICKET_DB", DEFAULT_DB)
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.db_path = db
    print(f"ticket system (partial) on http://127.0.0.1:{port} db={db}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''


class PartialTicketClaude(InteractiveUserClaude):
    """Deliberately incomplete agent: M1+M2 (CRUD + workflow) only, then stops."""

    @staticmethod
    def name() -> str:
        return "partial-ticket-claude"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Write the partial implementation via base64 to avoid shell-quoting issues.
        b64 = base64.b64encode(_PARTIAL.encode()).decode()
        command = f"printf '%s' '{b64}' | base64 -d > /workspace/app.py"
        await environment.exec(command=command)
        # Skip the interactive loop entirely — this agent never engages.
        if context.metadata is None:
            context.metadata = {}
        context.metadata["deliberately_partial"] = True
