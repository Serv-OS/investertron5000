"""The trading model.

Turns a single insider BUY into a transparent 0-100 conviction score. The
score is the sum of five weighted components, each capped so no single factor
dominates. Insider *buying* is the only thing scored — insiders sell for many
reasons (diversification, taxes, options), but they buy for essentially one:
they think the stock is cheap.

    value       0-35   how much real money was committed
    role        0-20   seniority of the buyer (CEO/CFO > director > other)
    cluster     0-25   how many distinct insiders bought recently (conviction)
    own_change  0-10   how much the buyer grew their own stake
    cap_class   0-10   bias toward larger, more liquid companies

Tune the weights here; the breakdown is stored per-trade so the dashboard can
explain every score.
"""
import math
from typing import Optional, Tuple

# Weight caps — must sum to 100.
W_VALUE = 35
W_ROLE = 20
W_CLUSTER = 25
W_OWN = 10
W_CAP = 10


def _value_score(value: Optional[float]) -> float:
    """$10k -> 0, $100k -> ~8.75, $1M -> ~17.5, $10M -> ~26, $100M+ -> 35."""
    if not value or value <= 0:
        return 0.0
    raw = W_VALUE * (math.log10(value) - 4) / (8 - 4)
    return max(0.0, min(float(W_VALUE), raw))


def _role_score(title: Optional[str]) -> Tuple[float, str]:
    t = (title or "").upper()
    if any(k in t for k in ("CEO", "CHIEF EXECUTIVE", "PRES", "CHAIR", "COB",
                            "CFO", "CHIEF FINANC", "FOUNDER")):
        return float(W_ROLE), "C-suite/Chair"
    if "DIR" in t:
        return 12.0, "Director"
    if any(k in t for k in ("COO", "CTO", "CIO", "EVP", "SVP", "VP",
                            "OFFICER", "SECRETARY", "TREAS", "GC")):
        return 10.0, "Officer"
    if "10%" in t:
        return 8.0, "10% owner"
    return 6.0, "Other"


def _cluster_score(distinct_insiders: int) -> float:
    table = {0: 5, 1: 5, 2: 12, 3: 18}
    return float(table.get(distinct_insiders, W_CLUSTER))  # 4+ -> full


def _own_change_score(own_chg_pct: Optional[float], is_new: bool) -> float:
    if is_new:
        return float(W_OWN)          # brand-new position = strong conviction
    if own_chg_pct is None:
        return 2.0
    if own_chg_pct >= 100:
        return float(W_OWN)
    if own_chg_pct >= 50:
        return 8.0
    if own_chg_pct >= 20:
        return 6.0
    if own_chg_pct >= 10:
        return 4.0
    return 2.0


def _cap_score(market_cap: Optional[float]) -> Tuple[float, str]:
    if market_cap is None:
        return 0.0, "unknown"
    if market_cap >= 200e9:
        return float(W_CAP), "mega-cap"
    if market_cap >= 10e9:
        return float(W_CAP), "large-cap"
    if market_cap >= 2e9:
        return 6.0, "mid-cap"
    if market_cap >= 300e6:
        return 3.0, "small-cap"
    return 1.0, "micro-cap"


def score_trade(*, value, title, own_chg_pct, is_new_pos,
                market_cap, distinct_insiders) -> Tuple[int, dict]:
    vs = _value_score(value)
    rs, role_label = _role_score(title)
    cs = _cluster_score(distinct_insiders)
    os_ = _own_change_score(own_chg_pct, bool(is_new_pos))
    caps, cap_label = _cap_score(market_cap)

    total = vs + rs + cs + os_ + caps
    breakdown = {
        "value": round(vs, 1),
        "role": round(rs, 1),
        "role_label": role_label,
        "cluster": round(cs, 1),
        "cluster_insiders": distinct_insiders,
        "own_change": round(os_, 1),
        "cap_class": round(caps, 1),
        "cap_label": cap_label,
        "total": round(total),
    }
    return int(round(total)), breakdown
