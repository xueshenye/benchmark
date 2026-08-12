#!/usr/bin/env python3
"""Local mock order API for the support-bot task (test environment).

A stdlib-only HTTP server that serves order data from ``data/orders.json``.
The agent may start it during development to test order queries:

    python3 /workspace/mock_api/server.py     # listens on 127.0.0.1:8123

The verifier starts its own instance with synthetic data at grading time, so
this file's contents do not affect scoring.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8123


def load_orders() -> list[dict]:
    path = os.path.join(BASE_DIR, "data", "orders.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


class Handler(BaseHTTPRequestHandler):
    orders = load_orders()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._reply({"ok": True})
            return
        prefix = "/api/orders/"
        if path.startswith(prefix):
            oid = path[len(prefix):]
            order = next((o for o in self.orders if o["order_id"] == oid), None)
            if order is None:
                self._reply({"error": "order not found"}, status=404)
            else:
                self._reply(order)
            return
        self._reply({"error": "not found"}, status=404)

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass


def main() -> None:
    port = int(os.environ.get("SUPPORT_API_PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"order api listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
