"""Слага писма директно в кутията на Gmail / Google Workspace по IMAP.

Не се изпраща нищо: писмото се добавя в INBOX като непрочетено, с оригиналния
подател, дата и прикачени файлове. Нужни са GMAIL_USER и GMAIL_APP_PASSWORD.
"""
import email.utils
import imaplib
import os
import ssl
import time

HOST = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
PORT = int(os.environ.get("GMAIL_IMAP_PORT", "993"))
USE_SSL = os.environ.get("USE_SSL", "1") == "1"  # 0 само за локален тест


def connect(user, password):
    imap = (imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ssl.create_default_context())
            if USE_SSL else imaplib.IMAP4(HOST, PORT))
    imap.login(user, password)
    return imap


def put(imap, raw, date_header=None):
    """Добавя писмото в INBOX като непрочетено."""
    when = time.time()
    if date_header:
        try:
            when = email.utils.parsedate_to_datetime(date_header).timestamp()
        except Exception:
            pass
    typ, data = imap.append("INBOX", None, imaplib.Time2Internaldate(when), raw)
    if typ != "OK":
        raise RuntimeError(f"IMAP APPEND: {typ}")
