"""Дашборд: один HTML-файл из data/, plan/ и journal/ — посмотреть неделю глазами, а не таблицами.

Читает только локальные файлы, как status. Результат ложится в data/dashboard.html:
там данные здоровья, поэтому он, как и вся data/, не попадает в git.
Графики — встроенный SVG, без библиотек и сети.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import DATA, ROOT, WEEKS, load_athlete, load_yaml
from .dsl import PlanError, build_workout
from .plan import load_week, week_bounds, week_id_for
from .push import load_state
from .signals import fatigue_signals
from .status import WEEKDAYS, _pace, dig, is_run, is_strength, load_activities

OUT = DATA / "dashboard.html"
EASY_REFS = ("easy", "long", "recovery")
e = html.escape


# ── данные ──────────────────────────────────────────────────────────────────

def load_daily() -> dict[dt.date, dict[str, Any]]:
    out = {}
    for p in sorted((DATA / "daily").glob("*.json")):
        out[dt.date.fromisoformat(p.stem)] = json.loads(p.read_text(encoding="utf-8"))
    return out


def planned_week(week_id: str, athlete: dict[str, Any]) -> tuple[dict[dt.date, dict[str, Any]], dict[str, Any]]:
    """Дата → {name, ref, km, secs} и сырой yaml недели (focus, changes)."""
    path = WEEKS / f"{week_id}.yaml"
    if not path.exists():
        return {}, {}
    week = load_week(path)
    days = {}
    for p in week.days:
        try:
            w = build_workout(p.spec, athlete)
        except PlanError:
            continue
        days[p.day] = {"name": w["workoutName"], "ref": p.ref or "",
                       "km": w["estimatedDistanceInMeters"] / 1000, "secs": w["estimatedDurationInSecs"]}
    return days, load_yaml(path) or {}


def current_phase(today: dt.date) -> dict[str, str] | None:
    """Строка из таблицы фаз в plan/README.md, в которую попадает сегодня."""
    path = ROOT / "plan" / "README.md"
    if not path.exists():
        return None
    rx = re.compile(r"(\d{2})\.(\d{2})(?:\.(\d{4}))?\s*[–-]\s*(\d{2})\.(\d{2})\.(\d{4})")
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not (m := rx.search(cells[1])):
            continue
        end = dt.date(int(m[6]), int(m[5]), int(m[4]))
        start = dt.date(int(m[3] or m[6]), int(m[2]), int(m[1]))
        if start <= today <= end:
            return {"name": cells[0], "dates": cells[1], "volume": cells[3], "long": cells[4], "key": cells[5]}
    return None


def latest_journal() -> tuple[str, str] | None:
    files = sorted((ROOT / "journal").glob("*.md"))
    return (files[-1].stem, files[-1].read_text(encoding="utf-8")) if files else None


# ── мелкая разметка ─────────────────────────────────────────────────────────

def md_inline(text: str) -> str:
    s = e(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


def md_block(text: str) -> str:
    """Журнал пишется абзацами с **жирными** ярлыками — этого подмножества хватает."""
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        if para.startswith("# "):
            out.append(f"<h3>{md_inline(para[2:])}</h3>")
        else:
            out.append(f"<p>{md_inline(' '.join(para.split()))}</p>")
    return "\n".join(out)


def badge(kind: str, label: str) -> str:
    icon = {"good": "✓", "warning": "!", "serious": "✕", "neutral": "·", "info": "+"}[kind]
    return f'<span class="badge {kind}"><i aria-hidden="true">{icon}</i>{e(label)}</span>'


def nice_max(v: float) -> float:
    for step in (5, 10, 20, 25, 50, 100):
        if v <= step * 5:
            return max(step, -(-v // step) * step)
    return v


# ── графики ─────────────────────────────────────────────────────────────────

def volume_chart(rows: list[tuple[str, float, float | None, bool]]) -> str:
    """Столбцы по неделям: факт — заливка, план — контур. rows: (неделя, факт, план, текущая)."""
    w, h, left, bottom, top = 720, 220, 36, 28, 12
    ymax = nice_max(max([f for _, f, _, _ in rows] + [p or 0 for _, _, p, _ in rows] + [10]))
    band = (w - left) / len(rows)
    bw = min(28.0, band * 0.5)
    y = lambda v: top + (h - top - bottom) * (1 - v / ymax)
    parts = []
    step = ymax / 4
    for i in range(5):
        v = step * i
        parts.append(f'<line class="grid" x1="{left}" x2="{w}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                     f'<text class="tick" x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    for i, (wk, fact, plan, cur) in enumerate(rows):
        cx = left + band * (i + 0.5)
        tip = f"{wk}: факт {fact:.1f} км" + (f", план ~{plan:.0f} км" if plan else "")
        if fact:
            parts.append(f'<path class="fact" d="{bar_path(cx - bw / 2, y(fact), bw, y(0) - y(fact))}"/>')
        if plan and fact:
            # план поверх факта — засечка на своей высоте, чтобы видно было недобег и перебег
            parts.append(f'<line class="plan" x1="{cx - bw / 2 - 5:.1f}" x2="{cx + bw / 2 + 5:.1f}" '
                         f'y1="{y(plan):.1f}" y2="{y(plan):.1f}"/>')
        elif plan:
            parts.append(f'<rect class="plan" x="{cx - bw / 2:.1f}" y="{y(plan):.1f}" width="{bw:.1f}" '
                         f'height="{y(0) - y(plan):.1f}" rx="4"/>')
        label = wk[-3:] + (" ·" if cur else "")
        parts.append(f'<text class="tick{" cur" if cur else ""}" x="{cx:.1f}" y="{h - 8}" text-anchor="middle">{label}</text>')
        if cur or i == len(rows) - 1:
            val = fact if fact else plan
            if val:
                parts.append(f'<text class="val" x="{cx:.1f}" y="{y(max(fact, plan or 0)) - 6:.1f}" text-anchor="middle">{val:.0f}</text>')
        parts.append(f'<rect class="hit" x="{cx - band / 2:.1f}" y="{top}" width="{band:.1f}" height="{h - top - bottom}" data-tip="{e(tip)}"/>')
    parts.append(f'<line class="axis" x1="{left}" x2="{w}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Объём бега по неделям, км">{"".join(parts)}</svg>'


def bar_path(x: float, y0: float, bw: float, bh: float, r: float = 4) -> str:
    """Столбец со скруглённым верхом и прямым низом у базовой линии."""
    r = min(r, bh, bw / 2)
    return (f"M{x:.1f},{y0 + bh:.1f}V{y0 + r:.1f}Q{x:.1f},{y0:.1f} {x + r:.1f},{y0:.1f}"
            f"H{x + bw - r:.1f}Q{x + bw:.1f},{y0:.1f} {x + bw:.1f},{y0 + r:.1f}V{y0 + bh:.1f}Z")


def spark(title: str, unit: str, points: list[tuple[dt.date, float | None]], *,
          ref: float | None = None, ref_label: str = "", band: tuple[float, float] | None = None,
          fmt: str = "{:.0f}") -> str:
    """Один показатель восстановления: линия по дням, опорная линия или коридор нормы."""
    w, h, left, bottom, top = 260, 110, 28, 20, 10
    vals = [v for _, v in points if v is not None]
    if not vals:
        return f'<figure class="spark"><figcaption>{e(title)}</figcaption><p class="muted">нет данных</p></figure>'
    extra = [ref] if ref is not None else []
    extra += list(band) if band else []
    lo, hi = min(vals + extra), max(vals + extra)
    pad = max((hi - lo) * 0.15, 1)
    lo, hi = lo - pad, hi + pad
    n = len(points)
    x = lambda i: left + (w - left - 6) * (i / max(n - 1, 1))
    y = lambda v: top + (h - top - bottom) * (1 - (v - lo) / (hi - lo))
    parts = []
    if band:
        parts.append(f'<rect class="band" x="{left}" width="{w - left - 6}" y="{y(band[1]):.1f}" height="{y(band[0]) - y(band[1]):.1f}"/>')
    for v in (lo + pad, hi - pad):
        parts.append(f'<text class="tick" x="{left - 5}" y="{y(v) + 4:.1f}" text-anchor="end">{fmt.format(v)}</text>')
    if ref is not None:
        parts.append(f'<line class="ref" x1="{left}" x2="{w - 6}" y1="{y(ref):.1f}" y2="{y(ref):.1f}"/>'
                     f'<text class="tick" x="{w - 8}" y="{y(ref) - 4:.1f}" text-anchor="end">{e(ref_label)}</text>')
    segs, cur = [], []
    for i, (_, v) in enumerate(points):
        if v is None:
            segs.append(cur)
            cur = []
        else:
            cur.append(f"{x(i):.1f},{y(v):.1f}")
    segs.append(cur)
    for s in segs:
        if len(s) > 1:
            parts.append(f'<polyline class="line" points="{" ".join(s)}"/>')
    for i, (d, v) in enumerate(points):
        if v is None:
            continue
        last = i == max(j for j, (_, vv) in enumerate(points) if vv is not None)
        parts.append(f'<circle class="dot{" last" if last else ""}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="{4 if last else 2.5}"/>')
        parts.append(f'<rect class="hit" x="{x(i) - 8:.1f}" y="{top}" width="16" height="{h - top - bottom}" '
                     f'data-tip="{WEEKDAYS[d.weekday()]} {d:%d.%m}: {fmt.format(v)} {e(unit)}"/>')
    for i in (0, n - 1):
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{h - 6}" text-anchor="{"start" if i == 0 else "end"}">{points[i][0]:%d.%m}</text>')
    last_v = vals[-1]
    return (f'<figure class="spark"><figcaption>{e(title)} <b>{fmt.format(last_v)}</b> <span class="muted">{e(unit)}</span></figcaption>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">{"".join(parts)}</svg></figure>')


# ── страница ────────────────────────────────────────────────────────────────

def day_cards(start: dt.date, plan: dict[dt.date, dict[str, Any]], state: dict[str, Any],
              sessions_by_day: dict[str, list[dict[str, Any]]], data_until: dt.date | None,
              today: dt.date, easy_max: float) -> str:
    cards = []
    for i in range(7):
        d = start + dt.timedelta(days=i)
        p = plan.get(d) or ({"name": state[d.isoformat()]["name"], "ref": state[d.isoformat()].get("ref", "")}
                            if d.isoformat() in state else None)
        acts = sessions_by_day.get(d.isoformat(), [])
        facts = []
        for a in acts:
            hr = a.get("averageHR")
            if not is_run(a):
                facts.append(f'<div class="fact-line">{e(str(a.get("activityName") or "силовая"))} · {(a.get("duration") or 0) / 60:.0f} мин</div>')
                continue
            facts.append(f'<div class="fact-line">{(a.get("distance") or 0) / 1000:.1f} км · {_pace(a.get("averageSpeed"))}/км'
                         f' · пульс {hr:.0f}</div>' if hr else
                         f'<div class="fact-line">{(a.get("distance") or 0) / 1000:.1f} км · {_pace(a.get("averageSpeed"))}/км</div>')
        if acts and p:
            easy = p.get("ref", "").startswith(EASY_REFS)
            hot = easy and any((a.get("averageHR") or 0) > easy_max for a in acts)
            status = badge("warning", f"пульс выше {easy_max:.0f}") if hot else badge("good", "сделано")
        elif acts:
            status = badge("info", "вне плана")
        elif p and d > today:
            status = badge("neutral", "впереди")
        elif p and (data_until is None or d > data_until):
            status = badge("neutral", "нет данных")
        elif p:
            status = badge("serious", "пропуск")
        else:
            status = badge("neutral", "отдых")
        plan_line = e(p["name"]) + (f' <span class="muted">~{p["km"]:.0f} км</span>' if p and p.get("km") else "") if p else '<span class="muted">—</span>'
        cls = " today" if d == today else ""
        cards.append(f'<li class="day{cls}"><div class="day-head"><span><b>{WEEKDAYS[d.weekday()]}</b> {d:%d.%m}</span>{status}</div>'
                     f'<div class="plan-line">{plan_line}</div>{"".join(facts)}</li>')
    return f'<ol class="days">{"".join(cards)}</ol>'


def render(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    athlete = load_athlete()
    easy_max = float((dig(athlete, "targets", "easy", "hr") or [0, 150])[1])
    state = load_state()
    daily = load_daily()
    data_until = max(daily) if daily else None
    activities = load_activities(today - dt.timedelta(days=7 * 10))
    runs = [a for a in activities if is_run(a)]
    sessions_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in activities:
        if is_run(a) or is_strength(a):
            sessions_by_day[a["startTimeLocal"][:10]].append(a)

    this_id = week_id_for(today)
    next_id = week_id_for(today + dt.timedelta(days=7))
    this_plan, this_raw = planned_week(this_id, athlete)
    next_plan, _ = planned_week(next_id, athlete)
    start, _ = week_bounds(this_id)

    # объём по неделям: 8 прошедших, текущая и следующая
    fact_km: dict[str, float] = defaultdict(float)
    for a in runs:
        fact_km[week_id_for(dt.date.fromisoformat(a["startTimeLocal"][:10]))] += (a.get("distance") or 0) / 1000
    vol_rows = []
    for k in range(-8, 2):
        wk = week_id_for(start + dt.timedelta(days=7 * k))
        plan = {this_id: this_plan, next_id: next_plan}.get(wk)
        if plan is None and (WEEKS / f"{wk}.yaml").exists():
            plan = planned_week(wk, athlete)[0]
        plan_km = sum(p["km"] for p in plan.values()) if plan else None
        vol_rows.append((wk, fact_km.get(wk, 0.0), plan_km, wk == this_id))

    # плитки
    goal = athlete.get("goal") or {}
    race = dt.date.fromisoformat(str(goal.get("date"))) if goal.get("date") else None
    status_json = json.loads((DATA / "status.json").read_text(encoding="utf-8")) if (DATA / "status.json").exists() else {}
    vo2 = dig(status_json, "max_metrics", 0, "generic", "vo2MaxPreciseValue")
    week_fact = sum((a.get("distance") or 0) / 1000 for a in runs if start.isoformat() <= a["startTimeLocal"][:10] <= (start + dt.timedelta(days=6)).isoformat())
    week_plan = sum(p["km"] for p in this_plan.values())
    sig_day = min(today, data_until) if data_until else None
    signals = fatigue_signals(sig_day, daily, activities) if sig_day else []
    n = len(signals)
    verdict = ("план как есть", "good") if n == 0 else ("ключевую не добивать", "warning") if n == 1 else \
        ("ключевую заменить лёгкой", "serious") if n == 2 else ("лёгкие 30 мин или отдых", "serious")

    since14 = (today - dt.timedelta(days=13)).isoformat()
    easy_runs = [a for a in runs if a["startTimeLocal"][:10] >= since14
                 and str(dig(state, a["startTimeLocal"][:10], "ref") or "").startswith(EASY_REFS)]
    easy_ok = sum(1 for a in easy_runs if (a.get("averageHR") or 0) <= easy_max)

    tiles = [
        tile("До старта", f"{(race - today).days} дн" if race else "—",
             f'{e(str(goal.get("race", "")))} {race:%d.%m.%Y} · цель {e(str(goal.get("target", "")))}' if race else ""),
        tile("Неделя, км", f"{week_fact:.0f} <small>из ~{week_plan:.0f}</small>" if week_plan else f"{week_fact:.0f}",
             e(this_raw.get("focus", "")) if this_raw else "плана недели нет"),
        tile("Сигналы усталости", str(n),
             badge(verdict[1], verdict[0]) + (f'<div class="muted">{e("; ".join(signals))}</div>' if signals else "")
             + (f'<div class="muted">по данным на {sig_day:%d.%m}</div>' if sig_day else "")),
        tile(f"Лёгкие в пульсе ≤{easy_max:.0f}", f"{easy_ok} из {len(easy_runs)}" if easy_runs else "—",
             "за 14 дней, по тренировкам из плана" if easy_runs else "лёгких из плана за 14 дней ещё нет"),
        tile("VO₂max", f"{vo2:.1f}" if vo2 else "—", f"по Garmin на {e(str(status_json.get('date', '')))}" if vo2 else ""),
    ]

    # восстановление за 28 дней
    days28 = [today - dt.timedelta(days=i) for i in range(27, -1, -1)]
    def series(*path: Any, scale: float = 1) -> list[tuple[dt.date, float | None]]:
        return [(d, (v / scale) if (v := dig(daily.get(d), *path)) is not None else None) for d in days28]
    baseline = next((dig(daily[d], "hrv", "hrvSummary", "baseline") for d in sorted(daily, reverse=True)
                     if dig(daily[d], "hrv", "hrvSummary", "baseline")), None) if daily else None
    hrv_band = (baseline["balancedLow"], baseline["balancedUpper"]) if baseline else None
    sparks = [
        spark("HRV за ночь", "мс", series("hrv", "hrvSummary", "lastNightAvg"), band=hrv_band),
        spark("Пульс покоя", "уд/мин", series("summary", "restingHeartRate")),
        spark("Сон", "ч", series("sleep", "dailySleepDTO", "sleepTimeSeconds", scale=3600), ref=6, ref_label="6 ч", fmt="{:.1f}"),
        spark("Body Battery утром", "", series("summary", "bodyBatteryAtWakeTime"), ref=35, ref_label="35"),
    ]

    phase = current_phase(today)
    phase_html = (f'<p><b>{e(phase["name"])}</b> · {e(phase["dates"])} · объём {e(phase["volume"])} км · '
                  f'длительная {e(phase["long"])} км</p><p class="muted">{e(phase["key"])}</p>') if phase else \
        '<p class="muted">сегодня не попадает ни в одну фазу plan/README.md</p>'
    changes = this_raw.get("changes") or [] if this_raw else []
    changes_html = "".join(
        f'<li><b>{e(str(c.get("date", "")))}</b> {e(str(c.get("was") or c.get("from") or ""))} → '
        f'{e(this_plan.get(dt.date.fromisoformat(str(c.get("date"))), {}).get("name", str(c.get("to", ""))))}'
        f'<div class="muted">{e(" ".join(str(c.get("why", "")).split()))}</div></li>' for c in changes
    ) or '<li class="muted">правок нет</li>'
    journal = latest_journal()
    journal_html = (md_block(journal[1]) if journal[1].lstrip().startswith("# ")
                    else f"<h3>{e(journal[0])}</h3>{md_block(journal[1])}") if journal else '<p class="muted">журнал пуст</p>'

    week_end = start + dt.timedelta(days=6)
    stamp = f"данные Garmin по {data_until:%d.%m}" if data_until else "данных Garmin нет — запусти pull"
    body = f"""
