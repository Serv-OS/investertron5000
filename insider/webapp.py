"""Flask dashboard.

Two views:
  /         Best Opportunities — company-level, ranked, with a thesis each
  /trades   Trade Feed — the raw scored insider buys
Plus JSON: /api/opportunities and /api/signals
"""
from flask import Flask, jsonify, render_template_string, request

import config
from insider import __version__, db, opportunities
from insider.marketcap import human_cap, market_regime

app = Flask(__name__)


@app.context_processor
def _inject_globals():
    return {"version": __version__}


def _fnum(name, default):
    try:
        return float(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@app.route("/health")
def health():
    """Diagnostics — confirms config + DB connectivity. No secrets exposed."""
    import os
    info = {
        "version": __version__,
        "backend": "postgres" if db.IS_PG else "sqlite",
        "database_url_set": bool(os.environ.get("DATABASE_URL")
                                 or os.environ.get("OI_DATABASE_URL")),
    }
    try:
        db.init_db()
        info["db"] = "ok"
        info["stats"] = db.stats()
        return jsonify(info), 200
    except Exception as exc:  # noqa: BLE001
        info["db"] = "error"
        info["error"] = f"{type(exc).__name__}: {exc}"
        return jsonify(info), 500


@app.route("/run-poll")
def run_poll():
    """Run one scrape cycle. Hit daily by the Vercel Cron (see vercel.json).

    If CRON_SECRET is set, require it (Vercel cron sends it as a Bearer token;
    a ?key= query param also works for manual triggers). Open if unset.
    """
    import os
    from insider import poller

    secret = os.environ.get("CRON_SECRET")
    if secret:
        bearer = request.headers.get("authorization", "")
        if bearer != f"Bearer {secret}" and request.args.get("key") != secret:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        db.init_db()
        alerts = poller.poll_once()
        return jsonify({"ok": True, "new_alerts": len(alerts)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# ----------------------------------------------------------------------------
# Best Opportunities (home)
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    min_score = _fnum("min_score", 0)
    min_value = _fnum("min_value", config.MIN_TRADE_VALUE)
    min_cap = _fnum("min_cap", 0)
    days = int(_fnum("days", 30))

    opps = [o for o in opportunities.build(days=days, min_value=min_value,
                                           min_cap=min_cap, limit=100)
            if o["opp_score"] >= min_score]

    # "Best chances" — the model's highest-conviction setups, surfaced up top.
    top_picks = opps[:5]

    regime = market_regime()
    sector_tot: dict = {}
    for o in opps:
        if o["sector"]:
            sector_tot[o["sector"]] = sector_tot.get(o["sector"], 0) + o["total_value"]
    top_sectors = sorted(sector_tot.items(), key=lambda kv: kv[1], reverse=True)[:3]

    overview = {
        "n_opps": len(opps),
        "total_value": sum(o["total_value"] for o in opps),
        "clusters": sum(1 for o in opps if o["n_insiders"] >= 3),
        "top_sectors": top_sectors,
        "regime": regime,
    }
    return render_template_string(
        OPPS_PAGE, active="opps", opps=opps, top_picks=top_picks,
        overview=overview, human_cap=human_cap,
        f=dict(min_score=min_score, min_value=min_value, min_cap=min_cap, days=days),
        meta={"last_poll": db.get_meta("last_poll", "never"), **db.stats()},
        cfg=config,
    )


@app.route("/api/opportunities")
def api_opportunities():
    days = int(_fnum("days", 30))
    min_value = _fnum("min_value", config.MIN_TRADE_VALUE)
    return jsonify(opportunities.build(days=days, min_value=min_value, limit=100))


# ----------------------------------------------------------------------------
# Big Sells
# ----------------------------------------------------------------------------
@app.route("/sells")
def sells():
    min_value = _fnum("min_value", config.MIN_SELL_VALUE)
    days = int(_fnum("days", 30))
    rows = db.query_sells(min_value=min_value, days=days, limit=300)
    return render_template_string(
        SELLS_PAGE, active="sells", sells=rows, human_cap=human_cap,
        f=dict(min_value=min_value, days=days),
        meta={"last_poll": db.get_meta("last_poll", "never"), **db.stats()},
        cfg=config,
    )


# ----------------------------------------------------------------------------
# Trade feed
# ----------------------------------------------------------------------------
@app.route("/trades")
def trades():
    min_score = _fnum("min_score", 0)
    min_value = _fnum("min_value", config.MIN_TRADE_VALUE)
    min_cap = _fnum("min_cap", 0)
    days = int(_fnum("days", 30))
    signals = db.query_signals(min_score=min_score, min_value=min_value,
                               min_market_cap=min_cap, days=days, limit=500)
    return render_template_string(
        TRADES_PAGE, active="trades", signals=signals, human_cap=human_cap,
        f=dict(min_score=min_score, min_value=min_value, min_cap=min_cap, days=days),
        meta={"last_poll": db.get_meta("last_poll", "never"), **db.stats()},
        cfg=config,
    )


@app.route("/api/signals")
def api_signals():
    days = int(_fnum("days", 30))
    return jsonify(db.query_signals(
        min_score=_fnum("min_score", 0),
        min_value=_fnum("min_value", config.MIN_TRADE_VALUE),
        min_market_cap=_fnum("min_cap", 0), days=days, limit=500))


# ----------------------------------------------------------------------------
# Shared styling + chrome
# ----------------------------------------------------------------------------
HEAD = r"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --line:#21262d; --muted:#8b949e;
    --text:#e6edf3; --accent:#2f81f7; --green:#3fb950; --amber:#d29922; --red:#f85149;
    --purple:#a371f7; --pink:#db61a2;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  header{padding:16px 24px;border-bottom:1px solid var(--line);display:flex;
         align-items:center;gap:20px;flex-wrap:wrap}
  h1{font-size:17px;margin:0;font-weight:600}
  nav{display:flex;gap:6px}
  nav a{padding:6px 12px;border-radius:7px;color:var(--muted);font-weight:600;font-size:13px}
  nav a.on{background:var(--panel);color:var(--text);border:1px solid var(--line)}
  .wrap{padding:18px 24px;max-width:1400px;margin:0 auto}
  .sub{color:var(--muted);font-size:12px}
  form{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end;
       background:var(--panel);border:1px solid var(--line);border-radius:10px;
       padding:12px 16px;margin-bottom:18px}
  label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--muted);
        text-transform:uppercase;letter-spacing:.04em}
  input{background:#0d1117;border:1px solid var(--line);color:var(--text);
        border-radius:6px;padding:6px 8px;font-size:13px;width:130px}
  button{background:var(--accent);border:0;color:#fff;border-radius:6px;
        padding:8px 16px;font-size:13px;cursor:pointer;font-weight:600}
  .cards{display:flex;gap:14px;margin-bottom:18px;flex-wrap:wrap}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:12px 16px;min-width:130px}
  .stat .n{font-size:20px;font-weight:700}
  .stat .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  .muted{color:var(--muted)}
  .empty{padding:40px;text-align:center;color:var(--muted)}
  footer{padding:18px 24px;color:var(--muted);font-size:12px}
