import unittest
from datetime import date, datetime, time

from sportlock.config import Config, ConfigError, LockEntry, parse
from sportlock.schedule import config_frozen, decide, windows_for_day

MON = date(2026, 10, 5)


def cfg(*locks, cap=60, warn=(10, 2)):
    return Config(max_minutes_per_day=cap, warn_minutes=warn, locks=tuple(locks))


def lock(at, minutes, days=range(7)):
    hours, mins = map(int, at.split(":"))
    return LockEntry(frozenset(days), time(hours, mins), minutes)


def at(day, hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hours, mins))


class WindowsTest(unittest.TestCase):
    def test_only_matching_days(self):
        config = cfg(lock("18:00", 30, days=[1]))  # Tuesday
        self.assertEqual(windows_for_day(config, MON), [])
        self.assertEqual(len(windows_for_day(config, date(2026, 10, 6))), 1)

    def test_overlaps_merge(self):
        config = cfg(lock("18:00", 30), lock("18:20", 30), cap=120)
        [window] = windows_for_day(config, MON)
        self.assertEqual((window.start, window.end), (at(MON, "18:00"), at(MON, "18:50")))

    def test_daily_cap_truncates_in_order(self):
        config = cfg(lock("09:00", 40), lock("18:00", 40), cap=60)
        first, second = windows_for_day(config, MON)
        self.assertEqual(first.end, at(MON, "09:40"))
        self.assertEqual(second.end, at(MON, "18:20"))

    def test_cap_exhausted_drops_later_windows(self):
        config = cfg(lock("09:00", 60), lock("18:00", 30), cap=60)
        self.assertEqual(len(windows_for_day(config, MON)), 1)


class DecideTest(unittest.TestCase):
    config = cfg(lock("18:00", 30))

    def test_active_inside_window(self):
        d = decide(self.config, at(MON, "18:10"), trained_days=set(), ended=set())
        self.assertEqual(d.active.start, at(MON, "18:00"))

    def test_boot_mid_window_locks_for_remaining_time(self):
        d = decide(self.config, at(MON, "18:29"), trained_days=set(), ended=set())
        self.assertEqual(d.active.end, at(MON, "18:30"))

    def test_after_window_nothing_active(self):
        d = decide(self.config, at(MON, "18:30"), trained_days=set(), ended=set())
        self.assertIsNone(d.active)
        self.assertEqual(d.next.start, at(date(2026, 10, 6), "18:00"))

    def test_trained_today_skips(self):
        d = decide(self.config, at(MON, "18:10"), trained_days={MON}, ended=set())
        self.assertIsNone(d.active)

    def test_ended_window_stays_ended(self):
        d = decide(self.config, at(MON, "18:10"), trained_days=set(), ended={"2026-10-05T18:00"})
        self.assertIsNone(d.active)

    def test_next_lock_found_across_the_weekend(self):
        config = cfg(lock("22:35", 20, days=range(5)))  # Mon–Fri
        friday_night = at(date(2026, 10, 2), "23:00")
        d = decide(config, friday_night, trained_days=set(), ended=set())
        self.assertEqual(d.next.start, at(MON, "22:35"))

    def test_window_past_midnight(self):
        config = cfg(lock("23:50", 30))
        d = decide(config, at(date(2026, 10, 6), "00:10"), trained_days=set(), ended=set())
        self.assertEqual(d.active.start, at(MON, "23:50"))

    def test_disabled(self):
        config = Config(enabled=False, locks=(lock("18:00", 30),))
        self.assertIsNone(decide(config, at(MON, "18:10"), trained_days=set(), ended=set()).active)

    def test_warnings(self):
        cases = {"17:45": None, "17:50": 10, "17:57": 10, "17:58": 2, "17:59": 2}
        for now, expected in cases.items():
            d = decide(self.config, at(MON, now), trained_days=set(), ended=set())
            self.assertEqual(d.warning, expected, now)

    def test_freeze(self):
        def frozen(now):
            return config_frozen(decide(self.config, at(MON, now), trained_days=set(), ended=set()), at(MON, now))

        self.assertFalse(frozen("17:49"))
        self.assertTrue(frozen("17:50"))
        self.assertTrue(frozen("18:15"))
        self.assertFalse(frozen("18:30"))


class ConfigTest(unittest.TestCase):
    def test_parse(self):
        config = parse({"lock": [{"days": ["mon", "Friday"], "at": "07:30", "minutes": 20}]})
        self.assertEqual(config.locks[0].days, frozenset({0, 4}))
        self.assertEqual(config.locks[0].at, time(7, 30))

    def test_rejects_bad_values(self):
        for bad in (
            {"lock": [{"days": ["mon"], "at": "7h", "minutes": 20}]},
            {"lock": [{"days": ["noday"], "at": "07:00", "minutes": 20}]},
            {"lock": [{"days": ["mon"], "at": "07:00", "minutes": 0}]},
            {"override": {"phrase": "short"}},
        ):
            with self.assertRaises(ConfigError):
                parse(bad)


class SettingsFormTest(unittest.TestCase):
    def test_round_trip_through_form_and_file(self):
        import tomllib
        from sportlock.config import dump, from_settings, to_settings

        current = parse({"lock": [{"days": ["mon", "fri"], "at": "07:30", "minutes": 20}],
                         "override": {"phrase": 'say "yes" please, really'}})
        settings = to_settings(current)
        self.assertEqual(settings["locks"], [{"days": ["mon", "fri"], "at": "07:30", "minutes": 20}])
        settings["locks"].append({"days": ["sat", "sun"], "at": "10:00", "minutes": "45"})
        settings["enabled"] = True
        updated = from_settings(settings, current)
        again = parse(tomllib.loads(dump(updated)))
        self.assertEqual(again, updated)
        self.assertEqual(len(again.locks), 2)
        self.assertEqual(again.override_phrase, 'say "yes" please, really')

    def test_form_rejects_bad_values(self):
        from sportlock.config import from_settings, to_settings

        current = parse({})
        for change in ({"locks": [{"days": [], "at": "18:00", "minutes": 30}]},
                       {"locks": [{"days": ["mon"], "at": "25:00", "minutes": 30}]},
                       {"locks": [{"days": ["mon"], "at": "18:00", "minutes": "abc"}]},
                       {"locks": [{"days": ["mon"], "at": "18:00", "minutes": 500}]},
                       {"lead_in_seconds": 99}):
            with self.assertRaises(ConfigError):
                from_settings({**to_settings(current), **change}, current)


if __name__ == "__main__":
    unittest.main()
