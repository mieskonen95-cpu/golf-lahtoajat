import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

from teetimes import alerts
from teetimes.search import CourseOption, Match, SearchResult

NOW = datetime(2026, 6, 4, 12, 0)


def match(hhmm, club_id=134):
    h, m = map(int, hhmm.split(":"))
    return Match(club="Tapiola Golf", club_id=club_id, tee_id=1, tee="",
                 starts=[datetime(2026, 6, 6, h, m)], free=[4],
                 options=[CourseOption("18r", 18, 80.0)], handicap_limit=None,
                 notes=[], site_url="")


def fake_execute(*results):
    """query.execute returning the given match lists in turn."""
    it = iter(results)

    def run(q):
        matches, errors = next(it)
        return date.fromisoformat(q.get("date", "2026-06-06")), \
            SearchResult(matches=matches, errors=errors)
    return run


def by_day(table):
    """query.execute returning matches per date: {"2026-06-06": [...]}."""
    def run(q):
        return date.fromisoformat(q["date"]), SearchResult(matches=table.get(q["date"], []))
    return run


WATCH = {"id": "a1", "label": "la 6.6.", "query": {"date": "2026-06-06", "players": "4",
                                                   "clubs": "134"}, "baseline": []}


class CheckTest(unittest.TestCase):
    def check(self, results, state=None, watch=WATCH):
        sent = []
        with mock.patch("teetimes.query.execute", fake_execute(*results)):
            new = alerts.check_all([watch], state or {}, NOW,
                                   lambda t, m, c: sent.append((t, m, c)), "https://app")
        return new, sent

    def test_notifies_when_tee_time_appears(self):
        state, sent = self.check([([], [])])
        self.assertEqual(sent, [])
        state, sent = self.check([([match("08:10")], [])], state)
        self.assertEqual(len(sent), 1)
        self.assertIn("08:10 Tapiola Golf", sent[0][1])
        self.assertTrue(sent[0][2].startswith("https://app/?date=2026-06-06"))
        # Same result again: no repeat.
        state, sent = self.check([([match("08:10")], [])], state)
        self.assertEqual(sent, [])

    def test_baseline_suppresses_known_times(self):
        watch = dict(WATCH, baseline=["134:1:08:10"])
        _, sent = self.check([([match("08:10")], [])], watch=watch)
        self.assertEqual(sent, [])

    def test_disappear_and_reappear_notifies_again(self):
        state, _ = self.check([([match("08:10")], [])])
        state, _ = self.check([([], [])], state)
        _, sent = self.check([([match("08:10")], [])], state)
        self.assertEqual(len(sent), 1)

    def test_read_errors_do_not_forget_seen_times(self):
        state, _ = self.check([([match("08:10")], [])])
        state, _ = self.check([([], [("Tapiola Golf", "HTTP 429")])], state)
        _, sent = self.check([([match("08:10")], [])], state)
        self.assertEqual(sent, [])

    def test_failed_push_is_retried(self):
        def boom(*a):
            raise OSError("HTTP 429")
        with mock.patch("teetimes.query.execute", fake_execute(([match("08:10")], []))):
            state = alerts.check_all([WATCH], {}, NOW, boom, "https://app")
        self.assertIn("ilmoitus epäonnistui", state["a1"]["error"])
        _, sent = self.check([([match("08:10")], [])], state)
        self.assertEqual(len(sent), 1)

    def test_past_alerts_skipped(self):
        watch = dict(WATCH, query=dict(WATCH["query"], date="2026-06-01"))
        state, sent = self.check([], watch=watch)
        self.assertEqual((state, sent), ({}, []))


REPEAT = {"id": "r1", "label": "la+su", "initialized": False,
          "query": {"players": "4", "clubs": "134"},
          "repeat": {"days": [5, 6], "start": "2026-06-01", "end": "2026-08-31"}}


