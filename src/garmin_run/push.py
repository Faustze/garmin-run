"""Синхронизация недели из репозитория в календарь Garmin Connect.

На каждую дату — отдельная копия тренировки в библиотеке Garmin, поставленная
в календарь. Соответствие «дата → id в Garmin» хранится в state/garmin.json.
Прошедшие даты не трогаем: там уже история.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .config import DATA, STATE, load_athlete
from .dsl import build_workout, fingerprint, fmt_duration
from .plan import Week


def load_state(path: Path = STATE) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(state.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _schedule_id(resp: Any) -> int | None:
    if isinstance(resp, dict):
        for key in ("workoutScheduleId", "scheduleId", "id"):
            if isinstance(resp.get(key), int):
                return resp[key]
    return None


def _remove(api: Any, entry: dict[str, Any]) -> None:
    """Снять с календаря и удалить копию; если её уже убрали руками — не падать."""
    if entry.get("schedule_id"):
        try:
            api.unschedule_workout(entry["schedule_id"])
        except Exception as e:
            print(f"    (снять с календаря не вышло: {e})")
    try:
        api.delete_workout(entry["workout_id"])
    except Exception as e:
        print(f"    (удалить тренировку не вышло: {e})")


def push_week(week: Week, api: Any | None, *, today: dt.date | None = None) -> bool:
    """api=None — пробный прогон: показать изменения и сохранить JSON в data/preview."""
    today = today or dt.date.today()
    dry = api is None
    athlete = load_athlete()
    state = load_state()
    changed = False

    wanted: dict[str, tuple[Any, dict[str, Any], str]] = {}
    for p in week.days:
        day = p.day.isoformat()
        workout = build_workout(p.spec, athlete)
        wanted[day] = (p, workout, fingerprint(workout, day))

    for day in sorted(state):
        d = dt.date.fromisoformat(day)
        if week.contains(d) and day not in wanted and d >= today:
            print(f"- {day}  убрать «{state[day]['name']}»")
            if not dry:
                _remove(api, state.pop(day))
                save_state(state)
            changed = True

    for day, (p, workout, h) in sorted(wanted.items()):
        cur = state.get(day)
        label = f"{day}  {workout['workoutName']} (~{fmt_duration(workout['estimatedDurationInSecs'])}, ~{workout['estimatedDistanceInMeters'] / 1000:.1f} км)"
        if p.day < today:
            print(f"  {label} — дата прошла, не трогаю")
            continue
        if cur and cur["hash"] == h:
            print(f"= {label}")
            continue
        print(f"{'~' if cur else '+'} {label}")
        changed = True
        if dry:
            preview = DATA / "preview" / f"{day}.json"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_text(json.dumps(workout, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        if cur:
            _remove(api, state.pop(day))
            save_state(state)
        created = api.upload_workout(workout)
        workout_id = created["workoutId"]
        scheduled = api.schedule_workout(workout_id, day)
        state[day] = {
            "name": workout["workoutName"],
            "ref": p.ref,
            "workout_id": workout_id,
            "schedule_id": _schedule_id(scheduled),
            "hash": h,
            "lock": p.lock,
        }
        if state[day]["schedule_id"] is None:
            print(f"    (не нашёл id расписания в ответе: {json.dumps(scheduled, ensure_ascii=False)[:300]})")
        save_state(state)

    if dry and changed:
        print(f"\nПробный прогон: JSON в {DATA / 'preview'}. Залить: garmin-run push {week.id} --apply")
    return changed
