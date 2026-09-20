"""Недельный план: plan/weeks/YYYY-Www.yaml.

    week: 2026-W39
    focus: восстановить лёгкий бег по пульсу
    days:
      2026-09-21: easy-45                     # шаблон из workouts/easy-45.yaml
      2026-09-22: rest
      2026-09-27: {use: long-16k, lock: true} # lock — тренер не трогает
      2026-09-25:                             # тренировка прямо в плане
        name: Лёгкий 50 мин
        steps:
          - run: 50min @ easy
    changes:                                  # журнал правок с причинами
      - {date: 2026-09-23, from: threshold-3x8, to: easy-40, why: HRV ниже нормы 2 дня}
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import WEEKS, WORKOUTS, load_yaml
from .dsl import PlanError

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Planned:
    day: dt.date
    ref: str | None  # имя шаблона, если тренировка из библиотеки
    spec: dict[str, Any]
    lock: bool = False


@dataclass
class Week:
    id: str
    start: dt.date
    end: dt.date
    focus: str
    days: list[Planned]
    path: Path

    def contains(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


def week_bounds(week_id: str) -> tuple[dt.date, dt.date]:
    m = _WEEK_RE.match(week_id)
    if not m:
        raise PlanError(f"неделя должна быть в виде 2026-W39, а не {week_id!r}")
    start = dt.date.fromisocalendar(int(m[1]), int(m[2]), 1)
    return start, start + dt.timedelta(days=6)


def week_id_for(day: dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def week_path(week_id: str) -> Path:
    return WEEKS / f"{week_id}.yaml"


def load_template(ref: str, workouts_dir: Path = WORKOUTS) -> dict[str, Any]:
    if not _REF_RE.match(ref):
        raise PlanError(f"имя шаблона {ref!r}: только a-z, 0-9 и дефис")
    path = workouts_dir / f"{ref}.yaml"
    if not path.exists():
        raise PlanError(f"нет шаблона {path.name} в {workouts_dir}")
    return load_yaml(path)


def _as_date(key: Any) -> dt.date:
    if isinstance(key, dt.date):
        return key
    try:
        return dt.date.fromisoformat(str(key))
    except ValueError as e:
        raise PlanError(f"не понял дату {key!r}") from e


def load_week(path: Path, workouts_dir: Path = WORKOUTS) -> Week:
    raw = load_yaml(path) or {}
    week_id = str(raw.get("week") or path.stem)
    start, end = week_bounds(week_id)
    days: list[Planned] = []
    for key, value in (raw.get("days") or {}).items():
        day = _as_date(key)
        if not start <= day <= end:
            raise PlanError(f"{day} не входит в неделю {week_id} ({start}…{end})")
        if value is None or value == "rest":
            continue
        if isinstance(value, str):
            days.append(Planned(day, value, load_template(value, workouts_dir)))
        elif isinstance(value, dict) and "use" in value:
            days.append(Planned(day, value["use"], load_template(value["use"], workouts_dir), bool(value.get("lock"))))
        elif isinstance(value, dict) and "steps" in value:
            days.append(Planned(day, None, value, bool(value.get("lock"))))
        else:
            raise PlanError(f"{day}: ожидаю имя шаблона, rest, {{use: …}} или тренировку со steps")
    days.sort(key=lambda p: p.day)
    return Week(week_id, start, end, str(raw.get("focus") or ""), days, path)
