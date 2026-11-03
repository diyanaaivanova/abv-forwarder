"""Препраща новите писма от пощата в abv.bg към друг адрес.

Пуска се по график (GitHub Actions). Чете INBOX по IMAP без да маркира писмата
като прочетени, препраща всяко ново писмо през SMTP на abv и записва номера
(UID) на последното обработено писмо в state.json.

При първото пускане само запомня докъде е стигнала пощата: старите писма
НЕ се препращат.
"""
import email
import email.policy
import email.utils
import imaplib
import json
import os
import smtplib
import ssl
import sys
from email.header import Header, decode_header, make_header
from pathlib import Path


import envfile  # noqa: E402
envfile.load()

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.abv.bg")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.abv.bg")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
USER = os.environ["ABV_USER"]
PASSWORD = os.environ["ABV_PASSWORD"]
FORWARD_TO = os.environ["FORWARD_TO"]
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "50"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
USE_SSL = os.environ.get("USE_SSL", "1") == "1"  # 0 само за локален тест

# Заглавки, които не бива да минават в препратеното писмо.
DROP_HEADERS = {
    "from", "to", "cc", "bcc", "reply-to", "sender", "return-path",
    "delivered-to", "dkim-signature", "domainkey-signature", "arc-seal",
    "arc-message-signature", "arc-authentication-results",
    "authentication-results", "received", "received-spf", "x-received",
    "message-id", "subject", "date", "list-unsubscribe",
    "list-unsubscribe-post",
}


def log(msg):
    print(msg, flush=True)


def decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def build_forward(raw):
    """Връща ново писмо със същото съдържание и прикачени файлове."""
    orig = email.message_from_bytes(raw, policy=email.policy.compat32)
    orig_from = decode(orig.get("From"))
    name, addr = email.utils.parseaddr(orig_from)
    subject = decode(orig.get("Subject")) or "(без тема)"
    # Ако писмото има Reply-To (напр. формата в Netlify), отговорът отива там.
    reply_to = decode(orig.get("Reply-To")) or orig_from

    for key in list(orig.keys()):
        if key.lower() in DROP_HEADERS:
            del orig[key]

    shown = name or addr or "непознат подател"
    orig["From"] = email.utils.formataddr((f"{shown} (през abv)", USER), charset="utf-8")
    orig["To"] = FORWARD_TO
    rname, raddr = email.utils.parseaddr(reply_to)
    if raddr:
        orig["Reply-To"] = email.utils.formataddr((rname, raddr), charset="utf-8")
    orig["Subject"] = Header(subject, "utf-8").encode()
    orig["Date"] = email.utils.formatdate(localtime=True)
    orig["Message-ID"] = email.utils.make_msgid(domain=USER.split("@")[-1])
    orig["X-Original-From"] = Header(orig_from, "utf-8").encode()
    orig["X-Forwarded-By"] = "abv-forwarder"
    return orig, addr


def main():
    state = load_state()
    ctx = ssl.create_default_context()

    imap = (imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
            if USE_SSL else imaplib.IMAP4(IMAP_HOST, IMAP_PORT))
    imap.login(USER, PASSWORD)
    typ, _ = imap.select("INBOX", readonly=True)  # readonly: не маркира като прочетени
    if typ != "OK":
        sys.exit("Не мога да отворя INBOX")

    validity = imap.response("UIDVALIDITY")[1][0].decode()
    typ, data = imap.uid("SEARCH", None, "ALL")
    uids = [int(u) for u in data[0].split()] if data and data[0] else []
    top = max(uids) if uids else 0

    if state.get("uidvalidity") != validity or "last_uid" not in state:
        # Първо пускане (или пощата е преномерирана): само запомняме докъде сме.
        log(f"Начална точка: UID {top}. Старите писма не се препращат.")
        save_state({"uidvalidity": validity, "last_uid": top})
        imap.logout()
        return

    last = int(state["last_uid"])
    new = sorted(u for u in uids if u > last)[:MAX_PER_RUN]
    if not new:
        log("Няма нови писма.")
        imap.logout()
        return

    own = USER.lower()
    fwd = FORWARD_TO.lower()
    smtp = (smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx)
            if USE_SSL else smtplib.SMTP(SMTP_HOST, SMTP_PORT))
    smtp.login(USER, PASSWORD)
    sent = 0
    try:
        for uid in new:
            typ, msg_data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
            if typ != "OK" or raw is None:
                log(f"UID {uid}: не може да се прочете, пропускам.")
                last = uid
                continue
            msg, sender = build_forward(raw)
            if sender.lower() in (own, fwd):
                # Не препращаме писма от самите нас (иначе може да стане кръг).
                log(f"UID {uid}: от нас самите, пропускам.")
            else:
                smtp.sendmail(USER, [FORWARD_TO], msg.as_bytes())
                sent += 1
                log(f"UID {uid}: препратено.")
            last = uid
            save_state({"uidvalidity": validity, "last_uid": last})
    finally:
        save_state({"uidvalidity": validity, "last_uid": last})
        smtp.quit()
        imap.logout()
    log(f"Готово: препратени {sent} от {len(new)}.")


if __name__ == "__main__":
    envfile.run_safely(main)
