import datetime as dt
from pathlib import Path

import pytest

from garmin_run.dsl import PlanError, build_workout, fingerprint, parse_amount, parse_pace_range, resolve_target
from garmin_run.plan import load_week, week_bounds

ROOT = Path(__file__).resolve().parents[1]
ATHLETE = {
    "estimate_pace": "6:00",
    "targets": {"easy": {"hr": [125, 150]}, "threshold": {"pace": "5:10-5:20"}, "stride": {}},
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45min", ("time", 2700.0)),
        ("1h10min", ("time", 4200.0)),
        ("90s", ("time", 90.0)),
        ("2min30s", ("time", 150.0)),
        ("400m", ("distance", 400.0)),
        ("1km", ("distance", 1000.0)),
        ("16.5km", ("distance", 16500.0)),
        ("lap", ("lap", None)),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["45", "45 minutes", "1.5h", ""])
def test_parse_amount_rejects(text):
    with pytest.raises(PlanError):
        parse_amount(text)


def test_pace_range_sorted_and_single():
    assert parse_pace_range("5:20-5:10") == (310, 320)
    assert parse_pace_range("5:27") == (322, 332)


def test_resolve_target():
    assert resolve_target("easy", ATHLETE["targets"]).kind == "hr"
    assert resolve_target("hr:150-130", {}).low == 130
    assert resolve_target("stride", ATHLETE["targets"]) is None
    with pytest.raises(PlanError):
        resolve_target("tempo", ATHLETE["targets"])


def test_pace_target_is_speed_slow_first():
    w = build_workout({"name": "t", "steps": [{"run": "1km @ threshold"}]}, ATHLETE)
    step = w["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert step["targetValueOne"] == pytest.approx(1000 / 320, abs=1e-3)
    assert step["targetValueTwo"] == pytest.approx(1000 / 310, abs=1e-3)


def test_repeat_orders_and_estimates():
    spec = {
        "name": "Порог",
        "steps": [
            {"warmup": "10min @ easy"},
            {"repeat": 3, "steps": [{"run": "1km @ threshold"}, {"recover": "2min"}]},
            {"cooldown": {"lap": True}},
        ],
    }
    w = build_workout(spec, ATHLETE)
    steps = w["workoutSegments"][0]["workoutSteps"]
    group = steps[1]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    assert [s["stepOrder"] for s in group["workoutSteps"]] == [3, 4]
    assert all(s["childStepId"] == group["childStepId"] == 1 for s in group["workoutSteps"])
    assert group["numberOfIterations"] == 3
    assert steps[2]["endCondition"]["conditionTypeKey"] == "lap.button"
    # 600 + 3 × (315 + 120) + 60 (круг)
    assert w["estimatedDurationInSecs"] == 600 + 3 * (315 + 120) + 60


def test_step_note_and_errors():
    w = build_workout({"name": "x", "steps": [{"run": {"time": "20s", "note": "быстро"}}]}, ATHLETE)
    assert w["workoutSegments"][0]["workoutSteps"][0]["description"] == "быстро"
    for bad in (
        {"name": "x", "steps": []},
        {"name": "x", "steps": [{"jog": "10min"}]},
        {"name": "x", "steps": [{"run": 10}]},
        {"name": "x", "steps": [{"repeat": 2, "steps": [{"repeat": 2, "steps": [{"run": "1km"}]}]}]},
    ):
        with pytest.raises(PlanError):
            build_workout(bad, ATHLETE)


def test_fingerprint_depends_on_day():
    w = build_workout({"name": "x", "steps": [{"run": "10min"}]}, ATHLETE)
    assert fingerprint(w, "2026-09-21") != fingerprint(w, "2026-09-22")


def test_week_bounds():
    assert week_bounds("2026-W39") == (dt.date(2026, 9, 21), dt.date(2026, 9, 27))


def test_repo_week_and_templates_build():
    from garmin_run.config import load_athlete

    athlete = load_athlete(ROOT / "athlete.yaml")
    for path in sorted((ROOT / "plan" / "weeks").glob("*.yaml")):
        week = load_week(path, ROOT / "workouts")
        for p in week.days:
            build_workout(p.spec, athlete)


def test_week_rejects_foreign_date(tmp_path):
    (tmp_path / "2026-W39.yaml").write_text("week: 2026-W39\ndays:\n  2026-09-28: rest\n")
    with pytest.raises(PlanError):
        load_week(tmp_path / "2026-W39.yaml", tmp_path)
