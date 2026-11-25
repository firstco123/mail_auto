"""
An inquiry we could not judge is put back, not recorded as rejected.

The row is written before screening. When the check cannot run — the API out of
credit, as on 2026-11-11 — leaving it at NG検出 records a verdict never reached,
and worse, a recorded Message-ID is skipped on every later cycle. The inquiry
would stay stuck after the cause was fixed, and nobody would know to look.

Removing the row returns the mail to "not yet processed", so the next cycle
picks it up from the mailbox once judgement is possible again. The 7-day fetch
window is the limit on how long that remains true.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.core.models import Inquiry

SUUMO_BODY = """\
問合せ物件
物件名:オリーブ201
最寄り駅:名鉄西尾線/南安城
賃料:6.3万円
間取り:1LDK
専有面積:40.32平米
お客様プロフィール
名前(漢字):三宅しのぶ
メールアドレス:shinobu@example.com
お問合せ内容:この部屋を実際に見学したい
"""


def _raw(mid="<a@jds>"):
    return {"from_addr": "system@jds.suumo.jp",
            "subject": "[リクルートJDS]反響お知らせメール",
            "body": SUUMO_BODY, "message_id": mid,
            "in_reply_to": "", "date": None, "uid": "1"}


def _verdict(failed):
    return MagicMock(is_clean=not failed, ng_hits=[], discriminatory=failed,
                     discriminatory_reason="判定エラー（要確認）" if failed else "",
                     complaint=False, complaint_reason="", check_failed=failed)


def _processor(failed=True):
    sheets = MagicMock()
    sheets.find_inquiry_by_message_id.return_value = None
    sheets.delete_inquiry_row.return_value = True
    wp = MagicMock()
    wp.get_property_by_url.return_value = None
    wp.resolve_by_formal_name.return_value = (None, "")
    wp.properties = []
    checker = MagicMock()
    checker.check.return_value = _verdict(failed)
    proc = InquiryProcessor(
        gmail=MagicMock(), sheets=sheets, wp=wp, checker=checker,
        generator=MagicMock(), scorer=MagicMock(), gate=MagicMock(),
        company={"staff_name": "担当"}, followup_cfg={})
    return proc, sheets


class TestAnUnjudgedInquiryIsPutBack(unittest.TestCase):
    def test_the_row_is_removed(self):
        proc, sheets = _processor(failed=True)
        proc._process_one(_raw(), {}, seen_ids=set())
        sheets.delete_inquiry_row.assert_called_once()

    def test_it_is_not_left_recorded_as_ng(self):
        proc, sheets = _processor(failed=True)
        proc._process_one(_raw(), {}, seen_ids=set())
        statuses = [c[0][1] for c in sheets.update_status.call_args_list]
        self.assertNotIn("NG検出", statuses)

    def test_the_message_id_is_released_for_a_retry(self):
        """A recorded Message-ID is skipped forever; it must not stay recorded."""
        proc, _ = _processor(failed=True)
        seen = set()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        self.assertNotIn("<a@jds>", seen)

    def test_a_later_cycle_processes_it_again(self):
        proc, sheets = _processor(failed=True)
        seen = set()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        self.assertEqual(sheets.write_inquiry.call_count, 2)


class TestARealVerdictStillStands(unittest.TestCase):
    def test_a_genuine_ng_keeps_its_row(self):
        proc, sheets = _processor(failed=False)
        proc._checker.check.return_value = MagicMock(
            is_clean=False, ng_hits=[MagicMock(word="詐欺")],
            discriminatory=False, discriminatory_reason="",
            complaint=False, complaint_reason="", check_failed=False)
        proc._process_one(_raw(), {}, seen_ids=set())
        sheets.delete_inquiry_row.assert_not_called()
        self.assertIn("NG検出",
                      [c[0][1] for c in sheets.update_status.call_args_list])


class TestDeferralNeverLosesTheRow(unittest.TestCase):
    def test_a_failed_delete_leaves_the_row_in_place(self):
        """Still visible to staff, just not retried — better than vanishing."""
        proc, sheets = _processor(failed=True)
        sheets.delete_inquiry_row.side_effect = RuntimeError("sheets down")
        proc._process_one(_raw(), {}, seen_ids=set())   # must not raise

    def test_the_id_stays_recorded_when_the_delete_fails(self):
        proc, sheets = _processor(failed=True)
        sheets.delete_inquiry_row.side_effect = RuntimeError("sheets down")
        seen = set()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        self.assertIn("<a@jds>", seen)


if __name__ == "__main__":
    unittest.main()
