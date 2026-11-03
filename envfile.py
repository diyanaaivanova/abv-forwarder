"""Зарежда .env (ако го има) в os.environ. Файлът .env е в .gitignore.

Локално: настройките са в .env. В GitHub Actions: в Settings → Secrets.
Вече зададени променливи не се презаписват.
"""
import os
from pathlib import Path


def load(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_safely(fn):
    """Логовете в публично repo са публични: грешките се печатат без имейл адреси."""
    import re
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        text = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<имейл>", f"{type(e).__name__}: {e}")
        print("Грешка:", text, flush=True)
        raise SystemExit(1) from None
