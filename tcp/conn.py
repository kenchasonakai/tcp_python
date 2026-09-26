"""自作TCPの本体。Step 4〜7 で少しずつ育てていく。

コード中の `[Step N]` は、その部分をどのステップで追加したかを示す。
Step 3 の steps/step03_connect.py を土台にしている。

設計方針: スレッドを使わない。
  - すべての処理は _step() を繰り返す1本のループで行う
  - _step() = 「セグメントを1つ受信して処理する」+「再送タイマーを確認する」
  - connect()/recv()/send()/close() は、目的の状態になるまで _step() を回すだけ
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

from tcp.rawsock import RawTCPSocket
from tcp.segment import ACK, FIN, PSH, SYN, Segment, build, mss_option, parse, verify_checksum

MSS = 1460                # 1セグメントに載せるデータの最大値
RTO_INITIAL = 1.0         # [Step 6] 再送タイムアウトの初期値(秒)。本物はRTTから計算する
MAX_RETRANSMITS = 5       # [Step 6] あきらめるまでの再送回数
TIME_WAIT_SECONDS = 2.0   # [Step 7] 本物は 2*MSL(Linuxは60秒)。実験用に短くしている


@dataclass
class RtxEntry:
    """[Step 6] 再送キューの1エントリ。ACKされるまで保持する。"""

    seq: int          # このセグメントの先頭seq
    end: int          # seq + seg_len。相手の ack がこの値以上なら「届いた」
    data: bytes       # 送ったバイト列そのもの
    sent_at: float
    count: int = 1    # 送信回数


class Connection:
    def __init__(
        self,
        local_ip: str,
        local_port: int,
        remote_ip: Optional[str] = None,   # listen する側は接続が来るまで未定
        remote_port: Optional[int] = None,
        loss: float = 0.0,                 # [Step 6] 送信をわざと落とす確率(実験用)
        loss_in: float = 0.0,              # [Step 6] 受信をわざと落とす確率(実験用)
    ):
        self.local_ip, self.local_port = local_ip, local_port
        self.remote_ip, self.remote_port = remote_ip, remote_port
        self.raw = RawTCPSocket(timeout=0.05)
        self.state = "CLOSED"
        self.started = time.time()

        # 送信側の変数(RFC 9293 3.3.1)
        self.iss = 0       # 自分の初期シーケンス番号
        self.snd_una = 0   # まだACKされていない最古のseq
        self.snd_nxt = 0   # 次に送るseq
        self.snd_wnd = 0   # 相手が「あとこれだけ受け取れる」と広告した量

        # 受信側の変数
        self.irs = 0       # 相手の初期シーケンス番号
        self.rcv_nxt = 0   # 次に受け取るはずのseq(= 相手に返すack)
        self.rcv_wnd = 65535

        self.recv_buf = bytearray()      # [Step 5] アプリがまだ読んでいない受信データ
        self.fin_received = False        # [Step 5] 相手が「もう送らない」と言った
        self.rtx_queue: list[RtxEntry] = []   # [Step 6]
        self.rto = RTO_INITIAL                # [Step 6]
        self.loss, self.loss_in = loss, loss_in

    # ------------------------------------------------------------------
    # 低レベル: 送信・受信・状態
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        print(f"[{time.time() - self.started:7.3f}] {msg}", flush=True)

    def _set_state(self, new: str):
        self._log(f"    state: {self.state} -> {new}")
        self.state = new

    def _send(self, flags: int, payload: bytes = b"", options: bytes = b""):
        """seq=snd_nxt, ack=rcv_nxt でセグメントを送り、snd_nxt を進める。"""
        window = max(0, self.rcv_wnd - len(self.recv_buf))  # [Step 5] 空き分だけ広告する
        seg_bytes = build(
            self.local_ip, self.remote_ip, self.local_port, self.remote_port,
            seq=self.snd_nxt, ack=self.rcv_nxt, flags=flags, window=window,
            payload=payload, options=options,
        )
        seg = parse(seg_bytes)
        self._transmit(seg_bytes, seg)
        if seg.seg_len:
            # [Step 6] SYN・FIN・データはACKが返るまで再送キューに入れる。
            # 純粋なACK(seg_len=0)は再送しない(相手が必要なら再度送ってくる)
            self.rtx_queue.append(RtxEntry(seg.seq, seg.seq + seg.seg_len, seg_bytes, time.time()))
        self.snd_nxt += seg.seg_len

    def _transmit(self, seg_bytes: bytes, seg: Segment, retransmit: bool = False):
        if self.loss and random.random() < self.loss:
            self._log(f"-x> {seg}  (わざと落とした)")
            return
        self.raw.send(self.remote_ip, seg_bytes)
        self._log(f"{'-R>' if retransmit else '-->'} {seg}{'  (再送)' if retransmit else ''}")

    def _recv(self) -> Optional[tuple[str, Segment]]:
        """この接続宛てのセグメントを1つ返す。無ければ None。"""
        got = self.raw.recv()
        if got is None:
            return None
        src_ip, dst_ip, tcp_bytes = got
        seg = parse(tcp_bytes)
        if seg.dst_port != self.local_port:
            return None
        if self.remote_port is not None and (src_ip != self.remote_ip or seg.src_port != self.remote_port):
            return None
        if not verify_checksum(src_ip, dst_ip, tcp_bytes):
            self._log(f"    (checksum が壊れているので捨てた: {seg})")
            return None
        if self.loss_in and random.random() < self.loss_in:
            self._log(f"<x- {seg}  (わざと落とした)")
            return None
        self._log(f"<-- {seg}")
        return src_ip, seg

    def _step(self):
        """イベントループの1周。"""
        got = self._recv()
        if got is not None:
            self._handle(*got)
        self._check_retransmit()

    def _run_until(self, cond: Callable[[], bool], timeout: Optional[float] = None) -> bool:
        """cond() が真になるまで _step() を回す。timeout 秒たったら False を返す。"""
        deadline = time.time() + timeout if timeout else None
        while not cond():
            if deadline and time.time() > deadline:
                return False
            self._step()
        return True

    # ------------------------------------------------------------------
    # [Step 6] 再送
    # ------------------------------------------------------------------

    def _check_retransmit(self):
        if not self.rtx_queue:
            return
        head = self.rtx_queue[0]  # 一番古い未ACKセグメントだけ見る
        if time.time() - head.sent_at < self.rto:
            return
        if head.count > MAX_RETRANSMITS:
            raise ConnectionError(f"{MAX_RETRANSMITS}回再送してもACKが来ない。あきらめる")
        self._log(f"    RTO={self.rto:.1f}s 経過。seq {head.seq} を再送({head.count}回目)")
        self._transmit(head.data, parse(head.data), retransmit=True)
        head.sent_at = time.time()
        head.count += 1
        self.rto = min(self.rto * 2, 60.0)  # 指数バックオフ(RFC 6298 5.5)

    def _ack_received(self, ack: int, window: int):
        """相手の ack で snd_una を進め、届いた分を再送キューから消す。"""
        self.snd_una = ack
        self.snd_wnd = window
        before = len(self.rtx_queue)
        self.rtx_queue = [e for e in self.rtx_queue if e.end > ack]  # [Step 6]
        if len(self.rtx_queue) < before:
            self.rto = RTO_INITIAL  # 新しいACKが来たらバックオフを戻す

    # ------------------------------------------------------------------
    # 状態ごとのセグメント処理
    # ------------------------------------------------------------------

    def _handle(self, src_ip: str, seg: Segment):
        if self.state == "LISTEN":
            self._handle_listen(src_ip, seg)
        elif self.state == "SYN_SENT":
            self._handle_syn_sent(seg)
        else:
            self._handle_other(seg)

    def _handle_listen(self, src_ip: str, seg: Segment):
        """[Step 4] 受動オープン: SYN を待ち、SYN/ACK を返す。"""
        if seg.rst or seg.ack_flag:
            return  # ACK付きが来たら本来はRSTを返す(省略)
        if not seg.syn:
            return
        self.remote_ip, self.remote_port = src_ip, seg.src_port
        self.irs = seg.seq
        self.rcv_nxt = seg.seq + 1
        self.iss = random.randint(0, 2**32 - 1)
        self.snd_una = self.snd_nxt = self.iss
        self.snd_wnd = seg.window
        self._send(SYN | ACK, options=mss_option(MSS))
        self._set_state("SYN_RECEIVED")

    def _handle_syn_sent(self, seg: Segment):
        """[Step 3] 能動オープン: SYN/ACK を待ち、ACK を返す。"""
        if seg.ack_flag and not (self.snd_una < seg.ack <= self.snd_nxt):
            self._log("    ack が範囲外なので無視")
            return
        if seg.rst:
            self._set_state("CLOSED")
            return
        if seg.syn and seg.ack_flag:
            self.irs = seg.seq
            self.rcv_nxt = seg.seq + 1
            self._ack_received(seg.ack, seg.window)
            self._send(ACK)
            self._set_state("ESTABLISHED")

    def _handle_other(self, seg: Segment):
        """SYN_RECEIVED 以降のすべての状態に共通の処理(RFC 9293 3.10.7.4 の構造)。

        1. RST → 接続を捨てる
        2. ACK → snd_una を進め、状態を遷移させる
        3. データ・FIN → 順番どおりなら受け入れて ACK を返す
        """
        # 1. RST
        if seg.rst:
            self._set_state("CLOSED")
            raise ConnectionResetError("相手から RST が届いた")

        # 2. ACK(ACKが立っていないセグメントは無視する)
        if not seg.ack_flag:
            return
        if self.snd_una < seg.ack <= self.snd_nxt:
            self._ack_received(seg.ack, seg.window)
            all_acked = self.snd_una == self.snd_nxt
            if self.state == "SYN_RECEIVED":
                self._set_state("ESTABLISHED")          # [Step 4] 3発目のACKが来た
            elif self.state == "FIN_WAIT_1" and all_acked:
                self._set_state("FIN_WAIT_2")           # [Step 7] 自分のFINが確認された
            elif self.state == "CLOSING" and all_acked:
                self._set_state("TIME_WAIT")            # [Step 7]
            elif self.state == "LAST_ACK" and all_acked:
                self._set_state("CLOSED")               # [Step 7]
        elif seg.ack > self.snd_nxt:
            self._log("    まだ送っていない分への ACK。無視")
            return

        # 3. データと FIN
        if seg.seg_len == 0:
            return  # 純粋な ACK。ここで終わり
        if seg.seq != self.rcv_nxt:
            # 重複(seq < rcv_nxt)か、間が抜けている(seq > rcv_nxt)。
            # 本物は後者をバッファに取っておくが、ここでは捨てて「今欲しい番号」を伝え直す
            self._log(f"    seq {seg.seq} != rcv_nxt {self.rcv_nxt}。捨てて ACK を返す")
            self._send(ACK)
            return
        if seg.payload:
            self.recv_buf += seg.payload                # [Step 5]
            self.rcv_nxt += len(seg.payload)
        if seg.fin:
            self.rcv_nxt += 1                           # [Step 5] FINも1つ消費する
            self.fin_received = True
            if self.state in ("ESTABLISHED", "SYN_RECEIVED"):
                self._set_state("CLOSE_WAIT")           # [Step 7] 相手が先に閉じた
            elif self.state == "FIN_WAIT_1":
                self._set_state("CLOSING")              # [Step 7] 同時クローズ
            elif self.state == "FIN_WAIT_2":
                self._set_state("TIME_WAIT")            # [Step 7]
        self._send(ACK)

    # ------------------------------------------------------------------
    # アプリ向け API
    # ------------------------------------------------------------------

    def connect(self):
        """[Step 3] 能動オープン。ESTABLISHED になるまでブロックする。"""
        self.iss = random.randint(0, 2**32 - 1)
        self.snd_una = self.snd_nxt = self.iss
        self._send(SYN, options=mss_option(MSS))
        self._set_state("SYN_SENT")
        self._run_until(lambda: self.state != "SYN_SENT")
        if self.state != "ESTABLISHED":
            raise ConnectionRefusedError("接続を拒否された")

    def listen(self):
        """[Step 4] 受動オープンの準備。"""
        self._set_state("LISTEN")

    def accept(self):
        """[Step 4] 接続が完了するまでブロックする。(1接続しか扱わない簡略版)"""
        self._run_until(lambda: self.state == "ESTABLISHED")

    def recv(self, max_bytes: int = 65535) -> bytes:
        """[Step 5] データが届くまでブロックする。相手が閉じていたら b"" を返す。"""
        self._run_until(lambda: len(self.recv_buf) > 0 or self.fin_received)
        data = bytes(self.recv_buf[:max_bytes])
        del self.recv_buf[:max_bytes]
        return data

    def send(self, data: bytes):
        """[Step 5] data を MSS ごとに分けて送り、すべて ACK されるまでブロックする。"""
        offset = 0
        while offset < len(data) or self.snd_una < self.snd_nxt:
            in_flight = self.snd_nxt - self.snd_una       # 送ったがまだACKされていない量
            room = self.snd_wnd - in_flight               # 相手のウィンドウの残り
            if offset < len(data) and room > 0:
                chunk = data[offset : offset + min(MSS, room)]
                self._send(PSH | ACK, chunk)
                offset += len(chunk)
            else:
                self._step()  # ACK やウィンドウ更新を待つ

    def close(self):
        """[Step 7] FIN を送って接続を閉じる。"""
        if self.state == "ESTABLISHED":
            self._send(FIN | ACK)
            self._set_state("FIN_WAIT_1")       # 自分から閉じる(能動クローズ)
        elif self.state == "CLOSE_WAIT":
            self._send(FIN | ACK)
            self._set_state("LAST_ACK")         # 相手が先に閉じていた(受動クローズ)
        else:
            self._set_state("CLOSED")
            return
        self._run_until(lambda: self.state in ("TIME_WAIT", "CLOSED"))
        if self.state == "TIME_WAIT":
            self._log(f"    TIME_WAIT: {TIME_WAIT_SECONDS}秒待つ(遅れて届く相手のFIN再送にACKを返すため)")
            self._run_until(lambda: False, timeout=TIME_WAIT_SECONDS)
            self._set_state("CLOSED")
        self.raw.close()
