"""Препраща новите писма от пощата в abv.bg към друг адрес.

Пуска се по график (GitHub Actions). Чете пощата по POP3 (без да трие писмата),
препраща всяко ново писмо през SMTP на abv и пази в state.json кои писма
вече е видял (като кратки хешове, не реалните идентификатори).

При първото пускане само запомня докъде е стигнала пощата: старите писма
НЕ се препращат.
"""
import email
import email.policy
import email.utils
import json
import os
import smtplib
import ssl
from email.header import Header, decode_header, make_header
from pathlib import Path


import envfile  # noqa: E402
envfile.load()
import abvpop  # noqa: E402

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

    pop = abvpop.connect(USER, PASSWORD)
    try:
        msgs = abvpop.listing(pop)
        keys_now = {k for _, k in msgs}
        seen = set(state.get("seen", []))

        if not state.get("init"):
            # Първо пускане: само запомняме какво има. Старите писма не се препращат.
            save_state({"init": True, "seen": sorted(keys_now)})
            log(f"Начална точка: {len(msgs)} писма в пощата. Старите не се препращат.")
            return

        new = [(n, k) for n, k in msgs if k not in seen][:MAX_PER_RUN]
        if not new:
            log("Няма нови писма.")
            # пазим само ключове, които още ги има на сървъра
            if seen - keys_now:
                save_state({"init": True, "seen": sorted(seen & keys_now)})
            return

        own = USER.lower()
        fwd = FORWARD_TO.lower()
        smtp = (smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx)
                if USE_SSL else smtplib.SMTP(SMTP_HOST, SMTP_PORT))
        smtp.login(USER, PASSWORD)
        sent = 0
        try:
            for n, key in new:
                msg, sender = build_forward(abvpop.retr(pop, n))
                if sender.lower() in (own, fwd):
                    # Не препращаме писма от самите нас (иначе може да стане кръг).
                    log(f"Писмо {n}: от нас самите, пропускам.")
                else:
                    smtp.sendmail(USER, [FORWARD_TO], msg.as_bytes())
                    sent += 1
                    log(f"Писмо {n}: препратено.")
                seen.add(key)
        finally:
            save_state({"init": True, "seen": sorted(seen & keys_now)})
            smtp.quit()
        log(f"Готово: препратени {sent} от {len(new)}.")
    finally:
        pop.quit()


if __name__ == "__main__":
    envfile.run_safely(main)
