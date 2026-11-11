"""
A follow-up must identify the property the same way intake did.

Live fault (2026-10-30): the 2nd mail for 「グランデュール」 went out using the
空室あり wording while recommending *other* properties — a contradiction. The
scheduler looked the property up with get_property_by_name, which compares
against the post title (marketing copy such as 「自分のペースでひとり暮らし」), so it
found nothing. With no property it kept the vacancy recorded at intake two days
earlier and never re-read the listing, while the body builder took its
"no property" path and offered alternatives.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from src.core.models import Inquiry, Property
from src.scheduling.followup_scheduler import FollowupScheduler


def _prop(wp_id=1, vacant=True, building="グランデュール56_510",
          title="初めてのひとり暮らしにもおすすめ"):
    return Property(
        wp_id=wp_id, name=title, url=f"https://x/{wp_id}/", rent=60000,
        management_fee=0, layout="1K", nearest_station="", train_line="",
        city="", walk_minutes=0, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=25.0, building_type="",
        building_name=building, room_number="510", address="", access="")


def _inq(name="グランデュール", url="", vacant=True):
    return Inquiry(id="i", received_at=datetime(2026, 10, 28), customer_name="n",
                   customer_email="a@b.c", inquiry_property_name=name,
                   inquiry_property_url=url, raw_body="", is_vacant=vacant)


def _sched():
    s = FollowupScheduler.__new__(FollowupScheduler)
    s._wp = MagicMock()
    s._sheets = MagicMock()
    return s


class TestResolutionMatchesIntake(unittest.TestCase):
    def test_a_formal_name_resolves_where_a_title_match_cannot(self):
        s = _sched()
        s._wp.get_property_by_url.return_value = None
        s._wp.get_property_by_name.return_value = None        # title match fails
        s._wp.resolve_by_formal_name.return_value = (_prop(), "グランデュール")
        got = s._resolve_property(_inq())
        self.assertIsNotNone(got)
        self.assertEqual(got.building_name, "グランデュール56_510")

    def test_the_url_still_wins_when_present(self):
        s = _sched()
        s._wp.get_property_by_url.return_value = _prop(wp_id=9)
        got = s._resolve_property(_inq(url="https://x/9/"))
        self.assertEqual(got.wp_id, 9)
        s._wp.resolve_by_formal_name.assert_not_called()

    def test_the_fuzzy_match_remains_as_a_last_resort(self):
        s = _sched()
        s._wp.get_property_by_url.return_value = None
        s._wp.resolve_by_formal_name.return_value = (None, "")
        s._wp.get_property_by_name.return_value = _prop(wp_id=7)
        self.assertEqual(s._resolve_property(_inq()).wp_id, 7)

    def test_nothing_resolves_to_nothing(self):
        s = _sched()
        s._wp.get_property_by_url.return_value = None
        s._wp.resolve_by_formal_name.return_value = (None, "")
        s._wp.get_property_by_name.return_value = None
        self.assertIsNone(s._resolve_property(_inq()))

    def test_an_inquiry_with_no_property_name_resolves_to_nothing(self):
        s = _sched()
        self.assertIsNone(s._resolve_property(_inq(name="")))


class TestOptOutFitsAPhone(unittest.TestCase):
    """The hard break left a stray indent mid-sentence when re-wrapped."""

    def test_the_notice_is_a_single_line(self):
        from src.email_builder import followup_blocks as fb
        self.assertNotIn("\n", fb.OPT_OUT)

    def test_it_carries_no_leading_full_width_space(self):
        from src.email_builder import followup_blocks as fb
        self.assertNotIn("\n　", fb.OPT_OUT)
        self.assertIn("「配信停止」とお知らせください", fb.OPT_OUT)


if __name__ == "__main__":
    unittest.main()