</style>
"""

NAV = """
<header>
  <h1>📈 Insider Buy Tracker</h1>
  <nav>
    <a href="/" class="{{ 'on' if active=='opps' else '' }}">Best Opportunities</a>
    <a href="/sells" class="{{ 'on' if active=='sells' else '' }}">Big Sells</a>
    <a href="/trades" class="{{ 'on' if active=='trades' else '' }}">Trade Feed</a>
  </nav>
  <span class="sub">SEC Form 4 insider buys · scored 0–100 · last poll {{ meta.last_poll }}
    · v{{ version }}</span>
</header>
"""


OPPS_PAGE = r"""
<!doctype html><html lang="en"><head>""" + HEAD + r"""<title>Best Opportunities</title></head><body>
""" + NAV + r"""
<div class="wrap">

  <div class="cards">
    <div class="stat"><div class="n">{{ overview.n_opps }}</div><div class="l">opportunities</div></div>
    <div class="stat"><div class="n">{{ human_cap(overview.total_value) }}</div><div class="l">insider $ (window)</div></div>
    <div class="stat"><div class="n">{{ overview.clusters }}</div><div class="l">cluster buys (3+)</div></div>
    <div class="stat" style="min-width:220px">
      <div class="n" style="font-size:15px;padding-top:3px">
        {% if overview.regime.spy and overview.regime.spy.pct_52w is not none %}
          S&P {{ (overview.regime.spy.pct_52w*100)|int }}% of 52-wk
        {% else %}—{% endif %}
      </div>
      <div class="l">market regime · {{ overview.regime.label }}</div>
    </div>
    {% if overview.top_sectors %}
    <div class="stat" style="min-width:240px">
      <div class="n" style="font-size:13px;padding-top:4px">
        {% for s,v in overview.top_sectors %}{{ s }} ({{ human_cap(v) }}){% if not loop.last %} · {% endif %}{% endfor %}
      </div>
      <div class="l">where insiders are buying</div>
    </div>
    {% endif %}
  </div>

  {% set tone_color = {'bull':'#3fb950','bear':'#f85149','mixed':'#d29922','neutral':'#8b949e'} %}
  {% set tone_icon = {'bull':'🟢','bear':'🔴','mixed':'🟡','neutral':'⚪'} %}
  {% if top_picks %}
  <div style="margin-bottom:22px">
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:10px;flex-wrap:wrap">
      <h2 style="font-size:16px;margin:0;font-weight:700">🎯 Best Chances Today</h2>
      <span class="muted" style="font-size:12px">the model's highest-conviction setups — insider conviction + market setup + company size, ranked</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:12px">
      {% for o in top_picks %}
      {% set tier = 's-hi' if o.opp_score>=70 else 's-mid' if o.opp_score>=55 else 's-lo' %}
      <div style="background:linear-gradient(180deg,rgba(210,153,34,.08),var(--panel));
                  border:1px solid rgba(210,153,34,.35);border-radius:12px;padding:14px;display:flex;gap:14px">
        <div style="text-align:center;min-width:52px">
          <div class="muted" style="font-size:12px;font-weight:700">#{{ loop.index }}</div>
          <div class="{{ tier }}" style="font-size:22px;font-weight:800;border-radius:8px;padding:6px 0;margin-top:4px">{{ o.opp_score }}</div>
        </div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">
            <a href="http://openinsider.com/{{ o.ticker }}" target="_blank" style="font-size:16px;font-weight:700">{{ o.ticker }}</a>
            <span style="font-weight:600">{{ o.company }}</span>
            <span style="font-size:11px;font-weight:700;color:var(--amber);text-transform:uppercase;letter-spacing:.03em">{{ o.conviction }}</span>
            <span title="{{ o.signal.reason }}" style="font-size:10px;font-weight:700;padding:1px 8px;border-radius:999px;border:1px solid {{ tone_color[o.signal.tone] }};color:{{ tone_color[o.signal.tone] }}">{{ tone_icon[o.signal.tone] }} {{ o.signal.label }}</span>
          </div>
          <div style="font-size:13px;margin:6px 0;line-height:1.4">{{ o.thesis }}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            {% for t in o.tags %}<span style="background:#0d1117;border:1px solid var(--line);color:var(--muted);font-size:10px;padding:1px 7px;border-radius:999px">{{ t }}</span>{% endfor %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <form method="get">
    <label>Min opp score <input type="number" name="min_score" value="{{ f.min_score|int }}" min="0" max="100"></label>
    <label>Min trade $ <input type="number" name="min_value" value="{{ f.min_value|int }}" step="100000"></label>
    <label>Min market cap $ <input type="number" name="min_cap" value="{{ f.min_cap|int }}" step="1000000000"></label>
    <label>Days back <input type="number" name="days" value="{{ f.days }}" min="1" max="365"></label>
    <button type="submit">Filter</button>
  </form>

  {% if not opps %}
    <div class="empty">No opportunities match yet — the poller may still be collecting,
      or loosen the filters.</div>
  {% endif %}

  {% if opps %}<h2 style="font-size:14px;margin:4px 0 12px;font-weight:600" class="muted">All opportunities ({{ opps|length }})</h2>{% endif %}

  {% for o in opps %}
  {% set sc = 's-hi' if o.opp_score>=70 else 's-mid' if o.opp_score>=55 else 's-lo' %}
  <div style="display:flex;gap:16px;background:var(--panel);border:1px solid var(--line);
              border-radius:12px;padding:16px;margin-bottom:12px;align-items:flex-start">
    <div style="text-align:center;min-width:64px">
      <div class="{{ sc }}" style="font-size:26px;font-weight:800;border-radius:10px;padding:8px 0">
        {{ o.opp_score }}</div>
      <div class="muted" style="font-size:10px;text-transform:uppercase;margin-top:4px">opp score</div>
    </div>

    <div style="flex:1;min-width:0">
      <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <a href="http://openinsider.com/{{ o.ticker }}" target="_blank"
           style="font-size:18px;font-weight:700">{{ o.ticker }}</a>
        <span style="font-weight:600">{{ o.company }}</span>
        <span class="muted" style="font-size:12px">{{ o.sector or '' }} · {{ human_cap(o.market_cap) }}</span>
        <span title="{{ o.signal.reason }}" style="font-size:10px;font-weight:700;padding:1px 8px;border-radius:999px;border:1px solid {{ tone_color[o.signal.tone] }};color:{{ tone_color[o.signal.tone] }}">{{ tone_icon[o.signal.tone] }} {{ o.signal.label }}</span>
        <a class="muted" style="font-size:12px" href="https://stockanalysis.com/stocks/{{ o.ticker }}/" target="_blank">chart ↗</a>
      </div>

      <div style="margin:8px 0;font-size:14px">{{ o.thesis }}</div>

      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
        {% for t in o.tags %}
        <span style="background:#0d1117;border:1px solid var(--line);color:var(--muted);
                     font-size:11px;padding:2px 8px;border-radius:999px">{{ t }}</span>
        {% endfor %}
      </div>

      <div style="display:flex;gap:22px;flex-wrap:wrap;font-size:12px;align-items:center">
        <div title="insider conviction (60% of score)">
          <span class="muted">insider</span>
          <b style="color:var(--purple)">{{ o.insider_score }}</b></div>
        <div title="market context (30% of score)">
          <span class="muted">market</span>
          <b style="color:var(--green)">{{ o.market_score }}</b></div>
        <div title="company size (25% of score)">
          <span class="muted">size</span>
          <b style="color:var(--pink)">{{ o.size_score }}</b></div>
        <div><span class="muted">buyers</span> <b>{{ o.n_insiders }}</b> ({{ o.n_buys }} buy{{ 's' if o.n_buys!=1 else '' }})</div>
        <div><span class="muted">total</span> <b>${{ '{:,.0f}'.format(o.total_value) }}</b></div>
        {% if o.upside is not none %}
        <div><span class="muted">analyst upside</span>
          <b style="color:{{ 'var(--green)' if o.upside>0 else 'var(--red)' }}">{{ (o.upside*100)|round(0)|int }}%</b></div>
        {% endif %}
        {% if o.price %}<div><span class="muted">price</span> <b>${{ '%.2f'|format(o.price) }}</b></div>{% endif %}
        <div><span class="muted">last buy</span> {{ o.last_trade }}</div>
      </div>

      {% if o.pct_52w is not none %}
      <div style="margin-top:10px;max-width:360px">
        <div style="display:flex;justify-content:space-between;font-size:10px" class="muted">
          <span>52-wk low ${{ '%.2f'|format(o.lo52) if o.lo52 else '—' }}</span>
          <span>high ${{ '%.2f'|format(o.hi52) if o.hi52 else '—' }}</span></div>
        <div style="position:relative;height:8px;background:#0d1117;border:1px solid var(--line);
                    border-radius:5px;margin-top:3px">
          <i style="position:absolute;top:-3px;width:3px;height:12px;border-radius:2px;
                    background:var(--accent);left:calc({{ (o.pct_52w*100)|round(0)|int }}% - 1px)"></i>
        </div>
      </div>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
<footer>
  <b>Opportunity score = 45% insider conviction + 30% market context + 25% company size.</b>
  Insider = $ size · seniority · cluster · ownership Δ. Market = proximity to 52-wk lows ·
  analyst upside · quality. Size = market cap (the large-company focus).<br>
  <b>Insider Signal</b> (🟢 Bullish / 🔴 Bearish / 🟡 Mixed) reflects net insider buying vs
  selling — a data signal derived from public SEC filings, <b>not financial advice and not a
  recommendation to buy or sell</b>. Insiders sell for many reasons (taxes, diversification,
  options); buying is the stronger signal. Do your own research.
  Data: openinsider.com (SEC Form 4) + Nasdaq.
</footer>
</body></html>
"""


TRADES_PAGE = r"""
<!doctype html><html lang="en"><head>""" + HEAD + r"""
<style>
  table{width:100%;border-collapse:collapse;background:var(--panel);
        border:1px solid var(--line);border-radius:10px;overflow:hidden}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
  th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;background:#11161d}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tr:hover td{background:#1b222b}
  .score{font-weight:700;border-radius:6px;padding:3px 9px;display:inline-block;min-width:34px;text-align:center}
  .s-hi{background:rgba(63,185,80,.18);color:var(--green)}
  .s-mid{background:rgba(210,153,34,.18);color:var(--amber)}
  .s-lo{background:rgba(139,148,158,.15);color:var(--muted)}
  .tick{font-weight:700}
  .new{background:rgba(47,129,247,.18);color:var(--accent);font-size:10px;padding:1px 6px;border-radius:4px}
</style>
<title>Trade Feed</title></head><body>
""" + NAV + r"""
<div class="wrap">
  <form method="get">
    <label>Min score <input type="number" name="min_score" value="{{ f.min_score|int }}" min="0" max="100"></label>
    <label>Min trade $ <input type="number" name="min_value" value="{{ f.min_value|int }}" step="100000"></label>
    <label>Min market cap $ <input type="number" name="min_cap" value="{{ f.min_cap|int }}" step="1000000000"></label>
    <label>Days back <input type="number" name="days" value="{{ f.days }}" min="1" max="365"></label>
    <button type="submit">Filter</button>
  </form>

  {% if signals %}
  <table>
    <thead><tr>
      <th>Score</th><th>Ticker</th><th>Company</th><th>Insider</th><th>Title</th>
      <th class="num">Value</th><th class="num">Mkt Cap</th><th class="num">ΔOwn</th>
      <th class="num">1m</th><th>Cluster</th><th>Filed</th>
    </tr></thead>
    <tbody>
    {% for s in signals %}
      <tr>
        <td><span class="score {{ 's-hi' if s.score>=70 else 's-mid' if s.score>=50 else 's-lo' }}">{{ s.score }}</span></td>
        <td class="tick"><a href="http://openinsider.com/{{ s.ticker }}" target="_blank">{{ s.ticker }}</a></td>
        <td>{{ s.company }}{% if s.sector %}<div class="muted" style="font-size:11px">{{ s.sector }}</div>{% endif %}</td>
        <td>{{ s.insider }}</td>
        <td class="muted">{{ s.title }}</td>
        <td class="num">${{ '{:,.0f}'.format(s.value or 0) }}</td>
        <td class="num">{{ human_cap(s.market_cap) }}</td>
        <td class="num">{% if s.is_new_pos %}<span class="new">NEW</span>{% elif s.own_chg_pct is not none %}+{{ s.own_chg_pct|int }}%{% else %}—{% endif %}</td>
        <td class="num">{% if s.ret_1m is not none %}<span style="color:{{ 'var(--green)' if s.ret_1m>=0 else 'var(--red)' }}">{{ '{:+.0f}%'.format(s.ret_1m) }}</span>{% else %}—{% endif %}</td>
        <td>{{ s.cluster_count }} insider(s)</td>
        <td class="muted">{{ s.filing_date }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
    <div class="empty">No trades match these filters yet.</div>
  {% endif %}
</div>
<footer>Raw scored insider buys. Data: openinsider.com (SEC Form 4). Not investment advice.</footer>
</body></html>
"""


SELLS_PAGE = r"""
<!doctype html><html lang="en"><head>""" + HEAD + r"""
<style>
  table{width:100%;border-collapse:collapse;background:var(--panel);
        border:1px solid var(--line);border-radius:10px;overflow:hidden}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
  th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;background:#11161d}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tr:hover td{background:#1b222b}
  .tick{font-weight:700}
  .sell{color:var(--red);font-weight:700}
  .oe{color:var(--muted);font-size:11px}
</style>
<title>Big Sells</title></head><body>
""" + NAV + r"""
<div class="wrap">
  <div style="background:rgba(248,81,73,.07);border:1px solid rgba(248,81,73,.3);
              border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px">
    🔴 <b>Large insider sells</b> (≥ ${{ '{:,.0f}'.format(f.min_value) }}).
    Selling is a <b>weaker</b> signal than buying — insiders sell for taxes, diversification,
    and option exercises. <span class="oe">"Sale+OE" = sale tied to an option exercise (weakest).</span>
    Informational, <b>not financial advice</b>.
  </div>

  <form method="get">
    <label>Min sell $ <input type="number" name="min_value" value="{{ f.min_value|int }}" step="100000"></label>
    <label>Days back <input type="number" name="days" value="{{ f.days }}" min="1" max="365"></label>
    <button type="submit">Filter</button>
  </form>

  {% if sells %}
  <table>
    <thead><tr>
      <th class="num">Value</th><th>Ticker</th><th>Company</th><th>Insider</th><th>Title</th>
      <th class="num">ΔOwn</th><th class="num">Price</th><th>Type</th><th>Filed</th>
    </tr></thead>
    <tbody>
    {% for s in sells %}
      <tr>
        <td class="num sell">-${{ '{:,.0f}'.format(s.value or 0) }}</td>
        <td class="tick"><a href="http://openinsider.com/{{ s.ticker }}" target="_blank">{{ s.ticker }}</a></td>
        <td>{{ s.company }}</td>
        <td>{{ s.insider }}</td>
        <td class="muted">{{ s.title }}</td>
        <td class="num">{% if s.own_chg_pct is not none %}{{ s.own_chg_pct|int }}%{% else %}—{% endif %}</td>
        <td class="num">{{ '${:,.2f}'.format(s.price) if s.price else '—' }}</td>
        <td>{{ 'Sale+OE' if '+' in (s.trade_type or '') else 'Sale' }}</td>
        <td class="muted">{{ s.filing_date }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
    <div class="empty">No large insider sells in this window yet.</div>
  {% endif %}
</div>
<footer>Large insider sales from SEC Form 4 filings (openinsider.com). Selling is noisy —
  not financial advice, not a recommendation to sell. Do your own research.</footer>
</body></html>
"""


def run_web():
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, use_reloader=False)
