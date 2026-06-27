"""Storage layer — works on both SQLite (local, zero-setup) and Postgres (cloud).

If `config.DATABASE_URL` is set we talk to Postgres (e.g. Supabase) via psycopg;
otherwise we use a local SQLite file. The rest of the app is unaware of which
backend is active.

Queries are written once using `:name` placeholders and translated to Postgres's
`%(name)s` style on the fly. Date math is done in Python (not in SQL) so the same
queries run on both engines. One row per insider trade, keyed by a stable
content hash so re-scraping a filing is idempotent.
"""
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from typing import Iterable, Optional

import config

IS_PG = bool(config.DATABASE_URL)
_LOCK = threading.Lock()
_NAMED = re.compile(r":(\w+)")

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row


def _q(sql: str) -> str:
    """`:name` -> `%(name)s` for Postgres; unchanged for SQLite.

    Literal `%` (e.g. in `LIKE 'P%'`) must be doubled to `%%` for psycopg's
    pyformat paramstyle — and that has to happen BEFORE we introduce the
    `%(name)s` placeholders, or we'd double their percents too.
    """
    if not IS_PG:
        return sql
    return _NAMED.sub(r"%(\1)s", sql.replace("%", "%%"))


@contextmanager
def _conn():
    if IS_PG:
        con = psycopg.connect(config.DATABASE_URL, row_factory=dict_row,
                              prepare_threshold=None)
    else:
        con = sqlite3.connect(config.DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _cutoff(days: int) -> str:
    """ISO date `days` ago — compared against the TEXT trade_date column."""
    return (date.today() - timedelta(days=days)).isoformat()


def _age_hours(updated_at) -> Optional[float]:
    if updated_at is None:
        return None
    if isinstance(updated_at, str):
        try:
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()
    return (now - updated_at).total_seconds() / 3600.0


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
def init_db() -> None:
    money = "DOUBLE PRECISION" if IS_PG else "REAL"
    bigint = "BIGINT" if IS_PG else "INTEGER"
    ts = "TIMESTAMPTZ DEFAULT now()" if IS_PG else "TEXT DEFAULT (datetime('now'))"

    trades_ddl = f"""
        CREATE TABLE IF NOT EXISTS trades (
            id            TEXT PRIMARY KEY,
            filing_date   TEXT,
            trade_date    TEXT,
            ticker        TEXT,
            company       TEXT,
            insider       TEXT,
            title         TEXT,
            trade_type    TEXT,
            price         {money},
            qty           {bigint},
            owned         {bigint},
            own_chg_pct   {money},
            is_new_pos    INTEGER DEFAULT 0,
            value         {money},
            ret_1d        {money},
            ret_1w        {money},
            ret_1m        {money},
            ret_6m        {money},
            market_cap    {money},
            sector        TEXT,
            cluster_count INTEGER DEFAULT 1,
            score         INTEGER DEFAULT 0,
            breakdown     TEXT,
            alerted       INTEGER DEFAULT 0,
            first_seen    {ts}
        )"""
    cache_ddl = f"""
        CREATE TABLE IF NOT EXISTS marketcap_cache (
            ticker      TEXT PRIMARY KEY,
            market_cap  {money},
            sector      TEXT,
            company     TEXT,
            price       {money},
            target      {money},
            hi52        {money},
            lo52        {money},
            yld         {money},
            updated_at  {ts}
        )"""
    meta_ddl = """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"""

    with _LOCK, _conn() as con:
        cur = con.cursor()
        for ddl in (trades_ddl, cache_ddl, meta_ddl):
            cur.execute(ddl)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_score ON trades(score)")
        _migrate(con, cur)


def _migrate(con, cur) -> None:
    """Add columns to pre-existing databases."""
    money = "DOUBLE PRECISION" if IS_PG else "REAL"
    trade_cols = (("ret_1d", money), ("ret_1w", money),
                  ("ret_1m", money), ("ret_6m", money))
    cache_cols = (("price", money), ("target", money), ("hi52", money),
                  ("lo52", money), ("yld", money))
    if IS_PG:
        for col, decl in trade_cols:
            cur.execute(f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col} {decl}")
        for col, decl in cache_cols:
            cur.execute(f"ALTER TABLE marketcap_cache ADD COLUMN IF NOT EXISTS {col} {decl}")
    else:
        def cols(table):
            return {r["name"] for r in cur.execute(f"PRAGMA table_info({table})")}
        existing_t = cols("trades")
        for col, decl in trade_cols:
            if col not in existing_t:
                cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {decl}")
        existing_c = cols("marketcap_cache")
        for col, decl in cache_cols:
            if col not in existing_c:
                cur.execute(f"ALTER TABLE marketcap_cache ADD COLUMN {col} {decl}")


# ----------------------------------------------------------------------------
# Trades
# ----------------------------------------------------------------------------
def insert_trade(t: dict) -> bool:
    """Insert a parsed trade. Returns True if it was new (not seen before)."""
    sql = _q("""
        INSERT INTO trades
            (id, filing_date, trade_date, ticker, company, insider, title,
             trade_type, price, qty, owned, own_chg_pct, is_new_pos, value,
             ret_1d, ret_1w, ret_1m, ret_6m)
        VALUES
            (:id, :filing_date, :trade_date, :ticker, :company, :insider, :title,
             :trade_type, :price, :qty, :owned, :own_chg_pct, :is_new_pos, :value,
             :ret_1d, :ret_1w, :ret_1m, :ret_6m)
        ON CONFLICT (id) DO NOTHING
    """)
    with _LOCK, _conn() as con:
        cur = con.cursor()
        cur.execute(sql, t)
        return cur.rowcount > 0


def distinct_insiders(ticker: str, days: int = 30) -> int:
    sql = _q("""
        SELECT COUNT(DISTINCT insider) AS n FROM trades
        WHERE ticker = :ticker AND trade_type LIKE 'P%' AND trade_date >= :cutoff
    """)
    with _conn() as con:
        cur = con.cursor()
        cur.execute(sql, {"ticker": ticker, "cutoff": _cutoff(days)})
        row = cur.fetchone()
        return (row["n"] if row and row["n"] is not None else 0)


def recent_trade_ids(ticker: str, days: int = 30) -> list:
    sql = _q("SELECT id FROM trades WHERE ticker = :ticker AND trade_date >= :cutoff")
    with _conn() as con:
        cur = con.cursor()
        cur.execute(sql, {"ticker": ticker, "cutoff": _cutoff(days)})
        return [r["id"] for r in cur.fetchall()]


def update_scoring(trade_id, market_cap, sector, cluster_count, score, breakdown) -> None:
    sql = _q("""
        UPDATE trades SET market_cap = :mc, sector = :sector,
            cluster_count = :cc, score = :score, breakdown = :bd
        WHERE id = :id
    """)
    with _LOCK, _conn() as con:
        con.cursor().execute(sql, {"mc": market_cap, "sector": sector,
                                   "cc": cluster_count, "score": score,
                                   "bd": json.dumps(breakdown), "id": trade_id})


def get_trade(trade_id: str):
    with _conn() as con:
        cur = con.cursor()
        cur.execute(_q("SELECT * FROM trades WHERE id = :id"), {"id": trade_id})
        return cur.fetchone()


def mark_alerted(trade_ids: Iterable[str]) -> None:
    ids = list(trade_ids)
    if not ids:
        return
    sql = _q("UPDATE trades SET alerted = 1 WHERE id = :id")
    with _LOCK, _conn() as con:
        con.cursor().executemany(sql, [{"id": i} for i in ids])


def query_signals(min_score=0, min_value=0.0, min_market_cap=0.0,
                  days=30, limit=300) -> list:
    sql = _q("""
        SELECT * FROM trades
        WHERE score >= :min_score
          AND value >= :min_value
          AND (:min_cap = 0 OR (market_cap IS NOT NULL AND market_cap >= :min_cap))
          AND trade_date >= :cutoff
        ORDER BY score DESC, value DESC
        LIMIT :limit
    """)
    params = {"min_score": min_score, "min_value": min_value,
              "min_cap": min_market_cap, "cutoff": _cutoff(days), "limit": limit}
    with _conn() as con:
        cur = con.cursor()
        cur.execute(sql, params)
        out = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["breakdown"] = json.loads(d.get("breakdown") or "{}")
            except (TypeError, ValueError):
                d["breakdown"] = {}
            out.append(d)
        return out


def stats() -> dict:
    with _conn() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM trades")
        total = cur.fetchone()["c"]
        cur.execute(_q("SELECT COUNT(*) AS c FROM trades WHERE score >= :s"),
                    {"s": config.ALERT_MIN_SCORE})
        big = cur.fetchone()["c"]
        return {"total_trades": total, "strong_signals": big}


# ----------------------------------------------------------------------------
# Quote cache
# ----------------------------------------------------------------------------
def get_cached_quote(ticker: str):
    sql = _q("""
        SELECT market_cap, sector, company, price, target, hi52, lo52, yld, updated_at
        FROM marketcap_cache WHERE ticker = :ticker
    """)
    with _conn() as con:
        cur = con.cursor()
        cur.execute(sql, {"ticker": ticker})
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["age_hours"] = _age_hours(d.pop("updated_at", None))
        return d


def set_cached_quote(ticker, q: dict) -> None:
    now = "now()" if IS_PG else "datetime('now')"
    sql = _q(f"""
        INSERT INTO marketcap_cache
            (ticker, market_cap, sector, company, price, target, hi52, lo52, yld, updated_at)
        VALUES (:ticker, :market_cap, :sector, :company, :price, :target, :hi52, :lo52, :yld, {now})
        ON CONFLICT (ticker) DO UPDATE SET
            market_cap = excluded.market_cap,
            sector     = COALESCE(excluded.sector, marketcap_cache.sector),
            company    = COALESCE(excluded.company, marketcap_cache.company),
            price      = excluded.price, target = excluded.target,
            hi52       = excluded.hi52, lo52 = excluded.lo52, yld = excluded.yld,
            updated_at = excluded.updated_at
    """)
    with _LOCK, _conn() as con:
        con.cursor().execute(sql, {"ticker": ticker, **q})


# ----------------------------------------------------------------------------
# Meta
# ----------------------------------------------------------------------------
def set_meta(key: str, value: str) -> None:
    sql = _q("""
        INSERT INTO meta (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """)
    with _LOCK, _conn() as con:
        con.cursor().execute(sql, {"k": key, "v": value})


def get_meta(key: str, default=None):
    with _conn() as con:
        cur = con.cursor()
        cur.execute(_q("SELECT value FROM meta WHERE key = :k"), {"k": key})
        row = cur.fetchone()
        return row["value"] if row else default
