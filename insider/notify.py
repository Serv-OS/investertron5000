"""Alert delivery: terminal, macOS desktop notification, and email.

Each channel fails soft — a broken SMTP config or a non-mac host never stops
the poller. Channels are toggled in config / .env.
"""
import smtplib
import subprocess
from email.mime.text import MIMEText
from typing import List

import config
from insider.marketcap import human_cap


def _line(sig: dict) -> str:
    mc = human_cap(sig.get("market_cap"))
    val = sig.get("value") or 0
    return (
        f"[{sig['score']:>3}] {sig['ticker']:<6} "
        f"${val:>12,.0f}  {mc:>8}  "
        f"{sig['insider']} ({sig['title']}) — {sig['company']}"
    )


def _terminal(signals: List[dict]) -> None:
    print("\n" + "=" * 78)
    print(f"  🟢  {len(signals)} new insider-BUY signal(s) "
          f"(score ≥ {config.ALERT_MIN_SCORE})")
    print("=" * 78)
    for sig in signals:
        print("  " + _line(sig))
    print("=" * 78 + "\n", flush=True)


def _desktop(signals: List[dict]) -> None:
    top = signals[0]
    title = f"Insider BUY · {top['ticker']} (score {top['score']})"
    extra = f" +{len(signals) - 1} more" if len(signals) > 1 else ""
    body = (f"{top['company']} — {top['insider']} ({top['title']}) "
            f"${(top.get('value') or 0):,.0f}{extra}")
    # Escape double quotes for AppleScript.
    title = title.replace('"', "'")
    body = body.replace('"', "'")
    script = f'display notification "{body}" with title "{title}" sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass  # not macOS / osascript missing


def _email(signals: List[dict]) -> None:
    if not (config.SMTP_HOST and config.ALERT_TO):
        return
    rows = "\n".join(
        f"  {_line(s)}\n      breakdown: {s.get('breakdown')}"
        for s in signals
    )
    text = (
        f"{len(signals)} new insider-purchase signal(s) at or above "
        f"score {config.ALERT_MIN_SCORE} and market cap "
        f"{human_cap(config.MIN_MARKET_CAP)}:\n\n{rows}\n\n"
        f"Dashboard: http://{config.WEB_HOST}:{config.WEB_PORT}/\n"
    )
    msg = MIMEText(text)
    msg["Subject"] = f"📈 {len(signals)} insider-buy signal(s) — top {signals[0]['ticker']}"
    msg["From"] = config.ALERT_FROM
    msg["To"] = config.ALERT_TO

    recipients = [r.strip() for r in config.ALERT_TO.split(",") if r.strip()]
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASS)
            smtp.sendmail(config.ALERT_FROM, recipients, msg.as_string())
        print(f"  ✉  emailed {len(recipients)} recipient(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ email failed: {exc}")


def send(signals: List[dict]) -> None:
    """Fan a batch of new strong signals out to all enabled channels."""
    if not signals:
        return
    signals = sorted(signals, key=lambda s: s["score"], reverse=True)
    if config.NOTIFY_TERMINAL:
        _terminal(signals)
    if config.NOTIFY_DESKTOP:
        _desktop(signals)
    if config.NOTIFY_EMAIL:
        _email(signals)
