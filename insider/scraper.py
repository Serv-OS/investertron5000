"""Fetch and parse openinsider.com.

The data lives in a single `<table class="tinytable">`. Column order:

    0  X (alert flags)        9  Qty
    1  Filing Date           10  Owned
    2  Trade Date            11  ΔOwn  (own change %)
    3  Ticker                12  Value
    4  Company Name          13  1d
    5  Insider Name          14  1w
    6  Title                 15  1m
    7  Trade Type            16  6m
    8  Price
"""
import hashlib
import re
from typing import List

import requests
from bs4 import BeautifulSoup

import config


def fetch_html(url: str = None) -> str:
    url = url or config.SOURCE_URL
    resp = requests.get(
        url,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def _money(text: str):
    """'+$1,234,567' -> 1234567.0 ; '' -> None"""
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _int(text: str):
    val = _money(text)
    return int(val) if val is not None else None


def _own_change(text: str):
    """Returns (pct_or_None, is_new_position)."""
    if not text:
        return None, False
    t = text.strip()
    if t.lower().startswith("new"):
        return None, True
    # ">999%" or "+59%" or "-12%"
    cleaned = re.sub(r"[^0-9.\-]", "", t.replace(">", ""))
    if cleaned in ("", "-", ".", "-."):
        return None, False
    try:
        return float(cleaned), False
    except ValueError:
        return None, False


def _trade_hash(ticker, insider, trade_date, qty, price, value) -> str:
    raw = f"{ticker}|{insider}|{trade_date}|{qty}|{price}|{value}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def parse_trades(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tinytable")
    if table is None:
        return []

    body = table.find("tbody") or table
    trades = []
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 13:
            continue
        col = [c.get_text(strip=True) for c in cells]

        ticker = col[3].upper()
        if not ticker:
            continue

        trade_type = col[7]
        own_pct, is_new = _own_change(col[11])
        price = _money(col[8])
        qty = _int(col[9])
        value = _money(col[12])

        # Post-trade price performance columns (1d / 1w / 1m / 6m). These tell
        # us whether the insider is already in the money since they bought.
        def _pct(idx):
            return _money(col[idx].replace("%", "")) if len(col) > idx else None

        trade = {
            "filing_date": col[1],
            "trade_date": col[2],
            "ticker": ticker,
            "company": col[4],
            "insider": col[5],
            "title": col[6],
            "trade_type": trade_type,
            "price": price,
            "qty": abs(qty) if qty is not None else None,
            "owned": _int(col[10]),
            "own_chg_pct": own_pct,
            "is_new_pos": 1 if is_new else 0,
            "value": abs(value) if value is not None else None,
            "ret_1d": _pct(13),
            "ret_1w": _pct(14),
            "ret_1m": _pct(15),
            "ret_6m": _pct(16),
        }
        trade["id"] = _trade_hash(
            ticker, trade["insider"], trade["trade_date"],
            trade["qty"], trade["price"], trade["value"],
        )
        trades.append(trade)
    return trades


def fetch_trades(url: str = None) -> List[dict]:
    return parse_trades(fetch_html(url))
