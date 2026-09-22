import datetime as dt

from garmin_run.signals import fatigue_signals

DAY = dt.date(2026, 9, 20)


def day(hrv_status="BALANCED", night=90, sleep_h=8.0, rhr=45, bb=90):
    return {
        "hrv": {"hrvSummary": {"status": hrv_status, "lastNightAvg": night, "baseline": {"balancedLow": 81}}},
        "sleep": {"dailySleepDTO": {"sleepTimeSeconds": sleep_h * 3600, "sleepScores": {"overall": {"value": 80}}}},
        "summary": {"restingHeartRate": rhr, "bodyBatteryAtWakeTime": bb},
        "readiness": [],
    }


def week(**today):
    daily = {DAY - dt.timedelta(days=i): day() for i in range(1, 8)}
    daily[DAY] = day(**today)
    return daily


def test_clean_day_has_no_signals():
    assert fatigue_signals(DAY, week(), []) == []


def test_hrv_needs_two_nights():
    assert fatigue_signals(DAY, week(hrv_status="UNBALANCED"), []) == []
    daily = week(hrv_status="UNBALANCED")
    daily[DAY - dt.timedelta(days=1)] = day(night=70)  # статус ровный, но ниже нормы
    assert fatigue_signals(DAY, daily, []) == ["HRV UNBALANCED две ночи подряд"]


def test_sleep_rhr_bb_and_hard_yesterday():
    hard = {"startTimeLocal": "2026-09-19 08:00:00", "aerobicTrainingEffect": 4.2, "anaerobicTrainingEffect": 1.0}
    got = fatigue_signals(DAY, week(sleep_h=5.5, rhr=51, bb=30), [hard])
    assert got == ["сон 5.5 ч", "пульс покоя 51 при среднем 45", "Body Battery утром 30", "вчера TE 4.2/1.0"]
