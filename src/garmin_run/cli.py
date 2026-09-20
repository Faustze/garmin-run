"""garmin-run — план бега в git, тренировки и измерения в Garmin Connect.

  login                  войти в Garmin (один раз; дальше по токенам)
  pull [--days N]        скачать активности и здоровье в data/
  status [--days N]      сводка план/факт/восстановление в markdown
  show <неделя|файл>     показать неделю, как её увидят часы
  push <неделя> [--apply]  залить неделю в календарь Garmin (без --apply — пробный прогон)
  calendar [ГГГГ-ММ]     что стоит в календаре Garmin на месяц
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .config import load_athlete
from .dsl import PlanError, build_workout, fmt_duration
from .garmin import NotLoggedIn, connect, login_interactive
from .plan import load_week, week_id_for, week_path
from .status import WEEKDAYS


def _week_arg(value: str) -> Path:
    if value in ("this", "next"):
        day = dt.date.today() + dt.timedelta(days=7 if value == "next" else 0)
        value = week_id_for(day)
    path = Path(value)
    return path if path.suffix == ".yaml" else week_path(value)


def _fmt_step_time(seconds: float) -> str:
    """Шаг показывается точно: 90 с — это 1 мин 30 с, а не округлённые 2 мин."""
    if seconds < 60:
        return f"{seconds:.0f} с"
    if seconds % 60:
        return f"{seconds // 60:.0f} мин {seconds % 60:.0f} с"
    return fmt_duration(seconds)


def _describe(step: dict, indent: str = "  ") -> list[str]:
    if step["type"] == "RepeatGroupDTO":
        lines = [f"{indent}{step['numberOfIterations']}×"]
        for s in step["workoutSteps"]:
            lines += _describe(s, indent + "   ")
        return lines
    end = step["endCondition"]["conditionTypeKey"]
    value = step.get("endConditionValue")
    amount = {"time": lambda: _fmt_step_time(value),
              "distance": lambda: f"{value / 1000:g} км" if value >= 1000 else f"{value:.0f} м",
              "lap.button": lambda: "до круга"}[end]()
    target = step["targetType"]["workoutTargetTypeKey"]
    if target == "heart.rate.zone":
        t = f"пульс {step['targetValueOne']:.0f}–{step['targetValueTwo']:.0f}"
    elif target == "pace.zone":
        slow, fast = (round(1000 / step[k]) for k in ("targetValueOne", "targetValueTwo"))
        t = f"темп {fast // 60}:{fast % 60:02d}–{slow // 60}:{slow % 60:02d}"
    else:
        t = "без цели"
    note = f" — {step['description']}" if step.get("description") else ""
    return [f"{indent}{step['stepType']['stepTypeKey']:<9} {amount:<8} {t}{note}"]


def cmd_show(args: argparse.Namespace) -> None:
    week = load_week(_week_arg(args.week))
    athlete = load_athlete()
    print(f"{week.id} ({week.start:%d.%m}–{week.end:%d.%m}) {week.focus}")
    total_s = total_m = 0
    for p in week.days:
        w = build_workout(p.spec, athlete)
        total_s += w["estimatedDurationInSecs"]
        total_m += w["estimatedDistanceInMeters"]
        lock = " [lock]" if p.lock else ""
        print(f"\n{WEEKDAYS[p.day.weekday()]} {p.day:%d.%m} {w['workoutName']}{lock} (~{fmt_duration(w['estimatedDurationInSecs'])}, ~{w['estimatedDistanceInMeters'] / 1000:.1f} км)")
        for s in w["workoutSegments"][0]["workoutSteps"]:
            print("\n".join(_describe(s)))
    print(f"\nИтого ~{total_m / 1000:.0f} км, ~{fmt_duration(total_s)}")


def cmd_push(args: argparse.Namespace) -> None:
    from .push import push_week

    week = load_week(_week_arg(args.week))
    push_week(week, connect() if args.apply else None)


def cmd_pull(args: argparse.Namespace) -> None:
    from .pull import pull

    since = dt.date.fromisoformat(args.activities_since) if args.activities_since else None
    pull(connect(), args.days, since)


def cmd_status(args: argparse.Namespace) -> None:
    from .status import render

    print(render(args.days, args.ahead))


def cmd_calendar(args: argparse.Namespace) -> None:
    month = dt.date.fromisoformat(f"{args.month}-01") if args.month else dt.date.today()
    data = connect().get_scheduled_workouts(month.year, month.month) or {}
    for item in sorted(data.get("calendarItems") or [], key=lambda i: i.get("date", "")):
        if item.get("itemType") in ("workout", "activity"):
            print(f"{item.get('date')}  {item.get('itemType'):<8} {item.get('title')}")


def cmd_login(_: argparse.Namespace) -> None:
    api = login_interactive()
    print(f"Вошёл как {api.get_full_name()}. Токены сохранены, пароль больше не нужен.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="garmin-run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(fn=cmd_login)

    p = sub.add_parser("pull")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--activities-since", help="ГГГГ-ММ-ДД — активности с этой даты (здоровье — только --days)")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("status")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--ahead", type=int, default=7)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("show")
    p.add_argument("week", help="2026-W39, this, next или путь к yaml")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("push")
    p.add_argument("week", help="2026-W39, this, next или путь к yaml")
    p.add_argument("--apply", action="store_true", help="на самом деле залить в Garmin")
    p.set_defaults(fn=cmd_push)

    p = sub.add_parser("calendar")
    p.add_argument("month", nargs="?", help="ГГГГ-ММ")
    p.set_defaults(fn=cmd_calendar)

    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except (PlanError, NotLoggedIn, FileNotFoundError) as e:
        sys.exit(f"ошибка: {e}")
