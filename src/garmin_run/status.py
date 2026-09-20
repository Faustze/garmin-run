"""Сводка в markdown из data/ и state/: план против факта, восстановление, недели.

Читает только локальные файлы — работает без сети после pull.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import DATA
from .push import load_state

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
RUN_TYPES = ("running", "treadmill_running", "trail_running", "track_running", "indoor_running")


def dig(obj: Any, *path: Any) -> Any:
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return None
    return obj


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pace(speed: float | None) -> str:
    if not speed:
        return "—"
    s = round(1000 / speed)
    return f"{s // 60}:{s % 60:02d}"


def _hms(seconds: float | None) -> str:
    if not seconds:
        return "—"
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _v(x: Any, fmt: str = "{}") -> str:
    return "—" if x is None or x == "" else fmt.format(x)


def load_activities(since: dt.date) -> list[dict[str, Any]]:
    out = []
    for p in sorted((DATA / "activities").glob("*.json")):
        if p.name[:10] >= since.isoformat():
            out.append(_read(p))
    return sorted(out, key=lambda a: a.get("startTimeLocal", ""))


def is_run(a: dict[str, Any]) -> bool:
    return dig(a, "activityType", "typeKey") in RUN_TYPES


def wellness_rows(since: dt.date) -> list[str]:
    rows = [
        "| дата | сон ч | сон балл | HRV ночь | HRV 7д | HRV статус | пульс покоя | BB утро | BB макс | готовность | стресс |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in sorted((DATA / "daily").glob("*.json")):
        if p.stem < since.isoformat():
            continue
        d = _read(p)
        sleep_s = dig(d, "sleep", "dailySleepDTO", "sleepTimeSeconds")
        readiness = dig(d, "readiness", 0) or {}
        rows.append("| " + " | ".join([
            p.stem[5:],
            _v(sleep_s and sleep_s / 3600, "{:.1f}"),
            _v(dig(d, "sleep", "dailySleepDTO", "sleepScores", "overall", "value")),
            _v(dig(d, "hrv", "hrvSummary", "lastNightAvg")),
            _v(dig(d, "hrv", "hrvSummary", "weeklyAvg")),
            _v(dig(d, "hrv", "hrvSummary", "status")),
            _v(dig(d, "summary", "restingHeartRate")),
            _v(dig(d, "summary", "bodyBatteryAtWakeTime")),
            _v(dig(d, "summary", "bodyBatteryHighestValue")),
            _v(readiness.get("score") if isinstance(readiness, dict) else None),
            _v(dig(d, "summary", "averageStressLevel")),
        ]) + " |")
    return rows


def activity_rows(activities: list[dict[str, Any]], state: dict[str, Any], since: dt.date, until: dt.date) -> list[str]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in activities:
        by_day[a.get("startTimeLocal", "")[:10]].append(a)
    rows = [
        "| дата | план | факт | км | время | темп | пульс ср/макс | TE аэр/анаэр | нагрузка |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    d = since
    while d <= until:
        ds = d.isoformat()
        plan = state.get(ds, {}).get("name", "")
        acts = by_day.get(ds) or [None]
        for a in acts:
            if a is None:
                if plan:
                    rows.append(f"| {ds[5:]} {WEEKDAYS[d.weekday()]} | {plan} | {'—' if d < dt.date.today() else 'впереди'} | | | | | | |")
                continue
            rows.append("| " + " | ".join([
                f"{ds[5:]} {WEEKDAYS[d.weekday()]}",
                plan or "—",
                str(a.get("activityName", "")),
                _v(a.get("distance") and a["distance"] / 1000, "{:.2f}"),
                _hms(a.get("duration")),
                _pace(a.get("averageSpeed")) if is_run(a) else "—",
                f"{_v(a.get('averageHR'), '{:.0f}')}/{_v(a.get('maxHR'), '{:.0f}')}",
                f"{_v(a.get('aerobicTrainingEffect'), '{:.1f}')}/{_v(a.get('anaerobicTrainingEffect'), '{:.1f}')}",
                _v(a.get("activityTrainingLoad"), "{:.0f}"),
            ]) + " |")
        d += dt.timedelta(days=1)
    return rows


def week_rows(activities: list[dict[str, Any]]) -> list[str]:
    weeks: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for a in activities:
        if not is_run(a):
            continue
        day = dt.date.fromisoformat(a["startTimeLocal"][:10])
        y, w, _ = day.isocalendar()
        km = (a.get("distance") or 0) / 1000
        row = weeks[f"{y}-W{w:02d}"]
        row[0] += 1
        row[1] += km
        row[2] = max(row[2], km)
        row[3] += (a.get("duration") or 0) / 3600
    rows = ["| неделя | пробежек | км | длительная км | часов |", "|---|---|---|---|---|"]
    for wk, (n, km, longest, hours) in sorted(weeks.items()):
        rows.append(f"| {wk} | {n:.0f} | {km:.1f} | {longest:.1f} | {hours:.1f} |")
    return rows


def garmin_state_rows() -> list[str]:
    path = DATA / "status.json"
    if not path.exists():
        return ["нет data/status.json — запусти pull"]
    s = _read(path)
    ts = dig(s, "training_status", "mostRecentTrainingStatus", "latestTrainingStatusData") or {}
    dev = next(iter(ts.values()), {}) if isinstance(ts, dict) else {}
    acute = dev.get("acuteTrainingLoadDTO") or {}
    vo2 = dig(s, "training_status", "mostRecentVO2Max", "generic", "vo2MaxPreciseValue") or dig(
        s, "max_metrics", 0, "generic", "vo2MaxPreciseValue")
    return [
        f"- на {s.get('date')}: статус «{_v(dev.get('trainingStatusFeedbackPhrase'))}», VO2max {_v(vo2)}",
        f"- острая нагрузка {_v(acute.get('dailyTrainingLoadAcute'))}, хроническая {_v(acute.get('dailyTrainingLoadChronic'))},"
        f" ACWR {_v(acute.get('dailyAcuteChronicWorkloadRatio'))} ({_v(acute.get('acwrStatus'))})",
    ]


def render(days: int, ahead: int = 7) -> str:
    today = dt.date.today()
    since = today - dt.timedelta(days=days - 1)
    state = load_state()
    activities = load_activities(since - dt.timedelta(days=7 * 8))
    recent = [a for a in activities if a.get("startTimeLocal", "")[:10] >= since.isoformat()]
    out = [
        f"# Сводка на {today} ({days} дн. назад, {ahead} дн. вперёд)",
        "", "## План и факт", *activity_rows(recent, state, since, today + dt.timedelta(days=ahead)),
        "", "## Восстановление", *wellness_rows(since),
        "", "## Недели (бег)", *week_rows(activities),
        "", "## Garmin", *garmin_state_rows(),
    ]
    return "\n".join(out)
