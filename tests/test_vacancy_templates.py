"""
Vacancy chooses the template, and the shared blocks are editable.

An available room and a gone one call for different mails, so each is edited
separately. The vacancy used is the one known at send time — for a follow-up
that is a live re-read moments earlier, not what was true at intake days ago
(client, 2026-10-27).

署名 and 配信停止案内 are inserted into every mail but were fixed in code, so
editing "the template" could not change them. They now come from the sheet, and
must apply to the built-in bodies too — otherwise editing the signature would
only work for someone who had also customised the body.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from src.core.models import Inquiry, Property
from src.email_builder import template_store as ts
from src.email_builder.assembler import EmailAssembler

CO = {"staff_name": "新家", "tel_reservation": "0566-70-8282",
      "discount_url": "https://x/c", "mypage_url": "https://x/m", "line_url": "https://x/l"}


def _prop(vacant=True):
    return Property(
        wp_id=1, name="物件", url="https://x/p/", rent=64000, management_fee=0,
        layout="1R", nearest_station="", train_line="名鉄本線", city="知立市",
        walk_minutes=20, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=50.0, building_type="",
        building_name="物件", room_number="107", address="",
        access='名鉄本線「牛田」徒歩20分')


def _inq(vacant=True):
    return Inquiry(id="i", received_at=datetime(2026, 10, 27), customer_name="大寺 啓未",
                   customer_email="a@b.c", inquiry_property_name="物件A",
                   inquiry_property_url="", raw_body="", is_vacant=vacant,
                   matched_property=_prop(vacant))


def _store(rows):
    sheets = MagicMock()
    sheets.read_templates.return_value = [
        {"テンプレート名": k, "内容": v} for k, v in rows.items()]
    s = ts.TemplateStore(sheets)
    s.refresh()
    return s


def _both(kind, vacant_body, gone_body):
    return {
        ts.SUBJECT_ROW[(kind, ts.VACANT)]: "空室あり件名",
        ts.BODY_ROW[(kind, ts.VACANT)]: vacant_body,
        ts.SUBJECT_ROW[(kind, ts.UNAVAILABLE)]: "空室なし件名",
        ts.BODY_ROW[(kind, ts.UNAVAILABLE)]: gone_body,
    }


class TestVacancyChoosesTheTemplate(unittest.TestCase):
    def setUp(self):
        self.a = EmailAssembler(CO, True, templates=_store(
            _both(ts.FIRST, "内見のご案内 {顧客名}", "募集終了のご案内 {顧客名}")))

    def test_an_available_room_uses_the_vacant_wording(self):
        subject, body = self.a.build_first_mail_parts(_inq(True), "紹介文", "", [])
        self.assertEqual(subject, "空室あり件名")
        self.assertIn("内見のご案内 大寺 啓未", body)

    def test_a_gone_room_uses_the_unavailable_wording(self):
        subject, body = self.a.build_first_mail_parts(_inq(False), "", "", [])
        self.assertEqual(subject, "空室なし件名")
        self.assertIn("募集終了のご案内 大寺 啓未", body)

    def test_unknown_vacancy_is_treated_as_unavailable(self):
        """Never assert a room is free on the strength of not knowing."""
        inq = _inq(True)
        inq.is_vacant = None
        _, body = self.a.build_first_mail_parts(inq, "", "", [])
        self.assertIn("募集終了のご案内", body)

    def test_each_mail_has_its_own_pair(self):
        a = EmailAssembler(CO, True, templates=_store({
            **_both(ts.FIRST, "1st空室あり", "1st空室なし"),
            **_both(ts.SECOND, "2nd空室あり", "2nd空室なし"),
        }))
        self.assertIn("1st空室なし", a.build_first_mail_parts(_inq(False), "", "", [])[1])
        self.assertIn("2nd空室あり", a.build_second_mail_parts(_inq(True), "", "", [])[1])

    def test_one_variant_configured_leaves_the_other_on_the_built_in(self):
        a = EmailAssembler(CO, True, templates=_store({
            ts.SUBJECT_ROW[(ts.FIRST, ts.VACANT)]: "件名",
            ts.BODY_ROW[(ts.FIRST, ts.VACANT)]: "空室ありのみ編集",
        }))
        self.assertIn("空室ありのみ編集",
                      a.build_first_mail_parts(_inq(True), "", "", [])[1])
        self.assertIn("この度はお問い合わせ頂きありがとうございます",
                      a.build_first_mail_parts(_inq(False), "", "", [])[1])


class TestBlockingIsPerVariant(unittest.TestCase):
    def setUp(self):
        self.store = _store({
            ts.SUBJECT_ROW[(ts.FIRST, ts.VACANT)]: "正常な件名",
            ts.BODY_ROW[(ts.FIRST, ts.VACANT)]: "正常な本文 {顧客名}",
            ts.SUBJECT_ROW[(ts.FIRST, ts.UNAVAILABLE)]: "【{存在しない}】",
            ts.BODY_ROW[(ts.FIRST, ts.UNAVAILABLE)]: "本文",
        })

    def test_the_broken_variant_blocks(self):
        self.assertTrue(self.store.blocking_errors(ts.FIRST, ts.UNAVAILABLE))

    def test_the_sound_variant_does_not(self):
        self.assertEqual(self.store.blocking_errors(ts.FIRST, ts.VACANT), [])

    def test_checking_without_a_variant_covers_both(self):
        """Used at intake, where the needed mail is not yet known."""
        self.assertTrue(self.store.blocking_errors(ts.FIRST))


class TestEditableSignature(unittest.TestCase):
    def test_it_replaces_the_built_in_signature(self):
        a = EmailAssembler(CO, True,
                           templates=_store({ts.SIGNATURE_ROW: "— 新しい署名 —"}))
        _, body = a.build_first_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("— 新しい署名 —", body)
        self.assertNotIn("■□━", body)

    def test_it_applies_to_follow_ups_as_well(self):
        a = EmailAssembler(CO, True,
                           templates=_store({ts.SIGNATURE_ROW: "— 新しい署名 —"}))
        _, body = a.build_second_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("— 新しい署名 —", body)

    def test_an_empty_row_keeps_the_built_in(self):
        a = EmailAssembler(CO, True, templates=_store({ts.SIGNATURE_ROW: "   "}))
        _, body = a.build_first_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("レントマガジン株式会社 / Rent Magazine", body)

    def test_a_signature_may_not_nest_a_placeholder(self):
        store = _store({ts.SIGNATURE_ROW: "署名 {署名}"})
        self.assertTrue(store.blocking_errors(ts.FIRST))


class TestEditableOptOutNotice(unittest.TestCase):
    def test_it_replaces_the_built_in_wording(self):
        a = EmailAssembler(CO, True,
                           templates=_store({ts.OPT_OUT_ROW: "配信停止のご案内（改）"}))
        _, body = a.build_first_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("配信停止のご案内（改）", body)
        self.assertNotIn("本メールへのご返信にて", body)

    def test_it_applies_to_follow_ups(self):
        a = EmailAssembler(CO, True,
                           templates=_store({ts.OPT_OUT_ROW: "配信停止のご案内（改）"}))
        _, body = a.build_third_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("配信停止のご案内（改）", body)

    def test_an_empty_row_keeps_the_built_in(self):
        a = EmailAssembler(CO, True, templates=_store({}))
        _, body = a.build_first_mail_parts(_inq(True), "紹介文", "", [])
        self.assertIn("配信停止", body)


class TestFollowUpUsesLiveVacancy(unittest.TestCase):
    """The stored vacancy is days old by the time a 2nd or 3rd goes out."""

    def _scheduler(self, live_vacant):
        from src.scheduling.followup_scheduler import FollowupScheduler
        s = FollowupScheduler.__new__(FollowupScheduler)
        s._wp = MagicMock()
        s._wp.get_property_by_url.return_value = _prop(vacant=False)
        s._wp.get_property_by_name.return_value = _prop(vacant=False)
        s._wp.refetch_one.return_value = _prop(vacant=live_vacant)
        return s

    def test_a_room_freed_since_intake_is_seen(self):
        s = self._scheduler(live_vacant=True)
        inq = _inq(False)
        inq.inquiry_property_url = "https://x/p/"
        prop = s._resolve_property(inq)
        fresh = s._wp.refetch_one(prop.wp_id)
        self.assertTrue(fresh.is_vacant)

    def test_the_live_listing_is_requested_by_id(self):
        s = self._scheduler(live_vacant=True)
        inq = _inq(False)
        inq.inquiry_property_url = "https://x/p/"
        prop = s._resolve_property(inq)
        s._wp.refetch_one(prop.wp_id)
        s._wp.refetch_one.assert_called_with(1)


if __name__ == "__main__":
    unittest.main()