<header><h1>Бег · {this_id}</h1><p class="muted">{start:%d.%m}–{week_end:%d.%m} · собрано {today:%d.%m.%Y} · {stamp}</p></header>
<section class="tiles">{"".join(tiles)}</section>
<section><h2>Неделя: план и факт</h2>{day_cards(start, this_plan, state, sessions_by_day, data_until, today, easy_max)}</section>
<section><h2>Объём по неделям, км</h2>
<div class="legend"><span><i class="sw fact"></i>факт</span><span><i class="sw plan"></i>план</span></div>
<div class="scroll">{volume_chart(vol_rows)}</div></section>
<section><h2>Восстановление, 28 дней</h2><div class="sparks">{"".join(sparks)}</div>
<p class="muted">Серый коридор у HRV — норма Garmin, пунктир — порог сигнала усталости.</p></section>
<section class="two"><div><h2>Фаза</h2>{phase_html}<h2>Правки недели</h2><ul class="changes">{changes_html}</ul></div>
<div class="journal"><h2>Последний разбор</h2>{journal_html}</div></section>
"""
    return PAGE.replace("{{BODY}}", body)


def tile(label: str, value: str, sub: str) -> str:
    return f'<div class="tile"><div class="label">{e(label)}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'


def write(today: dt.date | None = None, out: Path = OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(today), encoding="utf-8")
    return out


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Бег · дашборд</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--band:#f0efec;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--band:#2a2a28}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:15px;margin:0 0 12px;color:var(--ink2);font-weight:600}
h3{font-size:14px;margin:0 0 8px}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:16px}
header{padding:0 4px}.muted{color:var(--muted);font-size:13px}p{margin:0 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;background:none;border:0;padding:0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px}
.tile .label{color:var(--ink2);font-size:13px}.tile .value{font-size:30px;font-weight:600;margin:2px 0}
.tile .value small{font-size:15px;color:var(--ink2);font-weight:400}.tile .sub{font-size:13px;color:var(--ink2)}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--ink2)}
.badge i{flex:none}
.badge i{font-style:normal;font-weight:700;width:16px;height:16px;border-radius:50%;display:inline-grid;place-items:center;
font-size:11px;color:#fff;background:var(--muted)}
.badge.good i{background:var(--good)}.badge.warning i{background:var(--warning);color:#0b0b0b}
.badge.serious i{background:var(--serious);color:#0b0b0b}.badge.info i{background:var(--s1)}
.days{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.day{border:1px solid var(--border);border-radius:10px;padding:10px;min-height:96px;font-size:13px}
.day.today{outline:2px solid var(--s1);outline-offset:-1px}
.day-head{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap}
.plan-line{margin-bottom:4px}.fact-line{color:var(--ink2);font-variant-numeric:tabular-nums}
@media (max-width:820px){.days{grid-template-columns:1fr}.day{min-height:0}}
svg{width:100%;height:auto;display:block;overflow:visible}
.scroll{overflow-x:auto}.scroll svg{min-width:560px}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.tick.cur{fill:var(--ink);font-weight:600}
.val{fill:var(--ink2);font-size:12px;font-weight:600}
.fact{fill:var(--s1)}.plan{fill:none;stroke:var(--ink2);stroke-width:2;stroke-dasharray:4 3}
.hit{fill:transparent;cursor:default}.hit:hover{fill:var(--ink);fill-opacity:.04}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin:-4px 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block}.sw.fact{background:var(--s1)}
.sw.plan{border:2px dashed var(--ink2)}
.sparks{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.spark{margin:0}.spark figcaption{font-size:13px;color:var(--ink2);margin-bottom:4px}.spark figcaption b{color:var(--ink);font-size:16px}
.line{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round}
.dot{fill:var(--s1)}.dot.last{stroke:var(--surface);stroke-width:2}
.band{fill:var(--band)}.ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3}
.two{display:grid;grid-template-columns:1fr 1.4fr;gap:24px}
@media (max-width:820px){.two{grid-template-columns:1fr}}
.changes{margin:0;padding-left:18px;font-size:13px}.changes li{margin-bottom:8px}
.journal{font-size:13px;color:var(--ink2)}.journal strong{color:var(--ink)}
code{font-size:12px;background:var(--band);padding:0 4px;border-radius:4px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:12px;padding:4px 8px;
border-radius:6px;opacity:0;transition:opacity .1s;white-space:nowrap;z-index:10}
</style></head>
<body><main>{{BODY}}</main><div id="tip" role="tooltip"></div>
<script>
const tip=document.getElementById('tip');
document.addEventListener('pointermove',ev=>{const t=ev.target.closest('[data-tip]');
if(!t){tip.style.opacity=0;return}tip.textContent=t.dataset.tip;tip.style.opacity=1;
const x=Math.min(ev.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(ev.clientY-32)+'px'});
</script></body></html>
"""
