"""Vercel serverless entry point for the dashboard.

Vercel's Python runtime serves the module-level WSGI `app`. The dashboard is
read-only (it reads from Postgres/Supabase); polling happens separately in the
scheduled GitHub Action, so these functions never run the scraper.
"""
import os
import sys

# Make the project root importable (so `import config` / `from insider...` work
# regardless of Vercel's function working directory).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insider import db
from insider.webapp import app  # noqa: E402  (WSGI app Vercel serves)

# Ensure tables exist (no-op once created). Safe + cheap on a warm connection.
try:
    db.init_db()
except Exception:  # noqa: BLE001  — never block serving on a transient DB hiccup
    pass
