"""
System faults have to reach someone who is not looking at the panel.

The banner only helps a person who opens the panel. An exhausted API key halts
every reply, and that went unnoticed for at least twelve hours (client,
2026-11-12). Notifications go out on a change of state only, so a lasting fault
does not mail every five minutes.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor


def _processor(to="staff@example.com", unavailable=False, errors=None):
    p = InquiryProcessor.__new__(InquiryProcessor)
    p._sheets = MagicMock()
    p._sheets.read_config_value.return_value = to
    p._gmail = MagicMock()
    p._templates = MagicMock()
    p._templates.check_unavailable.return_value = unavailable
    p._templates.all_errors.return_value = errors or {}
    p._templates.blocking_errors.return_value = []
    p._last_template_status = None
    p._last_ai_status = None
    return p


class TestNotifiesOnChange(unittest.TestCase):
    def test_the_first_cycle_after_a_restart_does_not_mail(self):
        """Otherwise every deploy mails during an ongoing outage."""
        p = _processor(unavailable=True)
        p._templates_ready()
        p._gmail.send.assert_not_called()

    def test_a_fault_appearing_later_does_mail(self):
        p = _processor(unavailable=False)
        p._templates_ready()                       # healthy baseline
        p._templates.check_unavailable.return_value = True
        p._templates_ready()
        p._gmail.send.assert_called_once()
        self.assertIn("問題が発生", p._gmail.send.call_args[0][1])

    def test_a_lasting_fault_mails_only_once(self):
        p = _processor(unavailable=False)
        p._templates_ready()
        p._templates.check_unavailable.return_value = True
        for _ in range(5):
            p._templates_ready()
        self.assertEqual(p._gmail.send.call_count, 1)

    def test_recovery_is_announced(self):
        p = _processor(unavailable=False)
        p._templates_ready()
        p._templates.check_unavailable.return_value = True
        p._templates_ready()
        p._templates.check_unavailable.return_value = False
        p._templates_ready()
        self.assertEqual(p._gmail.send.call_count, 2)
        self.assertIn("復旧", p._gmail.send.call_args[0][1])


class TestNotificationIsOptional(unittest.TestCase):
    def test_no_address_means_no_mail(self):
        p = _processor(to="")
        p._templates_ready()
        p._templates.check_unavailable.return_value = True
        p._templates_ready()
        p._gmail.send.assert_not_called()

    def test_whitespace_is_treated_as_unset(self):
        p = _processor(to="   ")
        p._templates_ready()
        p._templates.check_unavailable.return_value = True
        p._templates_ready()
        p._gmail.send.assert_not_called()


class TestNotificationNeverBreaksTheCycle(unittest.TestCase):
    def test_a_send_failure_is_swallowed(self):
        p = _processor(unavailable=False)
        p._templates_ready()
        p._gmail.send.side_effect = RuntimeError("smtp down")
        p._templates.check_unavailable.return_value = True
        p._templates_ready()        # must not raise

    def test_an_unreadable_address_is_swallowed(self):
        p = _processor(unavailable=False)
        p._templates_ready()
        p._sheets.read_config_value.side_effect = RuntimeError("sheets down")
        p._templates.check_unavailable.return_value = True
        p._templates_ready()        # must not raise
        p._gmail.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
