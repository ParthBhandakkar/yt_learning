"""Email notifications (SMTP over SSL). Non-blocking-ish: failures are logged, never raised."""
from __future__ import annotations

import smtplib
import ssl
from email.mime.text import MIMEText

from config import CONFIG
from logging_setup import get_engine_logger

log = get_engine_logger()


def send_email(subject: str, body: str) -> bool:
    if not CONFIG.email_ready():
        log.info(f"[email skipped — SMTP not configured] {subject}")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = CONFIG.email_from or CONFIG.smtp_user
        msg["To"] = CONFIG.email_to
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(CONFIG.smtp_host, CONFIG.smtp_port, context=ctx, timeout=20) as s:
            s.login(CONFIG.smtp_user, CONFIG.smtp_pass)
            s.sendmail(msg["From"], [a.strip() for a in CONFIG.email_to.split(",")], msg.as_string())
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Email failed ({subject}): {e}")
        return False


def trade_email(t: dict) -> None:
    dry = "[DRY RUN] " if t.get("dry_run") else ""
    subject = f"{dry}Strategy95 {t['direction'].upper()} {t['symbol']} @ {t['entry']:.5f}"
    body = (
        f"{dry}Strategy 95 trade executed\n"
        f"--------------------------------\n"
        f"Symbol      : {t['symbol']}\n"
        f"Direction   : {t['direction'].upper()}\n"
        f"Entry       : {t['entry']:.5f}\n"
        f"Stop Loss   : {t['sl']:.5f}\n"
        f"Take Profit : {t['tp']:.5f}  (final {t.get('final_r', 1.5)}R)\n"
        f"Lots        : {t.get('lots', 0)}\n"
        f"Risk (pips) : {t.get('risk_pips', 0):.1f}\n"
        f"Est. margin : Rs {t.get('margin', 0):.0f}  (1:{t.get('leverage', 2000):.0f})\n"
        f"Time (UTC)  : {t.get('time_utc', '')}\n"
        f"\nSetup:\n"
        f"  4H sweep of {t.get('swept_level', '')} ({t.get('sweep_dir', '')})\n"
        f"  1H displacement MSS @ {t.get('mss_price', '')}\n"
        f"  15M order block (OTE)\n"
        f"  5M tap entry\n"
        f"\nManagement: close 50% at +{t.get('partial_r', 0.5)}R -> stop to breakeven -> rest to +{t.get('final_r', 1.5)}R\n"
    )
    send_email(subject, body)
