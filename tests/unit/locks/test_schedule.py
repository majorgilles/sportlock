from datetime import date, datetime, time

from sportlock.locks.domain.schedule import decide, settings_frozen, windows_for_day
from sportlock.settings.domain.settings import LockEntry, Settings

MON = date(2026, 10, 5)


def _settings(*locks, cap=60, warn=(10, 2), enabled=True):
    return Settings(max_minutes_per_day=cap, warn_minutes=warn, locks=tuple(locks), enabled=enabled)


def _lock(at, minutes, days=range(7)):
    hours, mins = map(int, at.split(":"))
    return LockEntry(days=frozenset(days), at=time(hours, mins), minutes=minutes)


def _at(day, hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hours, mins))


def _decide(settings, now, trained=frozenset(), ended=frozenset()):
    return decide(settings, now, trained_days=set(trained), ended=set(ended))


SIX_PM = _settings(_lock("18:00", 30))


def test_windows_for_day__other_weekday__no_window():
    settings = _settings(_lock("18:00", 30, days=[1]))  # Tuesday
    assert windows_for_day(settings, MON) == []
    assert len(windows_for_day(settings, date(2026, 10, 6))) == 1


def test_windows_for_day__overlapping_locks__merge():
    [window] = windows_for_day(_settings(_lock("18:00", 30), _lock("18:20", 30), cap=120), MON)
    assert (window.start, window.end) == (_at(MON, "18:00"), _at(MON, "18:50"))


def test_windows_for_day__daily_cap__truncates_in_order():
    first, second = windows_for_day(_settings(_lock("09:00", 40), _lock("18:00", 40), cap=60), MON)
    assert (first.end, second.end) == (_at(MON, "09:40"), _at(MON, "18:20"))


def test_windows_for_day__cap_exhausted__drops_later_windows():
    assert len(windows_for_day(_settings(_lock("09:00", 60), _lock("18:00", 30), cap=60), MON)) == 1


def test_decide__inside_a_window__it_is_active():
    assert _decide(SIX_PM, _at(MON, "18:10")).active.start == _at(MON, "18:00")


def test_decide__boot_mid_window__locks_for_the_time_left():
    assert _decide(SIX_PM, _at(MON, "18:29")).active.end == _at(MON, "18:30")


def test_decide__after_the_window__next_is_tomorrow():
    decision = _decide(SIX_PM, _at(MON, "18:30"))
    assert decision.active is None
    assert decision.next.start == _at(date(2026, 10, 6), "18:00")


def test_decide__trained_today__window_skipped():
    assert _decide(SIX_PM, _at(MON, "18:10"), trained={MON}).active is None


def test_decide__window_ended_early__stays_ended():
    assert _decide(SIX_PM, _at(MON, "18:10"), ended={"2026-10-05T18:00"}).active is None


def test_decide__weekday_schedule_on_friday_night__next_lock_is_monday():
    settings = _settings(_lock("22:35", 20, days=range(5)))
    assert _decide(settings, _at(date(2026, 10, 2), "23:00")).next.start == _at(MON, "22:35")


def test_decide__window_past_midnight__still_active_after_midnight():
    settings = _settings(_lock("23:50", 30))
    assert _decide(settings, _at(date(2026, 10, 6), "00:10")).active.start == _at(MON, "23:50")


def test_decide__disabled__nothing_active():
    assert _decide(_settings(_lock("18:00", 30), enabled=False), _at(MON, "18:10")).active is None


def test_decide__before_a_lock__most_urgent_warning_crossed():
    cases = {"17:45": None, "17:50": 10, "17:57": 10, "17:58": 2, "17:59": 2}
    for now, expected in cases.items():
        assert _decide(SIX_PM, _at(MON, now)).warning == expected, now


def test_settings_frozen__from_ten_minutes_before_until_the_lock_ends():
    def frozen(now):
        return settings_frozen(_decide(SIX_PM, _at(MON, now)), _at(MON, now))

    assert (frozen("17:49"), frozen("17:50"), frozen("18:15"), frozen("18:30")) == (False, True, True, False)
