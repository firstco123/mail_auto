"""
The reference wording written into the sheet must reproduce the built-in mail.

It is offered to the client as "copy this and edit it". If rendering it produced
something different from what the system sends today, their first edit would
silently change wording they never touched.

Six variants now: 1st/2nd/3rd × 空室あり/空室なし. The 空室なし wording — including
「せっかくお問い合わせいただきましたが…」 — was previously fixed in code and could
not be edited at all, which is what prompted the split (client, 2026-10-27).
"""
import unittest

import seed_templates as seed
from src.email_builder import followup_blocks as fb
from src.email_builder import template_store as ts
from src.email_builder.assembler import _SIGNATURE, EmailAssembler

CO = {"name": "レントマガジン株式会社", "staff_name": "新家",
      "tel_reservation": "0566-70-8282", "discount_url": "https://x/c",
      "mypage_url": "https://x/m", "line_url": "https://x/l"}


def _case(kind, vacancy):
    """(inquiry, alt_intros) for one variant, matching the seeder exactly."""
    inq = seed._sample_inquiry()
    vacant = vacancy == ts.VACANT
    inq.is_vacant = vacant
    alts = [] if vacant else [(seed._alt_property(), seed._MARK["AI紹介文"])]
    intro = seed._MARK["AI紹介文"] if vacant else ""
    return inq, alts, intro


class TestReferenceWordingRoundTrips(unittest.TestCase):
    def setUp(self):
        self.wording = seed.current_wording(CO)

    def _built_in(self, kind, vacancy):
        a = EmailAssembler(CO, True)
        inq, alts, intro = _case(kind, vacancy)
        build = {ts.FIRST: a.build_first_mail_parts,
                 ts.SECOND: a.build_second_mail_parts,
                 ts.THIRD: a.build_third_mail_parts}[kind]
        return build(inq, intro, "", alts)

    def _rendered(self, kind, vacancy):
        a = EmailAssembler(CO, True)
        inq, alts, intro = _case(kind, vacancy)
        subject, body = self.wording[(kind, vacancy)]
        ctx = {
            "顧客名": inq.customer_name,
            "物件名": inq.inquiry_property_name,
            "物件URL": inq.matched_property.url,
            "AI紹介文": seed._MARK["AI紹介文"],
            "最寄駅": a._station_text(inq.matched_property),
            "担当者名": CO["staff_name"],
            "署名": _SIGNATURE,
            "物件情報": a._property_section(inq, intro, alts),
        }
        return ts.render(subject, ctx), ts.render(body, ctx)

    def test_every_variant_round_trips(self):
        for kind in ts.KINDS:
            for vacancy in ts.VACANCIES:
                with self.subTest(kind=kind, vacancy=vacancy):
                    rs, rb = self._rendered(kind, vacancy)
                    bs, bb = self._built_in(kind, vacancy)
                    self.assertEqual(rs, bs)
                    self.assertEqual(rb.strip(), bb.strip())


class TestTheVacantAndUnavailableWordingDiffer(unittest.TestCase):
    """If both variants were identical the split would be pointless."""

    def setUp(self):
        self.wording = seed.current_wording(CO)

    def test_the_first_mail_bodies_differ(self):
        vacant = self.wording[(ts.FIRST, ts.VACANT)][1]
        gone = self.wording[(ts.FIRST, ts.UNAVAILABLE)][1]
        self.assertNotEqual(vacant, gone)

    def test_the_unavailable_wording_is_now_editable(self):
        """Previously fixed in code; the whole point of the split."""
        gone = self.wording[(ts.FIRST, ts.UNAVAILABLE)][1]
        self.assertIn("せっかくお問い合わせ", gone)


class TestReferenceWordingIsValid(unittest.TestCase):
    def test_the_offered_templates_all_pass_validation(self):
        for (kind, vacancy), (subject, body) in seed.current_wording(CO).items():
            with self.subTest(kind=kind, vacancy=vacancy):
                self.assertEqual(ts.validate(subject, body, kind), [])

    def test_no_sentinel_leaks_into_the_sheet(self):
        for subject, body in seed.current_wording(CO).values():
            for mark in seed._MARK.values():
                self.assertNotIn(mark, subject)
                self.assertNotIn(mark, body)
            self.assertNotIn("〘URL〙", body)
            self.assertNotIn("〘ALTURL〙", body)

    def test_the_staff_name_is_a_placeholder_not_baked_in(self):
        _, body = seed.current_wording(CO)[(ts.SECOND, ts.VACANT)]
        self.assertIn("{担当者名}", body)
        self.assertNotIn("新家", body)


class TestSheetLayout(unittest.TestCase):
    def setUp(self):
        self.rows = seed.build_rows(CO)
        self.names = [r[0] for r in self.rows]
        self.by_name = {r[0]: r[1] for r in self.rows}

    def test_all_twelve_editable_rows_ship_empty(self):
        for kind in ts.KINDS:
            for vacancy in ts.VACANCIES:
                self.assertEqual(self.by_name[ts.SUBJECT_ROW[(kind, vacancy)]], "")
                self.assertEqual(self.by_name[ts.BODY_ROW[(kind, vacancy)]], "")

    def test_the_shared_rows_ship_empty_too(self):
        self.assertEqual(self.by_name[ts.SIGNATURE_ROW], "")
        self.assertEqual(self.by_name[ts.OPT_OUT_ROW], "")

    def test_every_placeholder_is_documented(self):
        for name in ts.PLACEHOLDERS:
            self.assertIn(f"{{{name}}}", self.names)

    def test_reference_rows_are_not_read_by_the_store(self):
        for kind in ts.KINDS:
            for vacancy in ts.VACANCIES:
                ref = f"（参考）{ts.BODY_ROW[(kind, vacancy)]}"
                self.assertIn(ref, self.names)
                self.assertNotEqual(ref, ts.BODY_ROW[(kind, vacancy)])

    def test_the_signature_and_opt_out_have_reference_rows(self):
        self.assertEqual(self.by_name[f"（参考）{ts.SIGNATURE_ROW}"], _SIGNATURE)
        self.assertEqual(self.by_name[f"（参考）{ts.OPT_OUT_ROW}"], fb.OPT_OUT)


if __name__ == "__main__":
    unittest.main()
