from __future__ import annotations
import base64
import binascii
import logging
import quopri
import re
import smtplib
import time
import unicodedata
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Generator

from imap_tools import MailBox, AND, MailMessage

logger = logging.getLogger(__name__)

# How far back each poll looks. Ingestion is keyed on Message-ID rather than on
# the unread flag, so a mail someone opened first is still picked up; this
# window only bounds how much of the mailbox is re-scanned each cycle.
_LOOKBACK_DAYS = 7

# ── ISO-2022-JP with vendor-extended characters ─────────────────────────────
# SUUMO sends 反響 as iso-2022-jp and writes 外字 such as 髙 (U+9AD9) using the
# NEC/IBM extended area, 区 85–94. JIS X 0208 stops at 区 84, so every standard
# codec — including iso2022_jp_ext and _2004 — replaces those characters, and
# 「髙橋」 reached the panel as 「橋」. CP932 maps the same area, so the bytes are
# recoverable: convert each 区点 pair to Shift-JIS and decode that instead.
_ESC_TO_JIS = (b"\x1b$B", b"\x1b$@")
_ESC_TO_ASCII = (b"\x1b(B", b"\x1b(J")
_REPLACEMENT = "�"


def _jis_to_sjis(ku: int, ten: int) -> bytes:
    first = (ku + 257) // 2 if ku <= 62 else (ku + 385) // 2
    if ku % 2:
        second = ten + 63 if ten <= 63 else ten + 64
    else:
        second = ten + 158
    return bytes([first, second])


def decode_iso2022jp_lenient(data: bytes) -> str:
    """Decode ISO-2022-JP, resolving 外字 through CP932 rather than dropping them."""
    out: list[str] = []
    i, in_jis = 0, False
    while i < len(data):
        head = data[i:i + 3]
        if head in _ESC_TO_JIS:
            in_jis, i = True, i + 3
            continue
        if head in _ESC_TO_ASCII:
            in_jis, i = False, i + 3
            continue
        if head[:1] == b"\x1b":
            # Some other designation (half-width kana, JIS X 0201). Leave the
            # standard codec's judgement alone by bailing out entirely.
            raise ValueError("unsupported ISO-2022-JP escape")
        if in_jis and i + 1 < len(data):
            try:
                out.append(_jis_to_sjis(data[i] - 0x20, data[i + 1] - 0x20)
                           .decode("cp932"))
            except Exception:
                out.append(_REPLACEMENT)
            i += 2
            continue
        out.append(chr(data[i]))
        i += 1
    return "".join(out)


def _raw_text_part(msg: MailMessage) -> tuple[bytes | None, str]:
    """Undecoded bytes and declared charset of the body part we actually used.

    Mirrors _parse's own choice: the text/plain part, or the HTML one when the
    mail carries no plain alternative.
    """
    obj = getattr(msg, "obj", None)
    if obj is None:
        return None, ""
    try:
        part = obj
        if obj.is_multipart():
            parts = list(obj.walk())
            part = next((p for p in parts
                         if p.get_content_type() == "text/plain"), None)
            if part is None:
                part = next((p for p in parts
                             if p.get_content_type() == "text/html"), None)
            if part is None:
                return None, ""
        return part.get_payload(decode=True), (part.get_content_charset() or "")
    except Exception:
        return None, ""


# ── headers ─────────────────────────────────────────────────────────────────
# Subject and sender name arrive as RFC 2047 encoded-words, and lose 外字 the
# same way the body did: a 「髙橋」 subject reaches us as 「橋」. The decoding is
# separate from the body's, so it needs the same repair applied to it.
_ENCODED_WORD = re.compile(r"=\?([^?]+)\?([BbQq])\?([^?]*)\?=")


def _is_jis(charset: str) -> bool:
    return "2022" in charset.lower().replace("-", "")


def _decode_encoded_words(raw: str, lenient: bool) -> str:
    """Decode a header's encoded-words, optionally resolving 外字 via CP932."""
    out: list[str] = []
    pos, prev_was_word = 0, False
    for m in _ENCODED_WORD.finditer(raw):
        gap = raw[pos:m.start()]
        # RFC 2047: whitespace separating two adjacent encoded-words is not
        # part of the text, so a name does not gain a space mid-way.
        if not (prev_was_word and gap.strip() == ""):
            out.append(gap)
        charset, enc, payload = m.group(1), m.group(2).upper(), m.group(3)
        try:
            data = (base64.b64decode(payload + "===") if enc == "B"
                    else quopri.decodestring(payload.replace("_", " ")))
        except (binascii.Error, ValueError):
            out.append(m.group(0))
            pos, prev_was_word = m.end(), True
            continue
        if lenient and _is_jis(charset):
            try:
                out.append(decode_iso2022jp_lenient(data))
            except Exception:
                out.append(data.decode(charset, errors="replace"))
        else:
            try:
                out.append(data.decode(charset, errors="replace"))
            except LookupError:
                out.append(m.group(0))
        pos, prev_was_word = m.end(), True
    out.append(raw[pos:])
    return "".join(out)


def _repair_header(decoded: str, raw_header: str | None) -> str:
    """Re-decode a header only when the standard decoding dropped a character."""
    if not raw_header or "=?" not in raw_header:
        return decoded
    try:
        probed = _decode_encoded_words(raw_header, lenient=False)
        if _REPLACEMENT not in probed:
            return decoded
        repaired = _decode_encoded_words(raw_header, lenient=True)
    except Exception as e:
        logger.warning("Could not re-decode header: %s", e)
        return decoded
    if repaired.count(_REPLACEMENT) >= probed.count(_REPLACEMENT):
        return decoded
    logger.info("Recovered %d extended JIS character(s) lost from a header",
                probed.count(_REPLACEMENT) - repaired.count(_REPLACEMENT))
    return repaired


