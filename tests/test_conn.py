"""conn.py の状態遷移を、ラボなし・sudo なしで検証する。

RawTCPSocket を「台本どおりに応答する擬似相手」に差し替える。
擬似相手は build() でセグメントを作り、自作TCPの _recv() に流し込む。
"""

import unittest
from unittest import mock

import tcp.conn as conn
from tcp.segment import ACK, FIN, PSH, RST, SYN, Segment, build, parse

LOCAL, REMOTE = "10.0.0.1", "10.0.0.2"
LPORT, RPORT = 40000, 9000


class FakeRaw:
    """RawTCPSocket の代わり。send() された内容を記録し、inbox の中身を recv() で返す。"""

    def __init__(self, timeout: float = 0.05):
        self.inbox: list[bytes] = []
        self.sent: list[Segment] = []
        self.on_send = None      # 送信のたびに呼ばれるフック(擬似相手の台本)
        self.closed = False

    def send(self, dst_ip: str, seg_bytes: bytes) -> None:
        seg = parse(seg_bytes)
        self.sent.append(seg)
        if self.on_send:
            self.on_send(seg)

    def recv(self):
        if self.inbox:
            return REMOTE, LOCAL, self.inbox.pop(0)
        return None

    def close(self) -> None:
        self.closed = True

    # 擬似相手が返すセグメント
    def reply(self, seq: int, ack: int, flags: int, window: int = 65535, payload: bytes = b""):
        self.inbox.append(build(REMOTE, LOCAL, RPORT, LPORT, seq=seq, ack=ack, flags=flags, window=window, payload=payload))


def make_conn(**kw) -> tuple[conn.Connection, FakeRaw]:
    with mock.patch.object(conn, "RawTCPSocket", FakeRaw):
        c = conn.Connection(LOCAL, LPORT, REMOTE, RPORT, **kw)
    c._log = lambda msg: None  # テスト出力を静かに
    return c, c.raw


def established(snd=100, rcv=1) -> tuple[conn.Connection, FakeRaw]:
    """ESTABLISHED まで進んだ状態を直接作る。"""
    c, raw = make_conn()
    c.state = "ESTABLISHED"
    c.iss = c.snd_una = c.snd_nxt = snd
    c.irs = rcv - 1
    c.rcv_nxt = rcv
    c.snd_wnd = 65535
    return c, raw


class TestHandshake(unittest.TestCase):
    def test_active_open(self):
        c, raw = make_conn()

        def peer(seg):
            if seg.syn and not seg.ack_flag:
                raw.reply(seq=5000, ack=seg.seq + 1, flags=SYN | ACK)

        raw.on_send = peer
        c.connect()
        self.assertEqual(c.state, "ESTABLISHED")
        self.assertEqual(c.snd_una, c.iss + 1)
        self.assertEqual(c.rcv_nxt, 5001)
        self.assertEqual(raw.sent[-1].flags, ACK)
        self.assertEqual(raw.sent[-1].ack, 5001)
        self.assertEqual(c.rtx_queue, [])  # SYN が ACK されてキューから消えた

    def test_passive_open(self):
        c, raw = make_conn()
        c.remote_ip = c.remote_port = None
        c.listen()
        raw.reply(seq=7000, ack=0, flags=SYN)
        c._step()
        self.assertEqual(c.state, "SYN_RECEIVED")
        synack = raw.sent[-1]
        self.assertEqual(synack.flags, SYN | ACK)
        self.assertEqual(synack.ack, 7001)
        raw.reply(seq=7001, ack=synack.seq + 1, flags=ACK)
        c.accept()
        self.assertEqual(c.state, "ESTABLISHED")


class TestSend(unittest.TestCase):
    def test_pure_window_update_is_accepted(self):
        """ack が進まないウィンドウ更新を無視すると、相手の窓が 0 になった後で send() が止まる。"""
        c, raw = established()
        c.snd_wnd = 5

        def peer(seg):
            if seg.payload:
                acked = seg.seq + len(seg.payload)
                raw.reply(seq=seg.ack, ack=acked, flags=ACK, window=0)  # 満杯
                raw.reply(seq=seg.ack, ack=acked, flags=ACK, window=5)  # 読み出した → ウィンドウ更新

        raw.on_send = peer
        c.send(b"0123456789")  # 5 バイトずつ 2 回に分かれる
        payloads = [s.payload for s in raw.sent if s.payload]
        self.assertEqual(payloads, [b"01234", b"56789"])
        self.assertEqual(c.snd_una, c.snd_nxt)

    def test_split_by_mss(self):
        c, raw = established()
        raw.on_send = lambda seg: seg.payload and raw.reply(seq=seg.ack, ack=seg.seq + len(seg.payload), flags=ACK)
        c.send(b"x" * (conn.MSS + 1))
        payloads = [len(s.payload) for s in raw.sent if s.payload]
        self.assertEqual(payloads, [conn.MSS, 1])


