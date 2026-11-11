"""
"We could not look" must not be reported as "we found a problem".

Live fault (2026-11-11): the Anthropic API ran out of credit. _check_context
falls back to discriminatory=True so an unjudged *inquiry* still reaches a
person — correct — but template validation read that as bad wording and stopped
every mail, telling the client their templates contained possible discriminatory
expressions. They went looking for text that was never there.

The same refresh was also re-screening all eight blocks on every poll cycle,
roughly 1,150 AI calls a day for text that had not changed, which is what
exhausted the credit in the first place.
"""
import unittest
from unittest.mock import MagicMock

from src.email_builder import template_store as ts
from src.email_builder.template_store import TemplateStore


def _result(clean=True, failed=False, words=(), disc=False, complaint=False):
    r = MagicMock()
    r.is_clean = clean
    r.ng_hits = [MagicMock(word=w) for w in words]
    r.discriminatory = disc
    r.discriminatory_reason = "判定エラー（要確認）" if failed else "外国籍を理由に排除"
    r.complaint = complaint
    r.complaint_reason = ""
    r.check_failed = failed
    return r


def _store(result, rows=None):
    sheets = MagicMock()
    sheets.read_templates.return_value = rows if rows is not None else [
        {"テンプレート名": ts.SUBJECT_ROW[(ts.FIRST, ts.VACANT)], "内容": "件名"},
        {"テンプレート名": ts.BODY_ROW[(ts.FIRST, ts.VACANT)], "内容": "本文 {顧客名}"},
    ]
    checker = MagicMock()
    checker.check.return_value = result
    s = TemplateStore(sheets, checker=checker)
    s.refresh()
    return s, checker


class TestAnOutageDoesNotBlockSending(unittest.TestCase):
    def test_a_failed_judgement_does_not_stop_the_mail(self):
        store, _ = _store(_result(clean=False, failed=True, disc=True))
        self.assertEqual(store.blocking_errors(ts.FIRST), [])

    def test_the_template_remains_usable(self):
        store, _ = _store(_result(clean=False, failed=True, disc=True))
        self.assertIsNotNone(store.usable(ts.FIRST, ts.VACANT))

    def test_the_outage_is_reported_separately(self):
        store, _ = _store(_result(clean=False, failed=True, disc=True))
        self.assertTrue(store.check_unavailable())

    def test_keyword_hits_are_still_reported_during_an_outage(self):
        """The word scan runs offline and its findings still matter."""
        store, _ = _store(_result(clean=False, failed=True, words=["ぼったくり"]))
        self.assertTrue(any("ぼったくり" in e
                            for e in store.blocking_errors(ts.FIRST)))

    def test_a_healthy_check_reports_no_outage(self):
        store, _ = _store(_result(clean=True))
        self.assertFalse(store.check_unavailable())


class TestRealProblemsStillBlock(unittest.TestCase):
    def test_discriminatory_wording_still_blocks(self):
        store, _ = _store(_result(clean=False, disc=True))
        errs = store.blocking_errors(ts.FIRST)
        self.assertTrue(errs)
        self.assertIn("外国籍を理由に排除", errs[0])

    def test_the_detected_word_is_named(self):
        store, _ = _store(_result(clean=False, words=["詐欺"]))
        self.assertIn("詐欺", store.blocking_errors(ts.FIRST)[0])


class TestUnchangedTextIsNotRescreened(unittest.TestCase):
    def test_a_second_refresh_makes_no_further_calls(self):
        store, checker = _store(_result(clean=True))
        first = checker.check.call_count
        store.refresh()
        self.assertEqual(checker.check.call_count, first)

    def test_changed_text_is_screened_again(self):
        store, checker = _store(_result(clean=True))
        first = checker.check.call_count
        store._sheets.read_templates.return_value = [
            {"テンプレート名": ts.SUBJECT_ROW[(ts.FIRST, ts.VACANT)], "内容": "件名"},
            {"テンプレート名": ts.BODY_ROW[(ts.FIRST, ts.VACANT)], "内容": "書き換えた本文"},
        ]
        store.refresh()
        self.assertGreater(checker.check.call_count, first)

    def test_a_failed_verdict_is_not_cached(self):
        """Otherwise the outage would persist after the API recovered."""
        store, checker = _store(_result(clean=False, failed=True, disc=True))
        first = checker.check.call_count
        store.refresh()
        self.assertGreater(checker.check.call_count, first)


if __name__ == "__main__":
    unittest.main()
