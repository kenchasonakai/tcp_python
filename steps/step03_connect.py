"""Step 3: 3ウェイハンドシェイクを完成させる(能動オープン)。

    lab/lab.sh exec host2 nc -l -p 9000
    lab/lab.sh exec host1 python3 steps/step03_connect.py 9000

ESTABLISHED になったら10秒待つので、その間に相手側の状態を見る:

    lab/lab.sh exec host2 ss -tan
"""

import random
import sys
import time

from tcp.rawsock import RawTCPSocket
from tcp.segment import ACK, SYN, Segment, build, mss_option, parse, verify_checksum

LOCAL_IP = "10.0.0.1"
REMOTE_IP = "10.0.0.2"


class Connection:
    def __init__(self, local_port: int, remote_port: int):
        self.local_port = local_port
        self.remote_port = remote_port
        self.raw = RawTCPSocket(timeout=0.1)
        self.state = "CLOSED"

        # 送信側の変数(RFC 9293 3.3.1 の名前をそのまま使う)
        self.iss = 0      # Initial Send Sequence: 自分の初期シーケンス番号
        self.snd_una = 0  # Send UNAcknowledged: まだACKされていない最古のseq
        self.snd_nxt = 0  # Send NeXT: 次に送るseq
        self.snd_wnd = 0  # 相手が受け取れる量(相手が広告してくるウィンドウ)

        # 受信側の変数
        self.irs = 0      # Initial Receive Sequence: 相手の初期シーケンス番号
        self.rcv_nxt = 0  # Receive NeXT: 次に受け取るはずのseq(= 相手に返すack)
        self.rcv_wnd = 65535

    # --- 低レベル ---

    def _set_state(self, new: str):
        print(f"    state: {self.state} -> {new}")
        self.state = new

    def _send(self, flags: int, payload: bytes = b"", options: bytes = b""):
        seg = build(
            LOCAL_IP, REMOTE_IP, self.local_port, self.remote_port,
            seq=self.snd_nxt, ack=self.rcv_nxt, flags=flags, window=self.rcv_wnd,
            payload=payload, options=options,
        )
        self.raw.send(REMOTE_IP, seg)
        print(f"--> {parse(seg)}")

    def _recv(self):
        """この接続宛てのセグメントを1つ返す。タイムアウトや無関係なパケットなら None。"""
        got = self.raw.recv()
        if got is None:
            return None
        src_ip, dst_ip, tcp_bytes = got
        seg = parse(tcp_bytes)
        if src_ip != REMOTE_IP or seg.src_port != self.remote_port or seg.dst_port != self.local_port:
            return None
        if not verify_checksum(src_ip, dst_ip, tcp_bytes):
            print(f"    (checksum が壊れているので捨てた: {seg})")
            return None
        print(f"<-- {seg}")
        return seg

    # --- 状態ごとの処理 ---

    def connect(self):
        self.iss = random.randint(0, 2**32 - 1)
        self.snd_una = self.iss
        self.snd_nxt = self.iss
        self._send(SYN, options=mss_option(1460))
        self.snd_nxt += 1  # SYN はシーケンス番号を1つ消費する
        self._set_state("SYN_SENT")

        while self.state == "SYN_SENT":
            seg = self._recv()
            if seg is not None:
                self._handle_syn_sent(seg)
        if self.state != "ESTABLISHED":
            raise ConnectionError("接続に失敗した")

    def _handle_syn_sent(self, seg: Segment):
        # RFC 9293 3.10.7.3 "SYN-SENT STATE" を簡略化したもの
        if seg.ack_flag and not (self.snd_una < seg.ack <= self.snd_nxt):
            print("    ack が範囲外なので無視")
            return
        if seg.rst:
            self._set_state("CLOSED")
            return
        if seg.syn and seg.ack_flag:
            self.irs = seg.seq
            self.rcv_nxt = seg.seq + 1   # 相手のSYNも1つ消費する
            self.snd_una = seg.ack       # 自分のSYNが確認された
            self.snd_wnd = seg.window
            self._send(ACK)              # 3発目
            self._set_state("ESTABLISHED")


def main():
    remote_port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    conn = Connection(local_port=random.randint(40000, 60000), remote_port=remote_port)
    conn.connect()
    print(f"\n接続完了。snd_nxt={conn.snd_nxt} rcv_nxt={conn.rcv_nxt}")
    print("10秒待ちます。今のうちに  lab/lab.sh exec host2 ss -tan  で相手側の状態を見てください")
    time.sleep(10)
    print("close せずに終了します(相手側に接続が残るのを観察してみてください)")


if __name__ == "__main__":
    main()
