"""YAML-описание тренировки → JSON для workout-service Garmin Connect.

Шаг — одна пара «вид: что»:

    - warmup: 15min @ easy
    - repeat: 5
      steps:
        - run: 1km @ threshold
        - recover: 2min @ recovery
    - cooldown: 10min

Виды: warmup, run, recover, rest, cooldown, repeat; у силовой ещё exercise.
Длительность: 45min, 1h10min, 90s, 2min30s. Дистанция: 400m, 1km, 16km.
lap — до нажатия кнопки круга.
Цель: имя из athlete.yaml (easy, threshold…), hr:130-150 или pace:5:10-5:20.
Длинная форма шага: {time|distance|lap, target, note}.

Силовая — `sport: strength` на уровне тренировки и шаг `exercise`:

    sport: strength
    steps:
      - repeat: 3
        steps:
          - exercise: {lap: true, note: как делать}
          - rest: 60s

Упражнения не из каталога Garmin: название и техника — в заметке шага.
Дистанция у силовой нулевая, в объём недели она не идёт.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
STRENGTH = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}
SPORTS = {"running": RUNNING, "strength": STRENGTH}

STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "run": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "exercise": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recover": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
}
REPEAT_TYPE = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}

END = {
    "lap": {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
    "time": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
    "distance": {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True},
}
END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}

NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
HR_TARGET = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
PACE_TARGET = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}

LAP_ESTIMATE_S = 60

_TIME_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)min)?(?:(\d+)s)?$")
_DIST_RE = re.compile(r"^(\d+(?:\.\d+)?)(km|m)$")
_PACE_RE = re.compile(r"^(\d+):([0-5]\d)$")


class PlanError(ValueError):
    """Ошибка в описании тренировки или недели."""


@dataclass(frozen=True)
class Target:
    kind: str  # "hr" | "pace"
    low: float  # уд/мин или сек/км (быстрее)
    high: float  # уд/мин или сек/км (медленнее)


def parse_amount(text: str) -> tuple[str, float | None]:
    """'45min' → ('time', 2700), '1km' → ('distance', 1000), 'lap' → ('lap', None)."""
    s = text.strip().lower().replace(" ", "")
    if s == "lap":
        return "lap", None
    if m := _DIST_RE.match(s):
        value = float(m[1])
        return "distance", value * 1000 if m[2] == "km" else value
    m = _TIME_RE.match(s)
    if m and any(m.groups()):
        h, mi, sec = (int(g or 0) for g in m.groups())
        return "time", float(h * 3600 + mi * 60 + sec)
    raise PlanError(f"не понял длительность {text!r}: ожидаю 45min, 1h10min, 90s, 400m, 1km или lap")


def parse_pace(text: str) -> int:
    """'5:27' → 327 секунд на километр."""
    m = _PACE_RE.match(text.strip())
    if not m:
        raise PlanError(f"не понял темп {text!r}: ожидаю м:сс")
    return int(m[1]) * 60 + int(m[2])


def parse_pace_range(text: str) -> tuple[int, int]:
    """'5:10-5:20' → (310, 320); одиночный темп даёт коридор ±5 с."""
    parts = text.strip().split("-")
    if len(parts) == 1:
        p = parse_pace(parts[0])
        return p - 5, p + 5
    if len(parts) == 2:
        a, b = sorted(parse_pace(p) for p in parts)
        return a, b
    raise PlanError(f"не понял коридор темпа {text!r}")


def resolve_target(spec: str | None, targets: dict[str, Any]) -> Target | None:
    if spec is None or not str(spec).strip():
        return None
    spec = str(spec).strip()
    if spec in targets:
        t = targets[spec] or {}
        if "hr" in t:
            lo, hi = sorted(t["hr"])
            return Target("hr", lo, hi)
        if "pace" in t:
            return Target("pace", *parse_pace_range(t["pace"]))
        return None
    kind, _, value = spec.partition(":")
    if kind == "hr":
        try:
            lo, hi = sorted(int(v) for v in value.split("-"))
        except ValueError as e:
            raise PlanError(f"не понял пульс {spec!r}: ожидаю hr:130-150") from e
        return Target("hr", lo, hi)
    if kind == "pace":
        return Target("pace", *parse_pace_range(value))
    raise PlanError(f"неизвестная цель {spec!r}: нет в athlete.yaml и не hr:/pace:")


def target_fields(t: Target | None) -> dict[str, Any]:
    if t is None:
        return {"targetType": NO_TARGET}
    if t.kind == "hr":
        return {"targetType": HR_TARGET, "targetValueOne": t.low, "targetValueTwo": t.high}
    # Garmin хранит темп как скорость в м/с; первое значение — медленная граница.
    return {
        "targetType": PACE_TARGET,
        "targetValueOne": round(1000 / t.high, 4),
        "targetValueTwo": round(1000 / t.low, 4),
    }


class _Builder:
    def __init__(self, targets: dict[str, Any], estimate_pace_s: int, moving: bool = True):
        self.targets = targets
        self.estimate_pace_s = estimate_pace_s
        self.moving = moving
        self.order = 0
        self.groups = 0
        self.seconds = 0.0
        self.meters = 0.0

    def steps(self, items: list[Any], child: int | None = None, mult: int = 1) -> list[dict[str, Any]]:
        if not isinstance(items, list) or not items:
            raise PlanError("steps должен быть непустым списком")
        return [self.step(item, child, mult) for item in items]

    def step(self, item: Any, child: int | None, mult: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise PlanError(f"шаг должен быть словарём, а не {item!r}")
        self.order += 1
        if "repeat" in item:
            return self.repeat(item, child, mult)
        if len(item) != 1:
            raise PlanError(f"в шаге должен быть один вид из {sorted(STEP_TYPES)}: {item!r}")
        (kind, body), = item.items()
        if kind not in STEP_TYPES:
            raise PlanError(f"неизвестный вид шага {kind!r}")

        amount, target_spec, note = self._parse_body(body)
        end_kind, value = parse_amount(amount)
        target = resolve_target(target_spec, self.targets)

        node: dict[str, Any] = {
            "type": "ExecutableStepDTO",
            "stepOrder": self.order,
            "stepType": STEP_TYPES[kind],
            "endCondition": END[end_kind],
            **target_fields(target),
        }
        if value is not None:
            node["endConditionValue"] = value
        if child is not None:
            node["childStepId"] = child
        if note:
            node["description"] = note
        self._estimate(end_kind, value, target, mult)
        return node

    def repeat(self, item: dict[str, Any], child: int | None, mult: int) -> dict[str, Any]:
        if child is not None:
            raise PlanError("вложенные repeat не поддерживаются")
        n = int(item["repeat"])
        if n < 1:
            raise PlanError("repeat должен быть ≥ 1")
        self.groups += 1
        gid = self.groups
        node: dict[str, Any] = {
            "type": "RepeatGroupDTO",
            "stepOrder": self.order,
            "stepType": REPEAT_TYPE,
            "childStepId": gid,
            "numberOfIterations": n,
            "endCondition": END_ITERATIONS,
            "endConditionValue": float(n),
            "smartRepeat": False,
        }
        node["workoutSteps"] = self.steps(item.get("steps"), gid, mult * n)
        return node

    @staticmethod
    def _parse_body(body: Any) -> tuple[str, str | None, str | None]:
        if isinstance(body, (int, float)):
            raise PlanError(f"укажи единицы длительности: {body!r}")
        if isinstance(body, str):
            amount, _, target = body.partition("@")
            return amount.strip(), target.strip() or None, None
        if isinstance(body, dict):
            keys = [k for k in ("time", "distance") if k in body]
            if body.get("lap"):
                keys.append("lap")
            if len(keys) != 1:
                raise PlanError(f"в шаге нужен ровно один из time/distance/lap: {body!r}")
            amount = "lap" if keys[0] == "lap" else str(body[keys[0]])
            return amount, body.get("target"), body.get("note")
        raise PlanError(f"не понял шаг {body!r}")

    def _estimate(self, end_kind: str, value: float | None, target: Target | None, mult: int) -> None:
        pace = (target.low + target.high) / 2 if target and target.kind == "pace" else self.estimate_pace_s
        if end_kind == "distance":
            seconds, meters = value / 1000 * pace, value
        else:
            seconds = value if end_kind == "time" else LAP_ESTIMATE_S
            meters = seconds / pace * 1000
        self.seconds += seconds * mult
        self.meters += meters * mult if self.moving else 0


def build_workout(spec: dict[str, Any], athlete: dict[str, Any]) -> dict[str, Any]:
    """Собрать JSON тренировки для upload_workout."""
    name = spec.get("name")
    if not name:
        raise PlanError("у тренировки нет name")
    sport_key = spec.get("sport", "running")
    if sport_key not in SPORTS:
        raise PlanError(f"неизвестный sport {sport_key!r}: ожидаю {' или '.join(SPORTS)}")
    sport = SPORTS[sport_key]
    b = _Builder(athlete.get("targets") or {}, parse_pace(athlete.get("estimate_pace", "6:20")), sport is RUNNING)
    steps = b.steps(spec.get("steps"))
    workout: dict[str, Any] = {
        "workoutName": name,
        "sportType": sport,
        "estimatedDurationInSecs": round(b.seconds),
        "estimatedDistanceInMeters": round(b.meters),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}],
    }
    if spec.get("description"):
        workout["description"] = spec["description"].strip()
    return workout


def fingerprint(workout: dict[str, Any], day: str) -> str:
    raw = json.dumps({"day": day, "workout": workout}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def fmt_duration(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60}:{m % 60:02d}" if m >= 60 else f"{m} мин"
