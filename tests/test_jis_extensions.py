"""
外字 in a customer's name must survive the mail decode.

Live fault (2026-11-19): 「髙橋 里空」 reached the panel as 「橋 里空」. SUUMO sends
反響 as iso-2022-jp and writes 髙 (U+9AD9) in the NEC/IBM extended area — JIS
92区66点. JIS X 0208 stops at 区84, so every standard codec, iso2022_jp_ext and
_2004 included, cannot represent it. The mail library then dropped it silently
rather than leaving a replacement mark, so nothing looked wrong until a customer
was addressed by the wrong name.

CP932 maps the same area, so the bytes are recoverable: convert 区点 to
Shift-JIS and decode that.
"""
import unittest

from src.integrations.gmail_client import (_jis_to_sjis,
                                           decode_iso2022jp_lenient)

ESC_JIS, ESC_ASCII = b"\x1b$B", b"\x1b(B"


def _jis(*pairs):
    return ESC_JIS + bytes(b for p in pairs for b in p) + ESC_ASCII


class TestKuTenToShiftJis(unittest.TestCase):
    def test_the_failing_character(self):
        """JIS 92区66点 is 髙."""
        self.assertEqual(_jis_to_sjis(92, 66), b"\xee\xe0")
        self.assertEqual(b"\xee\xe0".decode("cp932"), "髙")

    def test_the_conversion_agrees_with_the_standard_codec(self):
        """Checked against Python's own tables rather than hand-written values,
        across odd and even 区 and both halves of the 点 range."""
        for ch in "、。あぁアン漢字橋山田一二三ｱ巷熙":
            encoded = ch.encode("iso2022_jp", errors="ignore")
            if len(encoded) != 7:          # ESC $ B + 2 bytes + ESC ( B
                continue
            ku, ten = encoded[3] - 0x20, encoded[4] - 0x20
            with self.subTest(ch=ch, ku=ku, ten=ten):
                self.assertEqual(_jis_to_sjis(ku, ten).decode("cp932"), ch)


class TestLenientDecode(unittest.TestCase):
    def test_the_extended_character_is_recovered(self):
        data = _jis((0x7c, 0x62), (0x36, 0x36))      # 髙 + 橋
        self.assertEqual(decode_iso2022jp_lenient(data), "髙橋")

    def test_ascii_outside_the_escapes_is_preserved(self):
        data = b"Name: " + _jis((0x36, 0x36)) + b"\r\n"
        self.assertEqual(decode_iso2022jp_lenient(data), "Name: 橋\r\n")

    def test_ordinary_japanese_is_unchanged(self):
        """Whatever this path does to normal text, it must match the standard."""
        text = "名前（漢字）：山田　太郎"
        data = text.encode("iso2022_jp")
        self.assertEqual(decode_iso2022jp_lenient(data), text)

    def test_mixed_standard_and_extended(self):
        text_part = "名前：".encode("iso2022_jp")[:-3]   # drop trailing ESC ( B
        data = text_part + bytes([0x7c, 0x62, 0x36, 0x36]) + ESC_ASCII
        self.assertEqual(decode_iso2022jp_lenient(data), "名前：髙橋")

    def test_an_unsupported_escape_is_refused_rather_than_guessed(self):
        """Half-width kana designation — leave the standard codec in charge."""
        with self.assertRaises(ValueError):
            decode_iso2022jp_lenient(b"\x1b(I\x31\x32")

    def test_a_nonsense_pair_does_not_raise(self):
        """Garbage in the JIS segment must not abort the whole mail."""
        out = decode_iso2022jp_lenient(_jis((0x21, 0x21), (0x7f, 0x7f)))
        self.assertEqual(len(out), 2)

    def test_a_truncated_segment_does_not_raise(self):
        decode_iso2022jp_lenient(ESC_JIS + b"\x36")


class TestOnlyBrokenMailIsTouched(unittest.TestCase):
    """Re-decoding sound mail risks changing text that was already correct."""

    class _Part:
        def __init__(self, raw, charset):
            self._raw, self._cs = raw, charset
        def is_multipart(self): return False
        def get_payload(self, decode=False): return self._raw
        def get_content_charset(self): return self._cs

    class _Msg:
        def __init__(self, obj): self.obj = obj

    def _repair(self, raw, charset, body):
        from src.integrations.gmail_client import _repair_jis_extensions
        return _repair_jis_extensions(body, self._Msg(self._Part(raw, charset)))

    def test_clean_iso2022jp_is_left_alone(self):
        text = "名前：山田"
        raw = text.encode("iso2022_jp")
        self.assertEqual(self._repair(raw, "iso-2022-jp", text), text)

    def test_a_utf8_mail_is_left_alone(self):
        text = "名前：髙橋"
        self.assertEqual(self._repair(text.encode("utf-8"), "utf-8", text), text)

    def test_a_broken_mail_is_repaired(self):
        raw = _jis((0x7c, 0x62), (0x36, 0x36))
        lossy = raw.decode("iso2022_jp", errors="ignore")   # what the library gave us
        self.assertEqual(self._repair(raw, "iso-2022-jp", lossy), "髙橋")

    def test_no_raw_bytes_means_no_change(self):
        self.assertEqual(self._repair(None, "iso-2022-jp", "橋"), "橋")


