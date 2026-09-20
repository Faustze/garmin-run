"""Выгрузка из Garmin Connect в data/ (в git не попадает).

data/activities/<дата>_<id>.json — сводка активности
data/daily/<дата>.json          — сон, HRV, готовность, пульс покоя, Body Battery
data/status.json                — статус тренированности, нагрузка, VO2max на сегодня
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

from .config import DATA

ACTIVITIES = DATA / "activities"
DAILY = DATA / "daily"

# Свежие дни перекачиваем: Garmin досчитывает сон и HRV после синхронизации.
REFRESH_DAYS = 2


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _safe(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as e:  # у части эндпоинтов бывают пустые дни и 404
        return {"_error": str(e)}


def pull_activities(api: Any, start: dt.date, end: dt.date) -> int:
    items = api.get_activities_by_date(start.isoformat(), end.isoformat()) or []
    for a in items:
        day = str(a.get("startTimeLocal", ""))[:10] or "unknown"
        _write(ACTIVITIES / f"{day}_{a['activityId']}.json", a)
    return len(items)


def pull_daily(api: Any, start: dt.date, end: dt.date, today: dt.date) -> int:
    count = 0
    d = start
    while d <= end:
        path = DAILY / f"{d.isoformat()}.json"
        if not path.exists() or (today - d).days < REFRESH_DAYS:
            ds = d.isoformat()
            _write(path, {
                "date": ds,
                "sleep": _safe(lambda: api.get_sleep_data(ds)),
                "hrv": _safe(lambda: api.get_hrv_data(ds)),
                "readiness": _safe(lambda: api.get_training_readiness(ds)),
                "summary": _safe(lambda: api.get_user_summary(ds)),
            })
            count += 1
        d += dt.timedelta(days=1)
    return count


def pull_status(api: Any, today: dt.date) -> None:
    ds = today.isoformat()
    _write(DATA / "status.json", {
        "date": ds,
        "training_status": _safe(lambda: api.get_training_status(ds)),
        "max_metrics": _safe(lambda: api.get_max_metrics(ds)),
        "hr_zones": _safe(api.get_heart_rate_zones),
    })


def pull(api: Any, days: int, activities_since: dt.date | None = None) -> None:
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    n = pull_activities(api, activities_since or start, today)
    print(f"активностей: {n}")
    print(f"дней здоровья обновлено: {pull_daily(api, start, today, today)}")
    pull_status(api, today)
    print(f"данные в {DATA}")
