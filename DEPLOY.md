# Deploying to the cloud (GitHub + Vercel + Supabase)

This makes the tracker **always-on and accessible from anywhere** — no laptop
required. Three free pieces:

| Piece | Role |
|-------|------|
| **Supabase** (Postgres) | stores the trades + market data |
| **GitHub Actions** | runs the scraper on a schedule (every 30 min) and writes to Supabase |
| **Vercel** | hosts the dashboard (read-only) at a public URL |

The same code runs locally on SQLite with zero setup — Postgres only kicks in
when `DATABASE_URL` is present. Nothing below changes how it runs on your Mac.

---

## 1. Push to GitHub

```bash
cd openinsider-tracker
git init && git add -A && git commit -m "Insider Buy Tracker"
gh repo create insider-tracker --private --source=. --push   # or create on github.com and: git remote add origin … && git push -u origin main
```

## 2. Create the Supabase database

1. Create a project at <https://supabase.com> (free tier is fine).
2. **Project Settings → Database → Connection string → "Connection pooling"**
   (Transaction mode, port `6543`). Copy that URI — it looks like:
   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   Use the **pooler** URL (not the direct `:5432` one) — it's the right choice
   for serverless (Vercel) and CI (Actions). The app disables prepared
   statements so it works through the pooler.

   *(Tables are created automatically on first connection — no SQL to run.)*

## 3. Deploy the dashboard to Vercel

1. <https://vercel.com> → **Add New → Project** → import the GitHub repo.
2. Framework preset: **Other** (the included `vercel.json` handles routing).
3. **Environment Variables** → add `DATABASE_URL` = your Supabase pooler URI.
4. **Deploy.** Your dashboard is live at `https://<project>.vercel.app`.

> Vercel only *reads* — it never scrapes. Cold starts are fine for a dashboard.

## 4. Turn on scheduled polling (GitHub Actions)

The workflow is already in `.github/workflows/poll.yml` (every 30 min).
In your repo: **Settings → Secrets and variables → Actions**, add:

**Secrets** (required):
- `DATABASE_URL` — same Supabase pooler URI

**Secrets** (only if you want email alerts):
- `OI_NOTIFY_EMAIL` = `true`
- `OI_SMTP_HOST`, `OI_SMTP_PORT`, `OI_SMTP_USER`, `OI_SMTP_PASS`
- `OI_ALERT_FROM`, `OI_ALERT_TO`
  *(Gmail: use an [App Password](https://myaccount.google.com/apppasswords), not your login.)*

**Variables** (optional threshold overrides):
- `OI_MIN_TRADE_VALUE` (default `1000000`)
- `OI_MIN_MARKET_CAP` (default `2000000000`)
- `OI_ALERT_MIN_SCORE` (default `70`)

Then **Actions → poll-insider-buys → Run workflow** to seed data immediately
(don't wait for the first scheduled run). Refresh your Vercel URL — opportunities
appear.

---

## Notes & gotchas

- **GitHub disables scheduled workflows after 60 days of repo inactivity.** Any
  push (or a manual run) re-arms it. Bump `__version__` in `insider/__init__.py`
  on each push so there's always a fresh commit.
- **Cron granularity:** GitHub's minimum is 5 min and scheduled runs can be
  delayed under load. 30 min is reliable and more than enough for Form 4 data.
- **Want truly continuous polling instead?** Point a small always-on host
  (Render/Railway/Fly) at `python run.py` with `DATABASE_URL` set — the built-in
  poller loop then does the scheduling and you can skip GitHub Actions.
- **Vercel module-not-found?** If a deploy can't import `config`/`insider`,
  add to `vercel.json`: `"functions": { "api/index.py": { "includeFiles": "insider/**" } }`.
- Desktop notifications are macOS-only and are off in the cloud — use email.
