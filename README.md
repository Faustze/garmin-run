# garmin-run

План бега к марафону 26.09.2027 живёт здесь, в git. Garmin Connect измеряет и хранит всё, что было на самом деле,
часы Forerunner 165 ведут по загруженным тренировкам.

```
plan/weeks/*.yaml ──push──▶ календарь Garmin Connect ──sync──▶ Forerunner 165 ──▶ пробежка
        ▲                                                                          │
        └──── тренер (/run-coach) ◀── status ◀── data/ ◀──pull── Garmin Connect ◀──┘
```

## Установка

```bash
cd ~/garmin-run
uv sync
uv run garmin-run login        # один раз: email, пароль, код MFA; токены — в ~/.garminconnect
```

## Команды

```bash
uv run garmin-run pull --days 14      # активности и здоровье в data/ (не в git)
uv run garmin-run status              # план против факта, восстановление, недели
uv run garmin-run show next           # неделя так, как её увидят часы
uv run garmin-run push next           # пробный прогон
uv run garmin-run push next --apply   # залить в календарь Garmin
uv run garmin-run calendar 2026-09    # что стоит в календаре Garmin
uv run pytest                         # тесты описаний тренировок
```

Тренер — скилл `.claude/skills/run-coach`: запусти `claude` в этой папке и скажи «тренер» или «план на неделю».

## Воскресный разбор

Каждое воскресенье в 19:00 планировщик Windows открывает окно и запускает `scripts/weekly-brief.sh`:
забирает данные Garmin и показывает сводку, дальше — `claude` в этой папке и «тренер».
Сводка остаётся в `data/weekly/` (не в git). Задача переживает выключенный компьютер:
пропущенный запуск случится при следующем включении.

```bash
schtasks.exe /Query /TN "garmin-run weekly" /V /FO LIST   # проверить
schtasks.exe /Change /TN "garmin-run weekly" /ST 20:00    # другое время
schtasks.exe /Delete /TN "garmin-run weekly" /F           # снять
```

## Как это устроено

- `athlete.yaml` — цель, пульс, темпы и именованные цели шагов (`easy`, `threshold`…).
- `workouts/` — библиотека тренировок, формат шагов описан в `src/garmin_run/dsl.py`.
- `plan/weeks/YYYY-Www.yaml` — неделя: дата → тренировка. Повторный `push` меняет только изменённые дни,
  убирает удалённые и не трогает прошедшие.
- `state/garmin.json` — какие id в Garmin соответствуют датам.

## Ограничения

- Работает через неофициальную библиотеку [`garminconnect`](https://github.com/cyberjunky/python-garminconnect):
  она логинится как веб-клиент Garmin Connect. Официальный Training API выдают только компаниям.
  Если Garmin меняет вход, всё ломается, пока не выйдет исправленная версия (`uv lock --upgrade-package garminconnect`).
- Подсказки тренировок Garmin (Daily Suggested Workouts) никуда не деваются — выбирай тренировку из календаря.
