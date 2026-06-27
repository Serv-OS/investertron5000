"""Market-data enrichment + market-context scoring.

openinsider gives us the insider's action but no market context, so for every
ticker we pull a single Nasdaq quote-summary (market cap, sector, 1-yr analyst
target, 52-week range, dividend yield) and cache it in SQLite with a TTL.

From that we derive a 0-100 *market score* — does the market context make this a
good opportunity, independent of the insider? — and a coarse index-regime gauge
(is the broad market near highs or lows?).
"""
import re
from typing import Optional

import requests

import config
from insider import db


# ----------------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------------
def _num(text) -> Optional[float]:
    if text is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(text))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _hi_lo(text):
    """'$317.4/$199.2607' -> (317.4, 199.26)"""
    if not text or "/" not in text:
        return None, None
    hi, _, lo = text.partition("/")
    return _num(hi), _num(lo)


def _fetch_nasdaq(ticker: str) -> Optional[dict]:
    url = f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=stocks"
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    summary = ((resp.json() or {}).get("data") or {}).get("summaryData") or {}
    if not summary:
        return None

    def val(key):
        return (summary.get(key) or {}).get("value")

    hi52, lo52 = _hi_lo(val("FiftTwoWeekHighLow"))
    return {
        "market_cap": _num(val("MarketCap")),
        "sector": val("Sector"),
        "company": None,  # already known from openinsider
        "price": _num(val("PreviousClose")),
        "target": _num(val("OneYrTarget")),
        "hi52": hi52,
        "lo52": lo52,
        "yld": _num(val("Yield")),
    }


# ----------------------------------------------------------------------------
# Cached quote (the thing the rest of the app calls)
# ----------------------------------------------------------------------------
def get_quote(ticker: str, force: bool = False) -> dict:
    """Return a market-context dict for a ticker, cached with a TTL.

    Always returns a dict; fields are None when unknown. Adds derived
    `pct_52w` (0 = at lows, 1 = at highs) and `upside` (analyst target vs price).
    """
    ticker = (ticker or "").upper().strip()
    empty = {"market_cap": None, "sector": None, "company": None, "price": None,
             "target": None, "hi52": None, "lo52": None, "yld": None}
    if not ticker:
        return _derive(empty)

    if not force:
        cached = db.get_cached_quote(ticker)
        if cached is not None and cached["age_hours"] is not None \
                and cached["age_hours"] < config.MARKETCAP_TTL_HOURS:
            return _derive(dict(cached))

    try:
        fetched = _fetch_nasdaq(ticker)
    except Exception:  # noqa: BLE001  (network / rate-limit / unknown symbol)
        fetched = None

    if fetched is None:
        cached = db.get_cached_quote(ticker)  # fall back to any stale data
        return _derive(dict(cached)) if cached is not None else _derive(empty)

    db.set_cached_quote(ticker, fetched)
    return _derive(fetched)


def _derive(q: dict) -> dict:
    price, hi, lo = q.get("price"), q.get("hi52"), q.get("lo52")
    pct_52w = None
    if price and hi and lo and hi > lo:
        pct_52w = max(0.0, min(1.0, (price - lo) / (hi - lo)))
    upside = None
    if price and q.get("target"):
        upside = (q["target"] - price) / price
    q = dict(q)
    q["pct_52w"] = pct_52w
    q["upside"] = upside
    return q


def cached_quote(ticker: str) -> dict:
    """Read the cached quote WITHOUT triggering a network fetch.

    Used on the read path (dashboard / opportunities) so page loads never block
    on the quote API — the poller is responsible for keeping the cache warm.
    """
    empty = {"market_cap": None, "sector": None, "company": None, "price": None,
             "target": None, "hi52": None, "lo52": None, "yld": None}
    row = db.get_cached_quote((ticker or "").upper().strip())
    return _derive(dict(row)) if row is not None else _derive(empty)


# Back-compat helper used by older call sites.
def get_market_cap(ticker: str, force: bool = False):
    q = get_quote(ticker, force=force)
    return q["market_cap"], q["sector"], q["company"]


# ----------------------------------------------------------------------------
# Market-context score (0-100)
# ----------------------------------------------------------------------------
def market_score(q: dict) -> tuple:
    """Score the *market setup* for an opportunity, ignoring the insider.

      proximity to 52w low  0-45   insiders buying near lows = asymmetric value
      analyst upside        0-35   how far below the 1-yr target it trades
      quality/income        0-10   pays a dividend = established business
      + 10 baseline
    """
    factors = {}
    base = 10.0

    pct = q.get("pct_52w")
    if pct is None:
        prox = 15.0  # unknown — neutral
    else:
        prox = 45.0 * (1.0 - pct)   # at lows -> 45, at highs -> 0
    factors["near_lows"] = round(prox, 1)

    up = q.get("upside")
    if up is None:
        ups = 12.0
    elif up >= 0.50:
        ups = 35.0
    elif up >= 0.25:
        ups = 26.0
    elif up >= 0.10:
        ups = 17.0
    elif up >= 0.0:
        ups = 9.0
    else:
        ups = 0.0                   # trades above target = priced for perfection
    factors["analyst_upside"] = round(ups, 1)

    yld = q.get("yld") or 0
    income = 10.0 if yld >= 2 else 5.0 if yld > 0 else 0.0
    factors["income"] = income

    total = base + prox + ups + income
    factors["total"] = round(min(100.0, total))
    return int(round(min(100.0, total))), factors


# ----------------------------------------------------------------------------
# Index-regime gauge (cached ~1h in meta)
# ----------------------------------------------------------------------------
def _fetch_index(symbol: str) -> Optional[dict]:
    url = f"https://stockanalysis.com/api/quotes/s/{symbol}"
    resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                        timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    d = (resp.json() or {}).get("data") or {}
    p, hi, lo = d.get("p"), d.get("h52"), d.get("l52")
    pct = None
    if p and hi and lo and hi > lo:
        pct = max(0.0, min(1.0, (p - lo) / (hi - lo)))
    return {"price": p, "change_pct": d.get("cp"), "pct_52w": pct}


def market_regime(refresh: bool = False) -> dict:
    """Where is the broad market sitting in its 52-week range?

    The poller refreshes this once per cycle (`refresh=True`); everything else
    just reads the cached value, so page loads make no network calls.
    """
    import json
    if not refresh:
        cached = db.get_meta("regime")
        if cached:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass
        return {"spy": None, "qqq": None, "label": "unknown"}

    out = {"spy": None, "qqq": None, "label": "unknown"}
    try:
        spy = _fetch_index("SPY")
        qqq = _fetch_index("QQQ")
        out["spy"], out["qqq"] = spy, qqq
        pct = spy.get("pct_52w") if spy else None
        if pct is None:
            out["label"] = "unknown"
        elif pct >= 0.85:
            out["label"] = "near highs (risk-on)"
        elif pct >= 0.5:
            out["label"] = "mid-range"
        elif pct >= 0.25:
            out["label"] = "below mid (cautious)"
        else:
            out["label"] = "near lows (fearful)"
    except Exception:  # noqa: BLE001
        pass
    db.set_meta("regime", json.dumps(out))
    return out


# ----------------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------------
def human_cap(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"
