"""Вход в Garmin Connect через неофициальную библиотеку garminconnect.

Пароль не хранится: после первого входа библиотека сохраняет токены в
~/.garminconnect (вне репозитория) и дальше работает по ним.
"""

from __future__ import annotations

import getpass
import os

from garminconnect import Garmin
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .config import TOKENSTORE


class NotLoggedIn(RuntimeError):
    pass


def login_interactive() -> Garmin:
    email = os.environ.get("GARMIN_EMAIL") or input("Email Garmin: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Пароль Garmin: ")
    TOKENSTORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    api = Garmin(email, password, prompt_mfa=lambda: input("Код MFA: ").strip())
    try:
        api.login(str(TOKENSTORE))
    except GarminConnectAuthenticationError as e:
        raise NotLoggedIn(f"Garmin не принял email или пароль ({e}). Проверь вход на connect.garmin.com") from e
    except (GarminConnectTooManyRequestsError, GarminConnectConnectionError) as e:
        raise NotLoggedIn(
            f"Garmin не пустил ({e}).\n"
            "Обычно это лимит попыток входа с твоего IP: подожди час-другой и не повторяй вход подряд — "
            "каждая попытка продлевает блокировку. Если включён VPN — попробуй без него. "
            "Заодно проверь пароль на connect.garmin.com."
        ) from e
    return api


def connect() -> Garmin:
    """Клиент по сохранённым токенам, без пароля."""
    if not TOKENSTORE.exists() or not any(TOKENSTORE.iterdir()):
        raise NotLoggedIn("нет токенов Garmin — сначала `uv run garmin-run login`")
    api = Garmin()
    try:
        api.login(str(TOKENSTORE))
    except Exception as e:
        raise NotLoggedIn(f"токены Garmin не подошли ({e}) — повтори `uv run garmin-run login`") from e
    return api
