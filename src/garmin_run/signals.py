"""Сигналы усталости по правилам тренера (.claude/skills/run-coach/SKILL.md).

Считаются по дневным файлам data/daily и активностям — те же пороги, что тренер
применяет в быстрой проверке «на сегодня».
"""

from __future__ import annotations

import datetime as dt
from statistics import mean
from typing import Any

from .status import dig

BAD_HRV = ("LOW", "UNBALANCED", "POOR")


def _hrv_bad(d: dict[str, Any] | None) -> bool:
    if not d:
        return False
    s = dig(d, "hrv", "hrvSummary") or {}
    low = dig(s, "baseline", "balancedLow")
    night = s.get("lastNightAvg")
    return s.get("status") in BAD_HRV or (low is not None and night is not None and night < low)


def fatigue_signals(
    day: dt.date,
    daily: dict[dt.date, dict[str, Any]],
    activities: list[dict[str, Any]],
) -> list[str]:
    """Список сработавших сигналов на утро дня `day`, человеческими словами."""
    d = daily.get(day)
    if not d:
        return []
    out = []

    if _hrv_bad(d) and _hrv_bad(daily.get(day - dt.timedelta(days=1))):
        status = dig(d, "hrv", "hrvSummary", "status")
        out.append(f"HRV {status if status in BAD_HRV else 'ниже нормы'} две ночи подряд")

    sleep_s = dig(d, "sleep", "dailySleepDTO", "sleepTimeSeconds")
    score = dig(d, "sleep", "dailySleepDTO", "sleepScores", "overall", "value")
    if sleep_s is not None and sleep_s < 6 * 3600:
        out.append(f"сон {sleep_s / 3600:.1f} ч")
    elif score is not None and score < 60:
        out.append(f"оценка сна {score}")

    rhr = dig(d, "summary", "restingHeartRate")
    week = [dig(daily.get(day - dt.timedelta(days=i)), "summary", "restingHeartRate") for i in range(1, 8)]
    week = [x for x in week if x]
    if rhr and len(week) >= 3 and rhr >= mean(week) + 5:
        out.append(f"пульс покоя {rhr} при среднем {mean(week):.0f}")

    readiness = dig(d, "readiness", 0, "score")
    bb = dig(d, "summary", "bodyBatteryAtWakeTime")
    if readiness is not None and readiness < 40:
        out.append(f"готовность {readiness}")
    elif bb is not None and bb < 35:
        out.append(f"Body Battery утром {bb}")

    yesterday = (day - dt.timedelta(days=1)).isoformat()
    for a in activities:
        if a.get("startTimeLocal", "")[:10] != yesterday:
            continue
        aer, anaer = a.get("aerobicTrainingEffect") or 0, a.get("anaerobicTrainingEffect") or 0
        if aer >= 4.0 or anaer >= 3.0:
            out.append(f"вчера TE {aer:.1f}/{anaer:.1f}")
            break
    return out
