"""Пути репозитория и профиль спортсмена."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("GARMIN_RUN_ROOT", Path(__file__).resolve().parents[2]))
ATHLETE = ROOT / "athlete.yaml"
WORKOUTS = ROOT / "workouts"
WEEKS = ROOT / "plan" / "weeks"
STATE = ROOT / "state" / "garmin.json"
DATA = ROOT / "data"

# Токены Garmin лежат вне репозитория.
TOKENSTORE = Path(os.environ.get("GARMINTOKENS", "~/.garminconnect")).expanduser()


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_athlete(path: Path = ATHLETE) -> dict[str, Any]:
    return load_yaml(path)