def _raw_header(msg: MailMessage, name: str) -> str:
    obj = getattr(msg, "obj", None)
    if obj is None:
        return ""
    try:
        return obj.get(name) or ""
    except Exception:
        return ""


def _repair_jis_extensions(body: str, msg: MailMessage) -> str:
    """Re-decode when the standard codec could not represent a character.

    The loss is detected from the raw bytes, not from the decoded string: the
    mail library drops an unmappable character silently rather than leaving a
    replacement mark, so 「髙橋」 simply arrived as 「橋」 with nothing to notice.
    """
    raw, charset = _raw_text_part(msg)
    if not raw or "2022" not in charset.lower().replace("-", ""):
        return body
    try:
        # errors="replace" makes the loss visible; the library hid it.
        probed = raw.decode(charset, errors="replace")
    except Exception:
        return body
    if _REPLACEMENT not in probed:
        return body
    try:
        repaired = decode_iso2022jp_lenient(raw)
    except Exception as e:
        logger.warning("Could not re-decode extended JIS characters: %s", e)
        return body
    if repaired.count(_REPLACEMENT) >= probed.count(_REPLACEMENT):
        return body
    logger.info("Recovered %d extended JIS character(s) the standard codec lost",
                probed.count(_REPLACEMENT) - repaired.count(_REPLACEMENT))
    return repaired


class GmailClient:
    def __init__(self, address: str, app_password: str,
                 imap_host: str, smtp_host: str, smtp_port: int):
        self._address = address
        self._password = app_password
        self._imap_host = imap_host
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

    # ── receive ─────────────────────────────────────────────────────────────

    def fetch_recent(self) -> list[dict]:
        """
        Connect → fetch the last _LOOKBACK_DAYS of mail → disconnect.
        Returns normalised dicts ready for InquiryProcessor.
        Retries up to 3 times with exponential backoff on connection errors.
        """
        for attempt in range(3):
            try:
                return self._do_fetch()
            except Exception as e:
                wait = 10 * (2 ** attempt)
                logger.warning("IMAP fetch attempt %d failed: %s — retry in %ds", attempt + 1, e, wait)
                if attempt == 2:
                    raise
                time.sleep(wait)
        return []

    def _do_fetch(self) -> list[dict]:
        results: list[dict] = []
        since = date.today() - timedelta(days=_LOOKBACK_DAYS)
        with MailBox(self._imap_host).login(self._address, self._password) as mb:
            # Deliberately not filtered on seen=False, and deliberately not
            # marking anything seen. Reflections were being lost outright: staff
            # opened a 反響 in the mailbox before the poller ran, seen=False then
            # excluded it, and it could never be ingested afterwards. Marking
            # seen was unsafe on its own too — the batch is flagged before any
            # row is written, so an error mid-cycle discarded the mail for good.
            # Duplicate protection is the Message-ID check in InquiryProcessor,
            # which survives both cases.
            for msg in mb.fetch(AND(date_gte=since), mark_seen=False, bulk=True):
                try:
                    results.append(self._parse(msg))
                except Exception as e:
                    # One malformed message must not abort the whole batch.
                    logger.error("Failed to parse message uid=%s: %s", msg.uid, e)
        logger.info("Fetched %d email(s) since %s", len(results), since)
        return results

    def _parse(self, msg: MailMessage) -> dict:
        body = msg.text or ""
        if not body and msg.html:
            body = msg.html
        body = _repair_jis_extensions(body, msg)
        from_name = (msg.from_values.name if msg.from_values else "") or ""
        # Headers are decoded separately from the body, so they need the same
        # 外字 repair — the subject and the sender's name lose characters too.
        from_name = _repair_header(
            from_name, parseaddr(_raw_header(msg, "From"))[0] or None)
        subject = _repair_header(msg.subject or "", _raw_header(msg, "Subject"))
        return {
            "uid": msg.uid,
            "message_id": (msg.headers.get("message-id") or [""])[0].strip(),
            "in_reply_to": (msg.headers.get("in-reply-to") or [""])[0].strip(),
            "from_addr": msg.from_ or "",
            "from_name": from_name.strip(),
            "subject": subject,
            "body": unicodedata.normalize("NFKC", body),
            "date": msg.date,
            "headers_present": list((msg.headers or {}).keys()),
        }

    # ── send ─────────────────────────────────────────────────────────────────

    def send(self, to: str, subject: str, body_html: str,
             reply_to_message_id: str = "") -> str:
        """
        Send an HTML email via Gmail SMTP.
        Returns the generated Message-ID.
        Raises on failure (caller decides whether to mark as 要確認).
        """
        msg = MIMEMultipart("alternative")
        msg["From"] = self._address
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        msg.attach(MIMEText(body_html, "html", "utf-8"))
        message_id = msg["Message-ID"] or f"<mail_auto_{int(time.time())}@rentmagazine.jp>"
        msg["Message-ID"] = message_id

        for attempt in range(3):
            try:
                with smtplib.SMTP(self._smtp_host, self._smtp_port) as s:
                    s.ehlo()
                    s.starttls()
                    s.login(self._address, self._password)
                    s.sendmail(self._address, [to], msg.as_bytes())
                logger.info("Sent email to %s (subject: %s)", to, subject)
                return message_id
            except smtplib.SMTPException as e:
                wait = 5 * (2 ** attempt)
                logger.warning("SMTP attempt %d failed: %s — retry in %ds", attempt + 1, e, wait)
                if attempt == 2:
                    raise
                time.sleep(wait)
        return message_id
