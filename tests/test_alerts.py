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
        return date(2026, 6, 6), SearchResult(matches=matches, errors=errors)
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
            self.assertEqual(a["baseline"], ["134:1:09:00"])
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
