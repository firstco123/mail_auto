"""
Guards against describing the wrong flat to a customer.

Three ways the matcher could pick a property it should not, each raised by the
client while verifying the 貴社物件コード rollout:

  1. A 貴社物件コード typed against the wrong post. The code is otherwise
     trusted absolutely, so nothing downstream would question it.
  2. Two different buildings sharing a formal name. The attribute matcher
     already refuses a tie; the formal-name path picked one by load order.
  3. A follow-up suggesting a room that has been taken since the snapshot.
     The first mail re-checks each suggestion; the follow-up, sent days later
     from a staler view, did not.
"""
import unittest
from unittest.mock import MagicMock

from src.core.models import Inquiry, Property
from src.core.inquiry_processor import InquiryProcessor
from src.core.reflection import identify
from src.matching import portal_matcher

BODY = """\
問合せ物件
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
お問合せ内容:見学したい
"""


def _raw(code=""):
    return {"from_addr": "system@jds.suumo.jp",
            "subject": "[リクルートJDS]反響お知らせメール",
            "body": BODY.format(code=code), "message_id": "<a@jds>",
            "in_reply_to": "", "date": None, "uid": "1"}


def _prop(wp_id=48329, *, name="T-WEST", room="302", rent=66000,
          layout="1K", area=30.03, city="豊田市", address="愛知県豊田市西町2"):
    return Property(
        wp_id=wp_id, name=name, url="https://rentmagazine.jp/%d/" % wp_id,
        rent=rent, management_fee=3000, layout=layout, nearest_station="豊田市",
        train_line="名鉄三河線", city=city, walk_minutes=4, category=[],
        equipment=[], is_vacant=True, is_commission_free=False, area_sqm=area,
        building_type="マンション", building_name=name, room_number=room,
        address=address)


class TestAMisregisteredCodeIsCaught(unittest.TestCase):
    def test_a_listing_that_agrees_is_accepted(self):
        wrong, _ = portal_matcher.contradicts(identify(_raw("48329")), _prop())
        self.assertFalse(wrong)

    def test_a_completely_different_flat_is_rejected(self):
        """Another building: 3LDK, 75㎡, ¥128,000, room 101."""
        other = _prop(999, name="別館", room="101", rent=128000,
                      layout="3LDK", area=75.0)
        wrong, checked = portal_matcher.contradicts(identify(_raw("48329")), other)
        self.assertTrue(wrong)
        self.assertIn("間取り", checked)

    def test_one_agreeing_attribute_is_enough_to_accept(self):
        """A near miss is not evidence of a wrong code — only a total one is."""
        near = _prop(999, name="別館", room="101", rent=128000,
                     layout="3LDK", area=30.03)       # area still agrees
        wrong, _ = portal_matcher.contradicts(identify(_raw("48329")), near)
        self.assertFalse(wrong)

    def test_a_sparse_listing_is_not_treated_as_wrong(self):
        """Too little recorded to compare is not the same as disagreeing."""
        sparse = _prop(999, room="", rent=0, layout="", area=0.0)
        wrong, _ = portal_matcher.contradicts(identify(_raw("48329")), sparse)
        self.assertFalse(wrong)

    def test_a_name_in_a_different_script_is_not_a_contradiction(self):
        """The site writes romaji where SUUMO writes katakana, routinely."""
        romaji = _prop(name="bonitorosa Ⅰ・Ⅱ")
        wrong, _ = portal_matcher.contradicts(identify(_raw("48329")), romaji)
        self.assertFalse(wrong)


class TestTheInquiryIsHeldRatherThanMisdescribed(unittest.TestCase):
    def _processor(self, by_id):
        wp = MagicMock()
        wp.get_property_by_post_id.return_value = by_id
        wp.get_property_by_url.return_value = None
        wp.resolve_by_formal_name.return_value = (_prop(999, name="名前一致"), "")
        wp.get_property_by_name.return_value = None
        wp.refetch_one.side_effect = lambda wid: by_id
        wp.properties = []
        sheets = MagicMock()
        proc = InquiryProcessor(
            gmail=MagicMock(), sheets=sheets, wp=wp, checker=MagicMock(),
            generator=MagicMock(), scorer=MagicMock(), gate=MagicMock(),
            company={"staff_name": "担当"}, followup_cfg={})
        return proc, sheets

    def _inquiry(self):
        return Inquiry(id="i", received_at=None, customer_name="堀井里菜",
                       customer_email="a@b.c", inquiry_property_name="T-WEST302",
                       inquiry_property_url="", raw_body="", message_id="<a@jds>")

    def test_a_contradicting_code_yields_no_property(self):
        bad = _prop(999, name="別館", room="101", rent=128000, layout="3LDK", area=75.0)
        proc, _ = self._processor(bad)
        prop, vacancy = proc._lookup_property(self._inquiry(), identify(_raw("48329")))
        self.assertIsNone(prop)
        self.assertIsNone(vacancy)

    def test_it_is_marked_for_review_with_a_reason(self):
        bad = _prop(999, name="別館", room="101", rent=128000, layout="3LDK", area=75.0)
        proc, sheets = self._processor(bad)
        proc._lookup_property(self._inquiry(), identify(_raw("48329")))
        self.assertIn("要確認",
                      [c[0][1] for c in sheets.update_status.call_args_list])
        reason = sheets.write_review_log.call_args[0][1]
        self.assertIn("48329", reason)

    def test_the_send_gate_actually_blocks_it(self):
        """Flagging the sheet is not enough — the gate reads inquiry.status."""
        from src.email_builder.send_gate import SendGate, GATE_BLOCKED
        bad = _prop(999, name="別館", room="101", rent=128000, layout="3LDK", area=75.0)
        proc, _ = self._processor(bad)
        inquiry = self._inquiry()
        proc._lookup_property(inquiry, identify(_raw("48329")))
        clean = MagicMock(is_clean=True)
        self.assertEqual(
            SendGate(global_require_confirmation=False).evaluate(
                inquiry, clean, clean, {"自動送信有効": True}),
            GATE_BLOCKED)

    def test_a_correct_code_is_not_flagged(self):
        proc, sheets = self._processor(_prop())
        prop, _ = proc._lookup_property(self._inquiry(), identify(_raw("48329")))
        self.assertIsNotNone(prop)
        sheets.write_review_log.assert_not_called()


