"""Веднъж на ден праща списък с писмата от последните 24 часа.

Чете abv (винаги) и пощата в Gmail (ако има GMAIL_USER и
GMAIL_APP_PASSWORD). Не маркира нищо като прочетено. Изпраща едно писмо
с подател, тема и първите редове на всяко писмо.
"""
import email
import email.policy
import email.utils
import html
import imaplib
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo


import envfile  # noqa: E402
envfile.load()

TZ = ZoneInfo("Europe/Sofia")
ABV_USER = os.environ["ABV_USER"]
ABV_PASSWORD = os.environ["ABV_PASSWORD"]
DIGEST_TO = os.environ.get("DIGEST_TO") or os.environ["FORWARD_TO"]
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
HOURS = int(os.environ.get("DIGEST_HOURS", "24"))
SNIPPET = 220
USE_SSL = os.environ.get("USE_SSL", "1") == "1"  # 0 само за локален тест
IMAP_ABV = os.environ.get("IMAP_HOST", "imap.abv.bg")
IMAP_GMAIL = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.abv.bg")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

FORM_SENDER = "formresponses@netlify.com"


def text_of(msg):
    """Първата текстова част, без HTML тагове и излишни интервали."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        body = part.get_content()
    except Exception:
        return ""
    if part.get_content_type() == "text/html":
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
        body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    # махаме цитирани отговори
    lines = [l for l in body.splitlines() if not l.lstrip().startswith(">")]
    body = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return body[:SNIPPET] + ("…" if len(body) > SNIPPET else "")


def attachments(msg):
    return [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]


def collect(host, user, password, label, since):
    imap = (imaplib.IMAP4_SSL(host, IMAP_PORT, ssl_context=ssl.create_default_context())
            if USE_SSL else imaplib.IMAP4(host, IMAP_PORT))
    imap.login(user, password)
    imap.select("INBOX", readonly=True)
    day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
    typ, data = imap.uid("SEARCH", None, "SINCE", day)
    uids = data[0].split() if data and data[0] else []
    items = []
    for uid in uids:
        typ, msg_data = imap.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
        raw = next((p for p in msg_data if isinstance(p, tuple)), None)
        if raw is None:
            continue
        when = imaplib.Internaldate2tuple(raw[0])  # местно време на машината
        received = datetime.fromtimestamp(time.mktime(when), tz=timezone.utc) if when else None
        msg = email.message_from_bytes(raw[1], policy=email.policy.default)
        if received and received < since:
            continue
        if msg.get("X-Forwarded-By") == "abv-forwarder":
            continue  # вече е в списъка от abv
        name, addr = email.utils.parseaddr(str(msg.get("From", "")))
        items.append({
            "box": label,
            "time": received.astimezone(TZ) if received else None,
            "from": name or addr or "?",
            "addr": addr.lower(),
            "subject": str(msg.get("Subject", "")) or "(без тема)",
            "text": text_of(msg),
            "files": attachments(msg),
        })
    imap.logout()
    return items


def render(items, since):
    now = datetime.now(TZ)
    forms = [i for i in items if i["addr"] == FORM_SENDER]
    rest = [i for i in items if i["addr"] != FORM_SENDER]

    def row(i):
        t = i["time"].strftime("%d.%m %H:%M") if i["time"] else ""
        files = f"<div style='color:#555;font-size:12px'>📎 {html.escape(', '.join(i['files']))}</div>" if i["files"] else ""
        return (
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e3e8ef;white-space:nowrap;color:#555;vertical-align:top'>{t}<br>"
            f"<span style='font-size:11px'>{html.escape(i['box'])}</span></td>"
            f"<td style='padding:8px;border-bottom:1px solid #e3e8ef;vertical-align:top'>"
            f"<b>{html.escape(i['from'])}</b><br>{html.escape(i['subject'])}"
            f"<div style='color:#444;font-size:13px;margin-top:3px'>{html.escape(i['text'])}</div>{files}</td>"
            "</tr>"
        )

    def table(title, rows):
        if not rows:
            return ""
        rows = sorted(rows, key=lambda i: i["time"] or now)
        return (f"<h3 style='color:#12508f;margin:18px 0 6px'>{title} ({len(rows)})</h3>"
                "<table style='border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px'>"
                + "".join(row(i) for i in rows) + "</table>")

    head = (f"<p style='font-family:Arial,sans-serif'>Писма от {since.astimezone(TZ):%d.%m %H:%M} "
            f"до {now:%d.%m %H:%M}: <b>{len(items)}</b>.</p>")
    if not items:
        body = head + "<p style='font-family:Arial,sans-serif'>Няма нови писма.</p>"
    else:
        body = head + table("Запитвания от сайта", forms) + table("Други писма", rest)

    plain = [f"Писма за последните {HOURS} ч.: {len(items)}"]
    for i in sorted(items, key=lambda i: i["time"] or now):
        t = i["time"].strftime("%d.%m %H:%M") if i["time"] else ""
        plain.append(f"\n{t} [{i['box']}] {i['from']} — {i['subject']}\n  {i['text']}")
    return body, "\n".join(plain)


def main():
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS)
    items = collect(IMAP_ABV, ABV_USER, ABV_PASSWORD, "abv", since)
    if GMAIL_USER and GMAIL_PASSWORD:
        items += collect(IMAP_GMAIL, GMAIL_USER, GMAIL_PASSWORD, "office", since)

    html_body, plain = render(items, since)
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr(("Дневен преглед на пощата", ABV_USER), charset="utf-8")
    msg["To"] = DIGEST_TO
    msg["Subject"] = f"Поща за {datetime.now(TZ):%d.%m}: {len(items)} писма"
    msg.set_content(plain)
    msg.add_alternative(f"<div style='max-width:720px'>{html_body}</div>", subtype="html")

    smtp = (smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context())
            if USE_SSL else smtplib.SMTP(SMTP_HOST, SMTP_PORT))
    smtp.login(ABV_USER, ABV_PASSWORD)
    smtp.send_message(msg)
    smtp.quit()
    print(f"Изпратен преглед: {len(items)} писма.")


if __name__ == "__main__":
    envfile.run_safely(main)
