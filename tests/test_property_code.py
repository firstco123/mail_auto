"""
SUUMO's 貴社物件コード identifies the listing outright.

Matching by name is inherently uncertain: portals send katakana while half the
site's buildings are romaji, and two buildings can share a name. The operator
can remove that uncertainty by typing the WordPress post ID into SUUMO's
貴社物件コード field, which SUUMO echoes back in every 反響.

The field is echoed whether or not it is filled, so a blank must mean "not
registered" and leave the existing matching untouched — registration is being
rolled out listing by listing, so most 反響 will not carry one for a while.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.core.models import Inquiry, Property
from src.core.reflection import identify

BODY = """\
問合せ物件
会社名:レントマガジン(株)
物件種別:マンション
物件コード:100536371217
物件名:T-WEST302
貴社物件コード:{code}
最寄り駅:名鉄三河線/豊田市
所在地:愛知県豊田市西町2
賃料:6.6万円
間取り:1K
専有面積:30.03平米
お客様プロフィール
名前(漢字):堀井里菜
メールアドレス:rina@example.com
お問合せ内容:この部屋を見学したい
"""


def _raw(code=""):
    return {"from_addr": "system@jds.suumo.jp",
            "subject": "[リクルートJDS]反響お知らせメール",
            "body": BODY.format(code=code), "message_id": "<a@jds>",
            "in_reply_to": "", "date": None, "uid": "1"}


def _prop(wp_id, name):
    return Property(
        wp_id=wp_id, name=name, url="https://rentmagazine.jp/%d/" % wp_id,
        rent=66000, management_fee=3000, layout="1K",
        nearest_station="豊田市", train_line="名鉄三河線", city="豊田市",
        walk_minutes=4, category=[], equipment=[], is_vacant=True,
        is_commission_free=False, area_sqm=30.03, building_type="マンション",
        building_name=name, room_number="302", address="愛知県豊田市西町2")


class TestTheFieldIsRead(unittest.TestCase):
    def test_a_registered_code_is_picked_up(self):
        self.assertEqual(identify(_raw("48329")).site_post_id, "48329")

    def test_a_blank_field_means_not_registered(self):
        self.assertEqual(identify(_raw("")).site_post_id, "")

    def test_a_non_numeric_entry_is_ignored(self):
        """An operator note in the field must not be used as an ID."""
        self.assertEqual(identify(_raw("未登録")).site_post_id, "")

    def test_a_suumo_style_code_is_not_mistaken_for_a_post_id(self):
        """SUUMO's own 物件コード is 12 digits and lives in a different field."""
        ref = identify(_raw(""))
        self.assertEqual(ref.site_post_id, "")
        self.assertEqual(ref.property_name, "T-WEST302")

    def test_the_other_fields_still_parse(self):
        ref = identify(_raw("48329"))
        self.assertEqual(ref.customer_name, "堀井里菜")
        self.assertEqual(ref.property_name, "T-WEST302")
        self.assertEqual(ref.extras["所在地"], "愛知県豊田市西町2")


class TestLookupByPostId(unittest.TestCase):
    def _wp(self, props):
        from src.integrations.wp_client import WordPressClient
        wp = WordPressClient.__new__(WordPressClient)
        wp._properties = props
        return wp

    def test_the_matching_listing_is_returned(self):
        wp = self._wp([_prop(111, "A"), _prop(48329, "T-WEST")])
        self.assertEqual(wp.get_property_by_post_id("48329").wp_id, 48329)

    def test_an_unknown_id_returns_nothing(self):
        wp = self._wp([_prop(111, "A")])
        self.assertIsNone(wp.get_property_by_post_id("48329"))

    def test_rubbish_returns_nothing_rather_than_raising(self):
        wp = self._wp([_prop(111, "A")])
        for value in ("", "abc", None, "12.5"):
            self.assertIsNone(wp.get_property_by_post_id(value))


class TestMatchingOrder(unittest.TestCase):
    """The code wins when present; everything else is unchanged without it."""

    def _processor(self, wp):
        return InquiryProcessor(
            gmail=MagicMock(), sheets=MagicMock(), wp=wp, checker=MagicMock(),
            generator=MagicMock(), scorer=MagicMock(), gate=MagicMock(),
            company={"staff_name": "担当"}, followup_cfg={})

    def _wp_mock(self, by_id=None, by_name=None):
        wp = MagicMock()
        wp.get_property_by_post_id.return_value = by_id
        wp.get_property_by_url.return_value = None
        wp.resolve_by_formal_name.return_value = (by_name, "")
        wp.get_property_by_name.return_value = None
        wp.properties = []
        # The live vacancy re-check runs on whatever was matched; return the
        # same listing so these tests observe the matching, not the refetch.
        wp.refetch_one.side_effect = lambda wid: next(
            (p for p in (by_id, by_name) if p is not None and p.wp_id == wid), None)
        return wp

    def _inquiry(self):
        return Inquiry(id="i", received_at=None, customer_name="堀井里菜",
                       customer_email="a@b.c", inquiry_property_name="T-WEST302",
                       inquiry_property_url="", raw_body="", message_id="<a@jds>")

    def test_the_code_decides_when_it_matches(self):
        wanted, other = _prop(48329, "T-WEST"), _prop(999, "別の建物")
        wp = self._wp_mock(by_id=wanted, by_name=other)
        prop, _ = self._processor(wp)._lookup_property(
            self._inquiry(), identify(_raw("48329")))
        self.assertIs(prop, wanted)
        wp.resolve_by_formal_name.assert_not_called()

    def test_name_matching_still_runs_when_no_code_is_registered(self):
        by_name = _prop(999, "T-WEST")
        wp = self._wp_mock(by_name=by_name)
        prop, _ = self._processor(wp)._lookup_property(
            self._inquiry(), identify(_raw("")))
        self.assertIs(prop, by_name)
        wp.get_property_by_post_id.assert_not_called()

    def test_an_unmatched_code_falls_back_rather_than_failing(self):
        """A listing may be unpublished, or the code mistyped — still reply."""
        by_name = _prop(999, "T-WEST")
        wp = self._wp_mock(by_id=None, by_name=by_name)
        prop, _ = self._processor(wp)._lookup_property(
            self._inquiry(), identify(_raw("48329")))
        self.assertIs(prop, by_name)

    def test_a_webform_inquiry_is_unaffected(self):
        wp = self._wp_mock()
        self._processor(wp)._lookup_property(self._inquiry(), None)
        wp.get_property_by_post_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()
