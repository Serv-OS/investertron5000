"""Vercel serverless endpoint that runs one scrape cycle.

Triggered by the Vercel Cron defined in vercel.json (daily). It scrapes
openinsider, enriches with market data, scores, and writes to Postgres
(Supabase) — the same `poll_once()` the local poller loop uses.

Protected by CRON_SECRET when that env var is set: Vercel automatically sends it
as a `Authorization: Bearer <CRON_SECRET>` header on cron invocations, and we
reject anything else. With no CRON_SECRET set the endpoint is open (fine for
initial setup; set CRON_SECRET in Vercel to lock it down).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make the project root importable regardless of the function's working dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = os.environ.get("CRON_SECRET")
        if secret and self.headers.get("authorization") != f"Bearer {secret}":
            self._respond(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            from insider import db, poller
            db.init_db()
            alerts = poller.poll_once()
            self._respond(200, {"ok": True, "new_alerts": len(alerts)})
        except Exception as exc:  # noqa: BLE001
            self._respond(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # Cron uses GET; allow POST too for manual triggers.
    do_POST = do_GET

    def _respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)
