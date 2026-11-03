"""Достъп до пощата в abv.bg по POP3 (abv няма IMAP)."""
import hashlib
import os
import poplib
import ssl

POP_HOST = os.environ.get("POP_HOST", "pop3.abv.bg")
POP_PORT = int(os.environ.get("POP_PORT", "995"))
USE_SSL = os.environ.get("USE_SSL", "1") == "1"  # 0 само за локален тест


def connect(user, password):
    pop = (poplib.POP3_SSL(POP_HOST, POP_PORT, context=ssl.create_default_context(), timeout=60)
           if USE_SSL else poplib.POP3(POP_HOST, POP_PORT, timeout=60))
    pop.user(user)
    pop.pass_(password)
    return pop


def listing(pop):
    """[(номер, кратък_ключ)] в реда на сървъра (най-новите са накрая).

    Ключът е хеш на UIDL, за да не пазим реалните идентификатори.
    """
    _, lines, _ = pop.uidl()
    out = []
    for line in lines:
        num, uid = line.decode("ascii", "replace").split(" ", 1)
        out.append((int(num), hashlib.sha1(uid.encode()).hexdigest()[:12]))
    return out


def retr(pop, num):
    _, lines, _ = pop.retr(num)
    return b"\r\n".join(lines) + b"\r\n"


def headers(pop, num):
    _, lines, _ = pop.top(num, 0)
    return b"\r\n".join(lines) + b"\r\n"
