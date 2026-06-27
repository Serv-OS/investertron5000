# 📈 Insider Buy Tracker

Continuously scrapes [openinsider.com](http://openinsider.com) (SEC Form 4
filings) for **large insider purchases at large companies** — i.e. executives
and insiders buying stock in their *own* company — scores each one with a
transparent conviction model, surfaces them on a live dashboard, and alerts you
when a strong signal appears.

> Why focus on buys? Insiders sell for many reasons (taxes, diversification,
> expiring options). They buy for essentially one: they think the stock is
> cheap. Clustered buying by senior insiders is one of the more studied
> signals in the literature.

**This is a research/screening tool, not investment advice, and it does not
place trades.**

---

## Quick start

```bash
cd openinsider-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python run.py                 # starts the poller + dashboard
```

Then open the dashboard at **http://127.0.0.1:8787/**.

The poller scrapes every 10 minutes, stores new large buys, scores them, and
fires alerts (terminal + macOS desktop notification by default).

The dashboard has two tabs:

- **Best Opportunities** (home) — the analysis layer: every recent buy is rolled
  up to one row *per company*, fused into a single opportunity score, ranked,
  and given a plain-English thesis. A market-overview strip shows the broad
  market regime and which sectors insiders are buying.
- **Trade Feed** — the raw scored insider buys, one row per filing.

### Run modes

```bash
python run.py                 # poller + dashboard (default)
python run.py --once          # one scrape cycle, print signals, exit (cron)
python run.py --no-web        # poller only (headless)
python run.py --no-poll       # dashboard only (browse existing data)
python run.py --port 9000     # change dashboard port
```

### Run it in the cloud (always-on, public URL)

The app is **backend-agnostic**: it uses a local **SQLite** file by default, and
switches to **Postgres** automatically when `DATABASE_URL` is set — so the exact
same code runs on your Mac and in the cloud. To host it free on
**GitHub + Vercel + Supabase** (dashboard on Vercel, data in Supabase, polling
via a scheduled GitHub Action), follow **[DEPLOY.md](DEPLOY.md)**.

---

## The model (0–100 conviction score)

Each insider **buy** is scored as the sum of five capped components. The
per-trade breakdown is stored and shown as colored bars on the dashboard, so
every score is explainable. Weights live in [`insider/model.py`](insider/model.py).

| Component | Max | What it rewards |
|-----------|----:|-----------------|
| **Value** | 35 | Dollar size of the buy (log scale: $1M ≈ 17, $100M+ ≈ 35) |
| **Role** | 20 | Seniority — CEO/CFO/Chair/Founder = 20, Director = 12, other officer = 10, 10% owner = 8 |
| **Cluster** | 25 | Distinct insiders buying the same ticker in 30 days (1→5, 2→12, 3→18, 4+→25) |
| **Ownership Δ** | 10 | How much the buyer grew their own stake (new position = full marks) |
| **Cap class** | 10 | Bias toward larger, more liquid companies (mega/large = 10, mid = 6…) |

A **cluster** of senior insiders all buying at once is the strongest pattern
the model can find — it pushes value + role + cluster simultaneously.

---

## Best Opportunities (the analysis layer)

The per-filing score above is the raw signal. The **opportunities engine**
([`insider/opportunities.py`](insider/opportunities.py)) steps back and analyses
each *company* against the *market*, fusing three independent dimensions into a
single 0–100 opportunity score:

| Dimension | Weight | What it measures |
|-----------|------:|------------------|
| **Insider conviction** | 45% | Aggregate of all the company's recent buys — total $, top insider's seniority, how many distinct insiders (cluster), biggest ownership increase |
| **Market context** | 30% | Proximity to 52-week lows, upside to the 1-yr analyst target, dividend/quality — *is the setup attractive regardless of the insider?* |
| **Company size** | 25% | Market cap — your explicit "large companies" focus, so big names rank above micro-caps |

Each opportunity gets a **thesis** sentence and **tags** (`cluster buy`,
`near 52-wk lows`, `deep value`, `new position`, `insiders in the money`…), plus
a 52-week range bar showing where it trades. Market context comes from one
cached Nasdaq quote per ticker (cap, sector, analyst target, 52-wk range, yield)
and the broad-market regime from SPY/QQQ's position in their own 52-wk range.

> Example: *"3 insiders (incl. CEO) bought $50M; trades near 52-wk lows (12% of
> range) with 38% upside to analyst target · large-cap Health Care."*

---

## "Large company" / "large trade" thresholds

| Setting | Default | Meaning |
|---------|--------:|---------|
| `OI_MIN_TRADE_VALUE` | `$1,000,000` | Only buys at/above this value are stored |
| `OI_MIN_MARKET_CAP` | `$2,000,000,000` | Alerts require the company to be at least this big |
| `OI_ALERT_MIN_SCORE` | `70` | Model score needed to fire an alert |

The **dashboard** shows every stored large buy ranked by score (and lets you
filter live). **Alerts** apply the stricter market-cap + score bars so you only
get pinged on genuinely large-company, high-conviction events.

Market cap isn't on openinsider, so each ticker is enriched from Nasdaq's
public quote API and cached for 24h ([`insider/marketcap.py`](insider/marketcap.py)).

---

## Configuration

All settings have sensible defaults in [`config.py`](config.py) and can be
overridden via environment variables or a `.env` file. Copy the template:

```bash
cp .env.example .env
```

### Alerts

- **Terminal** and **macOS desktop** notifications are on by default.
- **Email** is off until you fill in SMTP settings in `.env`
  (`OI_NOTIFY_EMAIL=true` + `OI_SMTP_*`). For Gmail, use an
  [App Password](https://myaccount.google.com/apppasswords), not your login.

### Run it as a always-on service

The built-in poller loop already runs "constantly". To survive logout/reboot,
either keep `python run.py` running (e.g. in `tmux`), or schedule the one-shot
mode with cron:

```cron
*/10 * * * * cd /path/to/openinsider-tracker && .venv/bin/python run.py --once >> poll.log 2>&1
```

---

## Project layout

```
openinsider-tracker/
├── run.py                 # entry point (poller thread + Flask dashboard)
├── config.py              # all settings (+ tiny .env loader)
├── requirements.txt
├── .env.example
├── DEPLOY.md              # GitHub + Vercel + Supabase deploy guide
├── vercel.json            # Vercel routing (all paths → the dashboard)
├── api/index.py           # Vercel serverless WSGI entry (read-only dashboard)
├── .github/workflows/
│   └── poll.yml           # scheduled scrape (writes to Postgres) every 30 min
└── insider/
    ├── scraper.py         # fetch + parse the openinsider table
    ├── marketcap.py       # Nasdaq quote (cap/target/52w/yield) + cache, market score, regime
    ├── model.py           # the 0–100 per-filing scoring model
    ├── opportunities.py   # company-level fusion → ranked "best opportunities" + thesis
    ├── db.py              # storage — SQLite locally, Postgres when DATABASE_URL is set
    ├── notify.py          # terminal / desktop / email alerts
    ├── poller.py          # scrape → enrich → score → store → alert
    └── webapp.py          # Flask dashboard (Opportunities + Trade Feed) + JSON API
```

Data is stored in `insider.db` (SQLite, git-ignored).

---

## Notes & limitations

- Respect openinsider — the default 10-min interval is polite. Don't hammer it.
- Market-cap lookups depend on a free, unauthenticated API; the occasional
  ticker may come back unknown (it still scores on the other factors).
- Form 4 filings can lag the actual trade by a couple of days.
- Past insider-buying patterns do **not** guarantee future returns. Do your
  own research. This software places no trades and is not financial advice.
```
