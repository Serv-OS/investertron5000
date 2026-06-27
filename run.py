#!/usr/bin/env python3
"""Entry point.

Default: start the background poller AND the web dashboard together.

    python run.py                 # poller + dashboard (recommended)
    python run.py --once          # scrape once, print signals, exit (great for cron)
    python run.py --no-web        # poller only
    python run.py --no-poll       # dashboard only (view existing data)
    python run.py --port 9000     # override dashboard port
"""
import argparse
import threading

import config
from insider import db, poller, webapp


def main():
    ap = argparse.ArgumentParser(description="OpenInsider buy-signal tracker")
    ap.add_argument("--once", action="store_true",
                    help="run one scrape cycle and exit")
    ap.add_argument("--no-web", action="store_true", help="don't start the dashboard")
    ap.add_argument("--no-poll", action="store_true", help="don't start the poller")
    ap.add_argument("--port", type=int, default=config.WEB_PORT)
    args = ap.parse_args()

    config.WEB_PORT = args.port
    db.init_db()

    if args.once:
        poller.poll_once()
        return

    if not args.no_poll:
        t = threading.Thread(target=poller.run_forever, daemon=True)
        t.start()

    if not args.no_web:
        print(f"Dashboard → http://{config.WEB_HOST}:{config.WEB_PORT}/")
        webapp.run_web()
    elif not args.no_poll:
        # Poller-only mode: keep the main thread alive.
        t.join()


if __name__ == "__main__":
    main()
