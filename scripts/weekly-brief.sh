#!/usr/bin/env bash
# Воскресный разбор: забрать данные Garmin и показать сводку.
# Запускается планировщиком Windows в отдельном окне, поэтому PATH задаём явно —
# окружения интерактивного шелла тут нет.
set -u
export PATH="$HOME/.local/bin:$PATH"

REPO="$HOME/garmin-run"
cd "$REPO" || { echo "нет папки $REPO"; sleep 30; exit 1; }

OUT="$REPO/data/weekly"
mkdir -p "$OUT"
STAMP="$(date +%Y-%m-%d)"
BRIEF="$OUT/$STAMP.md"
LOG="$OUT/.pull.log"

echo "════ Воскресный разбор · $STAMP ════"
echo

if uv run garmin-run pull --days 14 >"$LOG" 2>&1; then
    tail -3 "$LOG"
else
    echo "Данные забрать не вышло:"
    tail -5 "$LOG"
    echo
    echo "Если про токены — выполни: cd ~/garmin-run && uv run garmin-run login"
    echo "Сводка ниже будет по тому, что уже лежит локально."
fi
echo

uv run garmin-run status --days 14 | tee "$BRIEF"

echo
echo "Сводка сохранена: $BRIEF"
echo "Дальше: запусти claude в ~/garmin-run и скажи «тренер» — соберём неделю."
echo
read -r -p "Enter — закрыть окно. " _ || sleep 300