class TestSameNameBuildingsAreNotGuessed(unittest.TestCase):
    def _wp(self, props):
        from src.integrations.wp_client import WordPressClient
        wp = WordPressClient.__new__(WordPressClient)
        wp._properties = props
        return wp

    def test_rooms_of_one_building_still_match(self):
        """The ordinary case: several rooms, one address."""
        wp = self._wp([_prop(1, name="オリーブ_201", room="201"),
                       _prop(2, name="オリーブ_205", room="205")])
        prop, display = wp.resolve_by_formal_name("オリーブ")
        self.assertIsNotNone(prop)
        self.assertEqual(display, "オリーブ")

    def test_a_room_level_match_is_unaffected(self):
        wp = self._wp([_prop(1, name="オリーブ_201", room="201"),
                       _prop(2, name="オリーブ_205", room="205")])
        prop, _ = wp.resolve_by_formal_name("オリーブ205")
        self.assertEqual(prop.wp_id, 2)

    def test_two_buildings_in_different_cities_are_refused(self):
        wp = self._wp([
            _prop(1, name="オリーブ_201", room="201",
                  city="安城市", address="愛知県安城市東新町1"),
            _prop(2, name="オリーブ_301", room="301",
                  city="岡崎市", address="愛知県岡崎市明大寺町2"),
        ])
        prop, display = wp.resolve_by_formal_name("オリーブ")
        self.assertIsNone(prop)
        self.assertEqual(display, "")

    def test_a_confirmed_room_still_wins_over_the_ambiguity(self):
        """The room number settles which building it is, so answer normally."""
        wp = self._wp([
            _prop(1, name="オリーブ_201", room="201",
                  city="安城市", address="愛知県安城市東新町1"),
            _prop(2, name="オリーブ_301", room="301",
                  city="岡崎市", address="愛知県岡崎市明大寺町2"),
        ])
        prop, _ = wp.resolve_by_formal_name("オリーブ301")
        self.assertEqual(prop.wp_id, 2)


class TestFollowupSuggestionsAreRechecked(unittest.TestCase):
    def _scheduler(self, alts, vacant_now):
        from src.scheduling.followup_scheduler import FollowupScheduler
        sched = FollowupScheduler.__new__(FollowupScheduler)
        sched._scorer = MagicMock()
        sched._scorer.find_alternatives.return_value = alts
        sched._generator = MagicMock()
        sched._generator.generate_alt_intro.return_value = "紹介文"
        sched._generator.generate_visit_invitation.return_value = ""
        sched._wp = MagicMock()

        def refetch(wp_id):
            fresh = _prop(wp_id, name=str(wp_id))
            fresh.is_vacant = vacant_now.get(wp_id, True)
            return fresh

        sched._wp.refetch_one.side_effect = refetch
        return sched

    def test_a_room_taken_since_the_snapshot_is_dropped(self):
        alts = [_prop(1, name="A"), _prop(2, name="B"), _prop(3, name="C")]
        sched = self._scheduler(alts, {1: True, 2: False, 3: True})
        _, _, alt_intros = sched._build_segments(
            Inquiry(id="i", received_at=None, customer_name="x",
                    customer_email="a@b.c", inquiry_property_name="",
                    inquiry_property_url="", raw_body="", message_id=""),
            None)
        self.assertEqual([p.wp_id for p, _ in alt_intros], [1, 3])

    def test_an_unreadable_listing_is_kept(self):
        """A WordPress hiccup must not empty the suggestions."""
        alts = [_prop(1, name="A")]
        from src.scheduling.followup_scheduler import FollowupScheduler
        sched = FollowupScheduler.__new__(FollowupScheduler)
        sched._wp = MagicMock()
        sched._wp.refetch_one.side_effect = RuntimeError("wp down")
        self.assertTrue(sched._still_vacant(alts[0]))




if __name__ == "__main__":
    unittest.main()