class TestReceive(unittest.TestCase):
    def test_in_order_data_is_acked(self):
        c, raw = established(rcv=1)
        raw.reply(seq=1, ack=c.snd_nxt, flags=PSH | ACK, payload=b"hello")
        self.assertEqual(c.recv(), b"hello")
        self.assertEqual(c.rcv_nxt, 6)
        self.assertEqual(raw.sent[-1].ack, 6)
        # ACK は recv() でアプリが読み出す前に返るので、5 バイト分だけ狭い窓を広告している
        self.assertEqual(raw.sent[-1].window, 65535 - 5)

    def test_out_of_order_is_dropped_and_reacked(self):
        c, raw = established(rcv=1)
        raw.reply(seq=100, ack=c.snd_nxt, flags=PSH | ACK, payload=b"late")
        c._step()
        self.assertEqual(c.recv_buf, b"")
        self.assertEqual(raw.sent[-1].ack, 1)  # 「今欲しい番号」を伝え直す

    def test_fin_moves_to_close_wait(self):
        c, raw = established(rcv=1)
        raw.reply(seq=1, ack=c.snd_nxt, flags=FIN | ACK)
        self.assertEqual(c.recv(), b"")
        self.assertEqual(c.state, "CLOSE_WAIT")
        self.assertEqual(raw.sent[-1].ack, 2)  # FIN も 1 つ消費


class TestRetransmit(unittest.TestCase):
    def test_retransmits_after_rto_then_gives_up(self):
        c, raw = established()
        c.rto = 0.0  # 待たずに再送させる
        c.snd_wnd = 65535
        c._send(PSH | ACK, b"data")
        for _ in range(conn.MAX_RETRANSMITS):
            c._step()
        self.assertEqual(sum(1 for s in raw.sent if s.payload), 1 + conn.MAX_RETRANSMITS)
        with self.assertRaises(ConnectionError):
            c._step()

    def test_ack_clears_queue_and_resets_rto(self):
        c, raw = established()
        c._send(PSH | ACK, b"data")
        c.rto = 8.0
        raw.reply(seq=1, ack=c.snd_nxt, flags=ACK)
        c._step()
        self.assertEqual(c.rtx_queue, [])
        self.assertEqual(c.rto, conn.RTO_INITIAL)


class TestClose(unittest.TestCase):
    def test_active_close_goes_through_time_wait(self):
        c, raw = established()

        def peer(seg):
            if seg.fin:
                raw.reply(seq=1, ack=seg.seq + 1, flags=ACK)        # 自分の FIN への ACK
                raw.reply(seq=1, ack=seg.seq + 1, flags=FIN | ACK)  # 相手の FIN

        raw.on_send = peer
        with mock.patch.object(conn, "TIME_WAIT_SECONDS", 0.0):
            c.close()
        self.assertEqual(c.state, "CLOSED")
        self.assertTrue(raw.closed)
        self.assertEqual(raw.sent[-1].flags, ACK)
        self.assertEqual(raw.sent[-1].ack, 2)

    def test_passive_close(self):
        c, raw = established(rcv=1)
        raw.reply(seq=1, ack=c.snd_nxt, flags=FIN | ACK)
        c.recv()
        self.assertEqual(c.state, "CLOSE_WAIT")
        raw.on_send = lambda seg: seg.fin and raw.reply(seq=2, ack=seg.seq + 1, flags=ACK)
        c.close()
        self.assertEqual(c.state, "CLOSED")
        self.assertTrue(raw.closed)

    def test_close_in_other_state_still_closes_socket(self):
        c, raw = make_conn()
        c.close()
        self.assertEqual(c.state, "CLOSED")
        self.assertTrue(raw.closed)

    def test_rst_raises(self):
        c, raw = established()
        raw.reply(seq=1, ack=c.snd_nxt, flags=RST)
        with self.assertRaises(ConnectionResetError):
            c._step()
        self.assertEqual(c.state, "CLOSED")


if __name__ == "__main__":
    unittest.main()