class TestHeadersLoseTheSameCharacters(unittest.TestCase):
    """Subject and sender name are decoded separately from the body.

    Repairing only the body left 「髙橋」 arriving as 「橋」 in the subject line,
    so the same repair has to be applied to the encoded-words in a header.
    """

    @staticmethod
    def _word(text_or_bytes, charset="iso-2022-jp"):
        import base64
        raw = (text_or_bytes if isinstance(text_or_bytes, bytes)
               else text_or_bytes.encode(charset))
        return "=?%s?B?%s?=" % (charset, base64.b64encode(raw).decode())

    def _repair(self, decoded, raw_header):
        from src.integrations.gmail_client import _repair_header
        return _repair_header(decoded, raw_header)

    def test_an_extended_character_in_a_header_is_recovered(self):
        header = self._word(_jis((0x7c, 0x62), (0x36, 0x36)))
        self.assertEqual(self._repair("橋", header), "髙橋")

    def test_a_plain_header_is_left_alone(self):
        self.assertEqual(self._repair("反響お知らせ", "反響お知らせ"), "反響お知らせ")

    def test_a_sound_encoded_word_is_left_alone(self):
        header = self._word("反響お知らせメール")
        self.assertEqual(self._repair("反響お知らせメール", header),
                         "反響お知らせメール")

    def test_a_utf8_header_is_left_alone(self):
        header = self._word("髙橋 里空", "utf-8")
        self.assertEqual(self._repair("髙橋 里空", header), "髙橋 里空")

    def test_text_around_the_encoded_word_is_kept(self):
        header = "[JDS] " + self._word(_jis((0x7c, 0x62), (0x36, 0x36))) + " 様"
        self.assertEqual(self._repair("[JDS] 橋 様", header), "[JDS] 髙橋 様")

    def test_adjacent_encoded_words_do_not_gain_a_space(self):
        """RFC 2047: whitespace between two encoded-words is not text."""
        header = (self._word(_jis((0x7c, 0x62))) + " "
                  + self._word(_jis((0x36, 0x36))))
        self.assertEqual(self._repair("橋", header), "髙橋")

    def test_an_unparsable_header_does_not_raise(self):
        self.assertEqual(self._repair("橋", "=?iso-2022-jp?B?!!!?="), "橋")

    def test_an_unknown_charset_does_not_raise(self):
        self.assertEqual(self._repair("x", "=?no-such-charset?B?YWJj?="), "x")


class TestWholeMailIsCovered(unittest.TestCase):
    """The client's question: does this reach every field, not just the name?"""

    def _parse(self, body_bytes, subject_word, from_word):
        import base64
        from imap_tools import MailMessage
        from src.integrations.gmail_client import GmailClient
        raw = (b"Subject: " + subject_word + b"\r\n"
               b"From: " + from_word + b" <system@jds.suumo.jp>\r\n"
               b"Message-ID: <x@y>\r\n"
               b"Date: Wed, 19 Nov 2025 22:22:28 +0900\r\n"
               b"Content-Type: text/plain; charset=iso-2022-jp\r\n"
               b"Content-Transfer-Encoding: 7bit\r\n\r\n" + body_bytes + b"\r\n")
        msg = MailMessage([(b"1 (RFC822 {%d}" % len(raw), raw), b")"])
        return GmailClient.__new__(GmailClient)._parse(msg)

    # Written as ordinary text with a stand-in kanji, then that kanji's JIS
    # bytes are swapped for 92区66点 — so the fixture is a real SUUMO-shaped
    # mail rather than hand-assembled escape sequences.
    SENTINEL = "龍"
    TEXT = ("問合せ物件\r\n"
            "物件名:龍橋ハイツ201\r\n"
            "所在地:愛知県龍橋市東新町\r\n"
            "賃料:6.3万円\r\n"
            "お客様プロフィール\r\n"
            "名前(漢字):龍橋 里空\r\n"
            "メールアドレス:ritsuku@example.com\r\n"
            "お問合せ内容:龍橋ビル隣の部屋を見学したい\r\n")

    @classmethod
    def _body_with_extended(cls) -> bytes:
        pair = cls.SENTINEL.encode("iso2022_jp")[3:5]
        return cls.TEXT.encode("iso2022_jp").replace(pair, b"\x7c\x62")

    def test_name_property_address_and_body_all_survive(self):
        """One 外字 in every field — all of them must come back."""
        from src.core.reflection import identify
        body = self._body_with_extended()
        # Precondition: the standard codec really does drop it.
        self.assertNotIn("髙", body.decode("iso2022_jp", errors="ignore"))

        word = b"=?iso-2022-jp?B?" + __import__("base64").b64encode(
            b"\x1b$B\x7c\x62\x36\x36\x1b(B") + b"?="
        out = self._parse(body, word, word)
        self.assertIn("髙", out["subject"])
        self.assertIn("髙", out["from_name"])

        ref = identify({**out, "from_addr": "system@jds.suumo.jp"})
        self.assertIsNotNone(ref)
        self.assertEqual(ref.customer_name, "髙橋 里空")
        self.assertEqual(ref.property_name, "髙橋ハイツ201")
        self.assertEqual(ref.extras["所在地"], "愛知県髙橋市東新町")
        self.assertIn("髙橋ビル", ref.inquiry_text)
        self.assertEqual(ref.customer_email, "ritsuku@example.com")

    def test_the_same_mail_without_the_extended_char_is_unaffected(self):
        """The repair must not alter a mail the standard codec handled."""
        from src.core.reflection import identify
        plain = self.TEXT.encode("iso2022_jp")
        word = b"=?iso-2022-jp?B?" + __import__("base64").b64encode(
            b"\x1b$B\x36\x36\x1b(B") + b"?="
        ref = identify({**self._parse(plain, word, word),
                        "from_addr": "system@jds.suumo.jp"})
        self.assertEqual(ref.customer_name, "龍橋 里空")
        self.assertEqual(ref.property_name, "龍橋ハイツ201")


if __name__ == "__main__":
    unittest.main()