class RecurringTest(unittest.TestCase):
    def run_check(self, table, state, now=NOW):
        sent = []
        with mock.patch("teetimes.query.execute", by_day(table)):
            new = alerts.check_all([REPEAT], state, now,
                                   lambda t, m, c: sent.append((t, m, c)), "https://app")
        return new, sent

    def test_days_within_horizon(self):
        days = [d.isoformat() for d in alerts.dates_for(REPEAT, NOW.date())]
        # Thursday 4.6. + 14 days -> weekends 6-7.6., 13-14.6., not 20.6.
        self.assertEqual(days, ["2026-06-06", "2026-06-07", "2026-06-13", "2026-06-14"])
        self.assertEqual(alerts.dates_for(REPEAT, date(2026, 9, 1)), [])

    def test_first_run_silent_then_opening_and_freed(self):
        table = {"2026-06-06": [match("08:10")]}
        state, sent = self.run_check(table, {})
        self.assertEqual(sent, [])
        self.assertTrue(state["r1"]["initialized"])
        # Next week: 20.6. enters the window with free times -> booking opened.
        later = datetime(2026, 6, 6, 9, 0)
        table = {"2026-06-06": [match("08:10")], "2026-06-20": [match("10:00")]}
        state, sent = self.run_check(table, state, later)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0][0].startswith("Varattavissa:"))
        self.assertIn("la 20.6. 10:00", sent[0][1])
        self.assertIn("date=2026-06-20", sent[0][2])
        # A cancellation on a day already known -> "Vapautui".
        table["2026-06-06"] = [match("08:10"), match("09:30")]
        _, sent = self.run_check(table, state, later)
        self.assertTrue(sent[0][0].startswith("Vapautui:"))
        self.assertIn("la 6.6. 09:30", sent[0][1])

    def test_validation(self):
        with self.assertRaises(alerts.AlertError):
            alerts.new_alert({"players": "4", "clubs": "134"}, NOW,
                             {"days": [], "start": "2026-06-01", "end": "2026-08-31"})
        with self.assertRaises(alerts.AlertError):
            alerts.new_alert({"players": "4", "clubs": "134"}, NOW,
                             {"days": [5], "start": "2026-06-01", "end": "2026-05-01"})
        a, _ = alerts.new_alert({"date": "2026-06-06", "players": "4", "clubs": "134"}, NOW,
                                {"days": [5, 6], "start": "2027-05-01", "end": "2027-09-30"})
        self.assertNotIn("date", a["query"])
        self.assertTrue(a["label"].startswith("la+su 1.5.–30.9.2027"))

    def test_expires_after_end(self):
        self.assertFalse(alerts._expired(REPEAT, datetime(2026, 8, 31, 12)))
        self.assertTrue(alerts._expired(REPEAT, datetime(2026, 9, 1, 12)))

    def test_old_keys_without_date_still_known(self):
        w = dict(WATCH, baseline=["134:1:08:10"])
        sent = []
        with mock.patch("teetimes.query.execute", fake_execute(([match("08:10")], []))):
            alerts.check_all([w], {}, NOW, lambda *a: sent.append(a), "https://app")
        self.assertEqual(sent, [])


class CheckerTest(unittest.TestCase):
    def test_missing_topic_fails_without_touching_state(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=True):
            store = alerts.LocalStore(d)
            store.write(alerts.WATCHES_PATH, {"watches": [WATCH]}, None, "")
            with self.assertRaises(SystemExit):
                alerts.run_checker(d, NOW)
            self.assertEqual(store.read(alerts.STATE_PATH)[0], {})


class StoreTest(unittest.TestCase):
    def test_add_list_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = alerts.LocalStore(d)
            with mock.patch("teetimes.query.execute", fake_execute(([match("09:00")], []))):
                a = alerts.add_alert(store, {"date": "2026-06-06", "players": "8",
                                             "clubs": "134,41"}, NOW)
            self.assertEqual(a["baseline"], ["2026-06-06|134:1:09:00"])
            self.assertIn("8 pelaajaa", a["label"])
            self.assertEqual([x["id"] for x in alerts.list_alerts(store, NOW)], [a["id"]])
            alerts.delete_alert(store, a["id"], NOW)
            self.assertEqual(alerts.list_alerts(store, NOW), [])

    def test_weekday_is_frozen_to_a_date(self):
        alert, _ = alerts.new_alert({"date": "la", "players": "4", "clubs": "134"}, NOW)
        self.assertRegex(alert["query"]["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_alert_must_be_limited_to_some_clubs(self):
        with self.assertRaises(alerts.AlertError):
            alerts.new_alert({"date": "2026-06-06", "players": "4"}, NOW)
        alerts.new_alert({"date": "2026-06-06", "players": "4", "near": "40"}, NOW)

    def test_past_date_rejected(self):
        with self.assertRaises(alerts.AlertError):
            alerts.new_alert({"date": "2026-06-01", "players": "4", "clubs": "134"}, NOW)


if __name__ == "__main__":
    unittest.main()
