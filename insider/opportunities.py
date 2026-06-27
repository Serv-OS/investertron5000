"""Company-level opportunity ranking — the "best opportunities" engine.

Individual filings are noisy. This module rolls every recent insider BUY up to
one row per company, then fuses two independent signals into a single 0-100
opportunity score:

    insider conviction (60%)  — size, seniority, how many insiders, ownership Δ
    market context     (40%)  — proximity to 52-wk lows, analyst upside, quality

The output is a ranked shortlist, each with a plain-English thesis explaining
*why* it's an opportunity.
"""
from typing import List, Optional

import config
from insider import db, model
from insider.marketcap import cached_quote, human_cap, market_score

# How the three dimensions combine into the opportunity score. Size is its own
# weighted dimension because the focus is large companies — without it, tiny
# micro-caps near their lows would crowd out genuine large-cap signals.
W_INSIDER = 0.45
W_MARKET = 0.30
W_SIZE = 0.25


def _size_score(market_cap) -> float:
    """0-100 reward for company size (the 'large company' focus)."""
    if market_cap is None:
        return 40.0          # unknown — neutral
    if market_cap >= 200e9:
        return 100.0
    if market_cap >= 10e9:
        return 92.0
    if market_cap >= 2e9:
        return 78.0
    if market_cap >= 1e9:
        return 62.0
    if market_cap >= 300e6:
        return 45.0
    if market_cap >= 50e6:
        return 28.0
    return 15.0


def conviction_tier(score: int) -> str:
    """Human label for how strong a setup the model thinks this is."""
    if score >= 70:
        return "High conviction"
    if score >= 58:
        return "Strong"
    if score >= 46:
        return "Moderate"
    return "Speculative"


def _senior_label(title: str) -> Optional[str]:
    t = (title or "").upper()
    if "CEO" in t or "CHIEF EXECUTIVE" in t:
        return "CEO"
    if "CFO" in t or "CHIEF FINANC" in t:
        return "CFO"
    if "CHAIR" in t or "COB" in t:
        return "Chairman"
    if "PRES" in t:
        return "President"
    if "FOUNDER" in t:
        return "Founder"
    if "DIR" in t:
        return "a director"
    if "10%" in t:
        return "a 10% owner"
    return None


def _range_words(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    if pct < 0.35:
        return "lows"
    if pct > 0.70:
        return "highs"
    return "mid-range"


def _thesis(o: dict) -> str:
    parts = []

    who = f"{o['n_insiders']} insider" + ("s" if o["n_insiders"] != 1 else "")
    label = _senior_label(o["top_title"])
    if label and o["n_insiders"] > 1:
        who += f" (incl. {label})"
    elif label:
        who = label[0].upper() + label[1:] if label.islower() else label
    parts.append(f"{who} bought {human_cap(o['total_value'])}")

    market = []
    rw = _range_words(o["pct_52w"])
    if rw:
        market.append(f"trades near 52-wk {rw} ({o['pct_52w'] * 100:.0f}% of range)")
    if o["upside"] is not None and o["upside"] > 0.10:
        market.append(f"{o['upside'] * 100:.0f}% upside to analyst target")
    if market:
        parts.append("; " + " with ".join(market))

    tail = []
    if o["cap_label"] and o["cap_label"] != "unknown":
        tail.append(o["cap_label"])
    if o["sector"]:
        tail.append(o["sector"])
    if tail:
        parts.append(" · " + " ".join(tail))

    return "".join(parts) + "."


def _tags(o: dict) -> List[str]:
    tags = []
    if o["n_insiders"] >= 3:
        tags.append("cluster buy")
    if o["pct_52w"] is not None and o["pct_52w"] < 0.35:
        tags.append("near 52-wk lows")
    if o["upside"] is not None and o["upside"] >= 0.40:
        tags.append("deep value")
    if o["any_new"]:
        tags.append("new position")
    if o["total_value"] >= 25_000_000:
        tags.append("mega-buy")
    if o["avg_ret_1m"] is not None and o["avg_ret_1m"] > 0:
        tags.append("insiders in the money")
    return tags


def build(days: int = 30, min_value: float = None, min_cap: float = 0,
          limit: int = 50) -> List[dict]:
    rows = db.query_signals(
        min_score=0,
        min_value=min_value if min_value is not None else config.MIN_TRADE_VALUE,
        min_market_cap=0,
        days=days,
        limit=10000,
    )

    groups: dict = {}
    for r in rows:
        groups.setdefault(r["ticker"], []).append(r)

    opps = []
    for ticker, trades in groups.items():
        total_value = sum(t["value"] or 0 for t in trades)
        insiders = {t["insider"] for t in trades}
        n_insiders = len(insiders)

        top = max(trades, key=lambda t: model._role_score(t["title"])[0])
        max_own = max([t["own_chg_pct"] or 0 for t in trades] + [0])
        any_new = any(t["is_new_pos"] for t in trades)

        q = cached_quote(ticker)
        market_cap = q["market_cap"] or next(
            (t["market_cap"] for t in trades if t["market_cap"]), None)
        sector = q["sector"] or trades[0].get("sector")

        insider_score, insider_bd = model.score_trade(
            value=total_value, title=top["title"], own_chg_pct=max_own,
            is_new_pos=any_new, market_cap=market_cap,
            distinct_insiders=n_insiders,
        )
        mkt_score, mkt_bd = market_score(q)
        size_score = _size_score(market_cap)
        opp_score = round(W_INSIDER * insider_score
                          + W_MARKET * mkt_score
                          + W_SIZE * size_score)

        rets = [t["ret_1m"] for t in trades if t.get("ret_1m") is not None]
        avg_ret_1m = sum(rets) / len(rets) if rets else None

        o = {
            "ticker": ticker,
            "company": trades[0]["company"],
            "sector": sector,
            "n_insiders": n_insiders,
            "n_buys": len(trades),
            "total_value": total_value,
            "top_title": top["title"],
            "top_insider": top["insider"],
            "max_own_chg": max_own,
            "any_new": any_new,
            "market_cap": market_cap,
            "cap_label": insider_bd.get("cap_label", "unknown"),
            "price": q["price"],
            "target": q["target"],
            "hi52": q["hi52"],
            "lo52": q["lo52"],
            "pct_52w": q["pct_52w"],
            "upside": q["upside"],
            "yld": q["yld"],
            "avg_ret_1m": avg_ret_1m,
            "insider_score": insider_score,
            "market_score": mkt_score,
            "size_score": round(size_score),
            "opp_score": opp_score,
            "conviction": conviction_tier(opp_score),
            "insider_breakdown": insider_bd,
            "market_breakdown": mkt_bd,
            "last_trade": max(t["trade_date"] for t in trades),
        }
        o["tags"] = _tags(o)
        o["thesis"] = _thesis(o)
        if min_cap and (market_cap is None or market_cap < min_cap):
            continue
        opps.append(o)

    opps.sort(key=lambda o: (o["opp_score"], o["total_value"]), reverse=True)
    return opps[:limit]
