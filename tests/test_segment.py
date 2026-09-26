"""Step 1 のテスト。 python3 -m unittest で実行する(sudo不要)。"""

import unittest

from tcp.segment import (
    ACK,
    FIN,
    HEADER_LEN,
    SYN,
    Segment,
    build,
    checksum,
    flags_str,
    mss_option,
    parse,
    parse_options,
    verify_checksum,
)


class TestChecksum(unittest.TestCase):
    def test_rfc1071_example(self):
        # RFC 1071 の例: 00 01 f2 03 f4 f5 f6 f7 → 和 0xddf2 → 反転 0x220d
        self.assertEqual(checksum(bytes.fromhex("0001f203f4f5f6f7")), 0x220D)

    def test_odd_length_is_padded(self):
        self.assertEqual(checksum(b"\x12"), checksum(b"\x12\x00"))


class TestBuildAndParse(unittest.TestCase):
    def test_header_layout(self):
        seg = build("10.0.0.1", "10.0.0.2", 40000, 80, 1000, 0, SYN, 65535)
        self.assertEqual(len(seg), HEADER_LEN)
        # 送信元ポート 40000 = 0x9c40, 宛先 80 = 0x0050
        self.assertEqual(seg[0:4], bytes.fromhex("9c400050"))
        # seq 1000 = 0x000003e8
        self.assertEqual(seg[4:8], bytes.fromhex("000003e8"))
        # データオフセット 5(=20バイト)が上位4ビット → 0x50
        self.assertEqual(seg[12], 0x50)
        self.assertEqual(seg[13], SYN)

    def test_roundtrip(self):
        seg = build("10.0.0.1", "10.0.0.2", 1234, 5678, 111, 222, ACK | FIN, 4096, b"hello")
        parsed = parse(seg)
        self.assertEqual(parsed.src_port, 1234)
        self.assertEqual(parsed.dst_port, 5678)
        self.assertEqual(parsed.seq, 111)
        self.assertEqual(parsed.ack, 222)
        self.assertTrue(parsed.ack_flag and parsed.fin)
        self.assertFalse(parsed.syn)
        self.assertEqual(parsed.window, 4096)
        self.assertEqual(parsed.payload, b"hello")

    def test_checksum_verifies(self):
        seg = build("10.0.0.1", "10.0.0.2", 1, 2, 3, 4, ACK, 5, b"data")
        self.assertTrue(verify_checksum("10.0.0.1", "10.0.0.2", seg))

    def test_checksum_detects_corruption(self):
        seg = bytearray(build("10.0.0.1", "10.0.0.2", 1, 2, 3, 4, ACK, 5, b"data"))
        seg[-1] ^= 0x01  # ペイロードの1ビットを反転
        self.assertFalse(verify_checksum("10.0.0.1", "10.0.0.2", bytes(seg)))

    def test_checksum_detects_wrong_destination(self):
        # 疑似ヘッダにIPアドレスが含まれるので、別の宛先として検証すると失敗する
        seg = build("10.0.0.1", "10.0.0.2", 1, 2, 3, 4, ACK, 5)
        self.assertFalse(verify_checksum("10.0.0.1", "10.0.0.3", seg))

    def test_options(self):
        seg = build("10.0.0.1", "10.0.0.2", 1, 2, 3, 0, SYN, 5, options=mss_option(1460))
        parsed = parse(seg)
        self.assertEqual(len(seg), HEADER_LEN + 4)
        self.assertEqual(parse_options(parsed.options), {"mss": 1460})
        self.assertEqual(parsed.payload, b"")

    def test_seg_len_counts_syn_and_fin(self):
        self.assertEqual(Segment(1, 2, 0, 0, SYN, 0).seg_len, 1)
        self.assertEqual(Segment(1, 2, 0, 0, ACK, 0, payload=b"abc").seg_len, 3)
        self.assertEqual(Segment(1, 2, 0, 0, FIN | ACK, 0).seg_len, 1)

    def test_flags_str(self):
        self.assertEqual(flags_str(SYN), "[S]")
        self.assertEqual(flags_str(SYN | ACK), "[S.]")
        self.assertEqual(flags_str(FIN | ACK), "[F.]")


if __name__ == "__main__":
    unittest.main()
