"""
Central configuration. All values can be overridden via environment variables
or a `.env` file placed next to this module (see .env.example).

Nothing here is secret by default — the only sensitive values are SMTP
credentials, which you should put in `.env` (git-ignored).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# ----------------------------------------------------------------------------
# Tiny .env loader (avoids a python-dotenv dependency)
# ----------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Don't clobber values already exported in the real environment.
        os.environ.setdefault(key, val)


_load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ----------------------------------------------------------------------------
# Scraping
# ----------------------------------------------------------------------------
# Source page. The pre-built "latest insider purchases >= $25k" page is the
# most reliable feed; we apply our own (larger) thresholds on top of it.
# You can point this at any openinsider screener URL instead.
SOURCE_URL = _get(
    "OI_SOURCE_URL",
    "http://openinsider.com/latest-insider-purchases-25k",
)
USER_AGENT = _get(
    "OI_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
)
POLL_INTERVAL_SECONDS = _get_int("OI_POLL_INTERVAL", 600)  # 10 minutes
REQUEST_TIMEOUT = _get_int("OI_REQUEST_TIMEOUT", 30)


# ----------------------------------------------------------------------------
# What counts as a "large company" / "large trade"
# ----------------------------------------------------------------------------
# Only insider BUYS at/above this dollar value are stored.
MIN_TRADE_VALUE = _get_float("OI_MIN_TRADE_VALUE", 1_000_000)      # $1M
# Alerts (email/desktop) require the company to be at least this big.
MIN_MARKET_CAP = _get_float("OI_MIN_MARKET_CAP", 2_000_000_000)    # $2B
# Composite model score (0-100) required to fire an alert.
ALERT_MIN_SCORE = _get_int("OI_ALERT_MIN_SCORE", 70)


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------
# If DATABASE_URL is set (e.g. a Supabase/Postgres connection string) the app
# uses Postgres; otherwise it falls back to a local SQLite file. This is what
# lets the same code run locally with zero setup AND in the cloud.
DATABASE_URL = _get("DATABASE_URL", "") or _get("OI_DATABASE_URL", "")
DB_PATH = _get("OI_DB_PATH", str(BASE_DIR / "insider.db"))
MARKETCAP_TTL_HOURS = _get_int("OI_MARKETCAP_TTL_HOURS", 24)


# ----------------------------------------------------------------------------
# Notifications
# ----------------------------------------------------------------------------
NOTIFY_TERMINAL = _get_bool("OI_NOTIFY_TERMINAL", True)
NOTIFY_DESKTOP = _get_bool("OI_NOTIFY_DESKTOP", True)   # macOS only
NOTIFY_EMAIL = _get_bool("OI_NOTIFY_EMAIL", False)      # off until SMTP set

SMTP_HOST = _get("OI_SMTP_HOST", "")
SMTP_PORT = _get_int("OI_SMTP_PORT", 587)
SMTP_USER = _get("OI_SMTP_USER", "")
SMTP_PASS = _get("OI_SMTP_PASS", "")
SMTP_USE_TLS = _get_bool("OI_SMTP_USE_TLS", True)
ALERT_FROM = _get("OI_ALERT_FROM", SMTP_USER)
ALERT_TO = _get("OI_ALERT_TO", "")  # comma-separated


# ----------------------------------------------------------------------------
# Web dashboard
# ----------------------------------------------------------------------------
WEB_HOST = _get("OI_WEB_HOST", "127.0.0.1")
WEB_PORT = _get_int("OI_WEB_PORT", 8787)
