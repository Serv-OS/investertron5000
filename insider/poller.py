"""The scrape → enrich → score → store → alert pipeline.

`poll_once()` runs the full pipeline a single time and returns the list of new
strong signals it fired alerts for. `run_forever()` loops it on an interval.
"""
import time
import traceback
from datetime import datetime

import config
from insider import db, model, notify, scraper
from insider.marketcap import get_quote, market_regime


def _rescore_ticker(ticker: str) -> None:
    """Recompute cluster count + score for every recent trade of a ticker.

    Cluster size changes as more insiders show up, so when any new trade for a
    ticker lands we refresh the whole recent window for that symbol. This also
    warms the quote cache (market cap + market context) used by the dashboard.
    """
    q = get_quote(ticker)            # fetches + caches full market context
    market_cap, sector = q["market_cap"], q["sector"]
    distinct = db.distinct_insiders(ticker, days=30)
    for trade_id in db.recent_trade_ids(ticker, days=30):
        row = db.get_trade(trade_id)
        if row is None:
            continue
        score, breakdown = model.score_trade(
            value=row["value"],
            title=row["title"],
            own_chg_pct=row["own_chg_pct"],
            is_new_pos=row["is_new_pos"],
            market_cap=market_cap,
            distinct_insiders=distinct,
        )
        db.update_scoring(trade_id, market_cap, sector, distinct, score, breakdown)


def poll_once() -> list:
    started = datetime.now()
    try:
        trades = scraper.fetch_trades()
    except Exception as exc:  # noqa: BLE001
        print(f"[{started:%H:%M:%S}] scrape failed: {exc}")
        return []

    # Keep only purchases at/above the "large trade" threshold.
    candidates = [
        t for t in trades
        if t["trade_type"].upper().startswith("P")
        and t["value"] is not None
        and t["value"] >= config.MIN_TRADE_VALUE
    ]

    new_ids, affected_tickers = [], set()
    for t in candidates:
        if db.insert_trade(t):
            new_ids.append(t["id"])
            affected_tickers.add(t["ticker"])

    # Enrich + score every ticker that gained a new trade this round.
    # A small delay keeps us polite to the (unauthenticated) quote API.
    for ticker in affected_tickers:
        try:
            _rescore_ticker(ticker)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(0.4)

    # An alert fires for a NEW trade that clears the score bar AND sits in a
    # large-enough company.
    alerts = []
    for trade_id in new_ids:
        row = db.get_trade(trade_id)
        if row is None:
            continue
        if row["alerted"]:
            continue
        if row["score"] < config.ALERT_MIN_SCORE:
            continue
        if config.MIN_MARKET_CAP > 0 and (
            row["market_cap"] is None or row["market_cap"] < config.MIN_MARKET_CAP
        ):
            continue
        alerts.append(dict(row))

    if alerts:
        notify.send(alerts)
        db.mark_alerted([a["id"] for a in alerts])

    # Refresh the broad-market regime gauge once per cycle (best-effort).
    try:
        market_regime(refresh=True)
    except Exception:  # noqa: BLE001
        pass

    db.set_meta("last_poll", started.isoformat(timespec="seconds"))
    db.set_meta("last_poll_new", str(len(new_ids)))
    print(f"[{started:%H:%M:%S}] scraped {len(trades)} rows · "
          f"{len(candidates)} large buys · {len(new_ids)} new · "
          f"{len(alerts)} alert(s)")
    return alerts


def run_forever() -> None:
    db.init_db()
    print(f"Polling {config.SOURCE_URL}")
    print(f"  every {config.POLL_INTERVAL_SECONDS}s · "
          f"min trade ${config.MIN_TRADE_VALUE:,.0f} · "
          f"min cap for alerts {config.MIN_MARKET_CAP/1e9:.1f}B · "
          f"alert score ≥ {config.ALERT_MIN_SCORE}")
    while True:
        try:
            poll_once()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(config.POLL_INTERVAL_SECONDS)
