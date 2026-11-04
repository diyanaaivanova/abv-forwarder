"""Изпраща писмото за новия имейл и сайт до списък получатели, по няколко на ден.

Списъкът НЕ е в repo: идва от тайния ключ CAMPAIGN_LIST (един имейл на ред,
по реда, в който да се изпращат). В campaign_state.json се пазят само
съкратени хешове на адресите, на които вече е изпратено, без самите адреси.

Всяко писмо тръгва поотделно (никой не вижда другите получатели) от GMAIL_USER
през Gmail SMTP с паролата за приложения. Копие остава в „Изпратени“ в Gmail.
След всяко пускане в кутията на GMAIL_USER се слага кратък отчет.

Настройки (в .env локално, в GitHub Secrets в Actions):
  GMAIL_USER, GMAIL_APP_PASSWORD, CAMPAIGN_LIST
  CAMPAIGN_PER_DAY (по подразбиране 25), CAMPAIGN_DRY_RUN=1 за проба без изпращане
"""
import hashlib
import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, formatdate
from pathlib import Path

import envfile
import gmailbox

STATE = Path("campaign_state.json")
TEMPLATE = Path("campaign/pismo.html")
SUBJECT = "T&P Sped: нов имейл и нов сайт"
FROM_NAME = "T&P Sped"
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")

TEXT = """Здравейте,

Пиша ви, за да ви съобщя две промени при нас:

Нов имейл: {email}
Моля, използвайте го за запитвания и документи. Старият адрес в abv.bg ще работи още известно време.

Нов сайт: https://www.tpsped.com
Там можете да изпратите запитване за превоз директно през формата. Отговаряме обикновено още същия ден.

Както досега, организираме превози в цяла Европа, с редовни линии в двете посоки: България ⇄ Чехия, Германия, Полша, Сърбия и Унгария. Возим пълни товари, палети и групажи, машини, ADR и хладилни товари, както и пластмасови отпадъци за рециклиране.

Благодарим ви за досегашната работа и ще се радваме да ви бъдем полезни и занапред.

Поздрави,
Тихомир Пенчев
T&P Sped
+359 885 996 750 · +359 879 174 383
{email} · www.tpsped.com
"""


def h(addr):
    return hashlib.sha1(addr.lower().encode()).hexdigest()[:12]


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"sent": [], "failed": []}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def recipients():
    seen, out = set(), []
    for line in os.environ.get("CAMPAIGN_LIST", "").replace(",", "\n").splitlines():
        a = line.strip().lower()
        if a and EMAIL_RE.match(a) and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def build(to, sender):
    html = TEMPLATE.read_text(encoding="utf-8").replace("{{EMAIL}}", sender)
    m = EmailMessage()
    m["From"] = formataddr((FROM_NAME, sender))
    m["To"] = to
    m["Subject"] = SUBJECT
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = make_msgid(domain=sender.split("@")[1])
    m["List-Unsubscribe"] = f"<mailto:{sender}?subject=unsubscribe>"
    m.set_content(TEXT.format(email=sender))
    m.add_alternative(html, subtype="html")
    return m


def report(sender, password, sent, failed, left):
    lines = [f"Изпратени днес: {len(sent)}"] + [f"  ✓ {a}" for a in sent]
    if failed:
        lines += [f"Неуспешни: {len(failed)}"] + [f"  ✗ {a} ({e})" for a, e in failed]
    lines += ["", f"Остават в списъка: {left}"]
    if left == 0:
        lines.append("Списъкът свърши. Добави нови адреси в CAMPAIGN_LIST, ако искаш да продължи.")
    m = EmailMessage()
    m["From"] = formataddr(("Бот за писмата", sender))
    m["To"] = sender
    m["Subject"] = f"Отчет: писмо за новия имейл ({len(sent)} изпратени, остават {left})"
    m["Date"] = formatdate(localtime=True)
    m.set_content("\n".join(lines))
    imap = gmailbox.connect(sender, password)
    try:
        gmailbox.put(imap, m.as_bytes())
    finally:
        imap.logout()


def main():
    envfile.load()
    sender = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    per_day = int(os.environ.get("CAMPAIGN_PER_DAY") or 25)
    dry = os.environ.get("CAMPAIGN_DRY_RUN") == "1"
    if not (sender and password):
        print("Липсват GMAIL_USER / GMAIL_APP_PASSWORD.")
        return

    state = load_state()
    done = set(state["sent"]) | {f["h"] for f in state["failed"]}
    todo = [a for a in recipients() if h(a) not in done]
    batch = todo[:per_day]
    print(f"В списъка: {len(recipients())}, чакат: {len(todo)}, днес: {len(batch)}", flush=True)
    if not batch:
        return

    sent, failed = [], []
    if dry:
        for a in batch:
            build(a, sender)
        print("Проба: нищо не е изпратено.")
        return

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(sender, password)
        for i, a in enumerate(batch, 1):
            try:
                s.send_message(build(a, sender))
                sent.append(a)
                state["sent"].append(h(a))
                print(f"{i}/{len(batch)}: изпратено", flush=True)
            except smtplib.SMTPRecipientsRefused as e:
                err = str(list(e.recipients.values())[0][0])
                failed.append((a, err))
                state["failed"].append({"h": h(a), "code": err})
                print(f"{i}/{len(batch)}: отказан адрес ({err})", flush=True)
            save_state(state)
            time.sleep(20)  # разстояние между писмата

    left = len(todo) - len(sent) - len(failed)
    state["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)
    report(sender, password, sent, failed, left)
    print(f"Готово: {len(sent)} изпратени, {len(failed)} неуспешни, остават {left}.")


if __name__ == "__main__":
    envfile.run_safely(main)
