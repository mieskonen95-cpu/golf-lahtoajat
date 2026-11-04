import unittest
from datetime import datetime

from teetimes.availability import TeeTime
from teetimes.search import CourseOption, Match, find_group_slots, merge_shared_tees, split_group


def tt(hhmm, free, rid=1, cap=4):
    h, m = map(int, hhmm.split(":"))
    return TeeTime(start=datetime(2026, 6, 6, h, m), resource_id=rid, resource_name="",
                   capacity=cap, free=free, price=80.0)


def starts(runs):
    return [[t.start.strftime("%H:%M") for t in r] for r in runs]


class GroupTest(unittest.TestCase):
    def test_split_group(self):
        self.assertEqual(split_group(2, 4), [2])
        self.assertEqual(split_group(4, 4), [4])
        self.assertEqual(split_group(8, 4), [4, 4])
        self.assertEqual(split_group(6, 4), [3, 3])
        self.assertEqual(split_group(5, 4), [3, 2])

    def test_two_players_need_two_places(self):
        times = [tt("08:00", 1), tt("08:10", 2), tt("08:20", 4)]
        self.assertEqual(starts(find_group_slots(times, 2)), [["08:10"], ["08:20"]])

    def test_four_players_need_empty_tee_time(self):
        times = [tt("08:00", 3), tt("08:10", 4)]
        self.assertEqual(starts(find_group_slots(times, 4)), [["08:10"]])

    def test_eight_players_need_consecutive_empty_times(self):
        times = [tt("08:00", 4), tt("08:10", 3), tt("08:20", 4), tt("08:30", 4), tt("08:50", 4)]
        self.assertEqual(starts(find_group_slots(times, 8)), [["08:20", "08:30"]])

    def test_max_gap_allows_a_hole(self):
        # 08:40 missing from the list (e.g. past or not offered)
        times = [tt("08:30", 4), tt("08:50", 4)]
        self.assertEqual(find_group_slots(times, 8, interval=10), [])
        self.assertEqual(starts(find_group_slots(times, 8, max_gap=20, interval=10)),
                         [["08:30", "08:50"]])

    def test_runs_do_not_mix_tees(self):
        times = [tt("08:00", 4, rid=1), tt("08:10", 4, rid=2)]
        self.assertEqual(find_group_slots(times, 8), [])

    def test_merge_9_and_18_on_same_tee(self):
        def m(course, holes, price, free):
            return Match(club="Tapiola Golf", tee_id=1, tee="", starts=[datetime(2026, 6, 6, 8)],
                         free=[free], options=[CourseOption(course, holes, price)],
                         handicap_limit=None, notes=[], site_url="")
        out = merge_shared_tees([m("9r", 9, 47.0, 4), m("18r", 18, 80.0, 4)])
        self.assertEqual(len(out), 1)
        self.assertEqual([o.holes for o in out[0].options], [18, 9])


if __name__ == "__main__":
    unittest.main()
