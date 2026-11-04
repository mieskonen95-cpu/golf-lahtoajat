import unittest
from datetime import date, datetime

from teetimes.availability import (calendar_closed_reason, course_day, handicap_limit_for,
                                   handicap_limits, is_golf_calendar, rule_covers)

DAY = date(2026, 6, 6)  # Saturday
NOW = datetime(2026, 6, 4, 12, 0)  # Thursday noon, two days before


def settings(**kw):
    s = {
        "productId": 7, "productName": "Ajanvaraus 18r", "type": 5,
        "recurrenceDays": [True] * 7, "startDate": "2026-04-01", "endDate": "2026-10-31",
        "startTime": "08:00:00", "endTime": "09:00:00", "duration": 10, "breakTime": 0,
        "price": "80.00", "holes": "",
        "resources": [{"resourceId": 1, "resourceName": "Tee 1", "quantity": 4,
                       "resourceCategory": "golf18", "clubId": 134, "active": 1,
                       "priceRules": []}],
    }
    s.update(kw)
    return s


def rule(name, **kw):
    r = {"ruleId": 1, "resourceId": 1, "ruleName": name, "recurrenceDays": None,
         "startDate": None, "endDate": None, "startTime": None, "endTime": None,
         "ruleValue": None, "name": None, "categoryId": None, "isUserRule": True}
    r.update(kw)
    return r


def booking(start, qty, rid=1, n=1):
    end = start[:-5] + f"{int(start[-5:-3]) + 10:02d}:00"
    return {"reservationTimeId": n, "start": f"2026-06-06 {start}", "end": f"2026-06-06 {end}",
            "resources": [{"resourceId": rid, "quantity": qty}], "status": 2, "quantity": qty}


def run(s=None, rules=(), rows=(), now=NOW):
    cal = {"reservationSettings": s or settings(), "resourceRules": list(rules)}
    return course_day(cal, {"rows": list(rows)}, DAY, now)


class TeeSheetTest(unittest.TestCase):
    def test_empty_day_has_all_starts(self):
        cd = run()
        self.assertEqual([t.start.strftime("%H:%M") for t in cd.tee_times],
                         ["08:00", "08:10", "08:20", "08:30", "08:40", "08:50"])
        self.assertTrue(all(t.free == 4 for t in cd.tee_times))
        self.assertEqual(cd.tee_times[0].price, 80.0)

    def test_bookings_reduce_free_places(self):
        cd = run(rows=[booking("08:10:00", 1, n=1), booking("08:10:00", 2, n=2),
                       booking("08:20:00", 4, n=3)])
        free = {t.start.strftime("%H:%M"): t.free for t in cd.tee_times}
        self.assertEqual(free["08:10"], 1)
        self.assertEqual(free["08:20"], 0)
        self.assertEqual(free["08:00"], 4)

    def test_other_tee_bookings_ignored(self):
        cd = run(rows=[booking("08:10:00", 4, rid=2)])
        self.assertEqual(cd.tee_times[1].free, 4)

    def test_closure_rule(self):
        r = rule("aikaSulku", startDate="2026-06-06", endDate="2026-06-06",
                 startTime="08:20:00", endTime="08:40:00", ruleValue={"hide": False})
        free = {t.start.strftime("%H:%M"): t.free for t in run(rules=[r]).tee_times}
        self.assertEqual((free["08:10"], free["08:20"], free["08:30"], free["08:40"]), (4, 0, 0, 4))

    def test_closure_end_date_is_inclusive(self):
        r = rule("aikaSulku", startDate="2026-06-05", endDate="2026-06-06",
                 startTime="06:00:00", endTime="16:00:00")
        self.assertTrue(all(t.free == 0 for t in run(rules=[r]).tee_times))

    def test_closure_for_other_category_ignored(self):
        r = rule("aikaSulku", startTime="08:00:00", endTime="09:00:00",
                 categoryId=7, isUserRule=False)
        self.assertTrue(all(t.free == 4 for t in run(rules=[r]).tee_times))

    def test_hot_teetime_locked_until_release(self):
        r = rule("kuumatAjat", startTime="08:50:00", endTime="09:00:00",
                 ruleValue={"minutes": 1440}, isUserRule=None)
        t = run(rules=[r]).tee_times[-1]
        self.assertEqual((t.free, t.hot_minutes), (0, 1440))
        released = run(rules=[r], now=datetime(2026, 6, 5, 9, 0)).tee_times[-1]
        self.assertEqual(released.free, 4)

    def test_past_starts_dropped(self):
        cd = run(now=datetime(2026, 6, 6, 8, 25))
        self.assertEqual(cd.tee_times[0].start.strftime("%H:%M"), "08:30")

    def test_price_rule_weekday_monday_first(self):
        weekend = {"newPrice": "95.00", "dateStart": "2026-01-01", "dateEnd": "2026-12-31",
                   "startTime": "05:00:00", "endTime": "21:00:00",
                   "recurrenceDays": [False] * 5 + [True, True]}
        weekday = dict(weekend, newPrice="60.00", recurrenceDays=[True] * 5 + [False, False])
        s = settings()
        s["resources"][0]["priceRules"] = [weekday, weekend]
        self.assertEqual(run(s).tee_times[0].price, 95.0)

    def test_zero_price_is_unknown(self):
        self.assertIsNone(run(settings(price="0.00")).tee_times[0].price)

    def test_season_and_booking_window(self):
        s = settings(endDate="2026-05-31")
        self.assertIn("kausi päättyi", run(s).closed_reason)
        vis = rule("kalenteriNakyvyysPaivat", resourceId=None, ruleValue=1)
        cd = run(rules=[vis])
        self.assertFalse(cd.calendar_open)
        self.assertIn("aukeaa 1 pv", cd.closed_reason)

    def test_last_day_opens_at_local_time(self):
        rules = [rule("kalenteriNakyvyysPaivat", resourceId=None, ruleValue=2),
                 rule("kalenteriNakyvyysAvaus", resourceId=None, ruleValue={"localTime": "18:00"})]
        s = settings()
        self.assertIn("klo 18:00", calendar_closed_reason(s, rules, DAY, NOW))
        self.assertIsNone(calendar_closed_reason(s, rules, DAY, datetime(2026, 6, 4, 18, 0)))

    def test_handicap_limits(self):
        rules = [rule("tasoitusraja", ruleValue={"default": 100}),
                 rule("tasoitusraja", ruleValue={"groupSizes": {"1": 54, "2": 90}}),
                 rule("tasoitusraja", ruleValue={"default": 110}, categoryId=2, isUserRule=False)]
        limits = handicap_limits(rules, 1)
        self.assertEqual(handicap_limit_for(limits, 2), 90)
        self.assertEqual(handicap_limit_for(limits, 4), 100)

    def test_non_golf_calendars_skipped(self):
        sim = settings(duration=60)
        sim["resources"][0]["resourceCategory"] = None
        self.assertFalse(is_golf_calendar(sim))
        self.assertFalse(is_golf_calendar(settings(duration=1)))  # shotgun start
        self.assertTrue(is_golf_calendar(settings()))

    def test_rule_covers_overnight_window(self):
        r = {"startTime": "22:00:00", "endTime": "02:00:00"}
        self.assertTrue(rule_covers(r, datetime(2026, 6, 6, 23, 0), datetime(2026, 6, 6, 23, 10)))


if __name__ == "__main__":
    unittest.main()
