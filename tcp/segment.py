"""TCPセグメント(ヘッダ+ペイロード)の組み立てと解析。

Step 1 で作るモジュール。以降のすべてのステップがこれを使う。

TCPヘッダのレイアウト(RFC 9293 3.1):

     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-------------------------------+-------------------------------+
    |          Source Port          |       Destination Port        |  0-3
    +-------------------------------+-------------------------------+
    |                        Sequence Number                        |  4-7
    +---------------------------------------------------------------+
    |                    Acknowledgment Number                      |  8-11
    +-------+-------+-+-+-+-+-+-+-+-+-------------------------------+
    | Data  | Rsvd  |C|E|U|A|P|R|S|F|                               |
    | Offset|       |W|C|R|C|S|S|Y|I|            Window             |  12-15
    |       |       |R|E|G|K|H|T|N|N|                               |
    +-------+-------+-+-+-+-+-+-+-+-+-------------------------------+
    |           Checksum            |         Urgent Pointer        |  16-19
    +-------------------------------+-------------------------------+
    |                    Options (あれば)                     |  Pad |  20-
    +---------------------------------------------------------------+
    |                             data                              |
    +---------------------------------------------------------------+
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# フラグ(13バイト目の各ビット)
FIN = 0x01
SYN = 0x02
RST = 0x04
PSH = 0x08
ACK = 0x10
URG = 0x20

# '!' はネットワークバイトオーダー(ビッグエンディアン)。
# H=2バイト, I=4バイト, B=1バイト。合計 2+2+4+4+1+1+2+2+2 = 20バイト
HEADER_FORMAT = "!HHIIBBHHH"
HEADER_LEN = struct.calcsize(HEADER_FORMAT)  # 20


def checksum(data: bytes) -> int:
    """インターネットチェックサム(RFC 1071)。

    データを2バイトずつ足し合わせ、桁あふれを下位に折り返し、最後にビット反転する。
    """
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def pseudo_header(src_ip: str, dst_ip: str, tcp_len: int) -> bytes:
    """チェックサム計算にだけ使う疑似ヘッダ(実際には送信されない)。

    IPアドレスを混ぜて計算することで「宛先を間違えて届いたセグメント」も検出できる。
    """
    return (
        _ip_to_bytes(src_ip)
        + _ip_to_bytes(dst_ip)
        + struct.pack("!BBH", 0, 6, tcp_len)  # 0, プロトコル番号(TCP=6), TCP長
    )


def _ip_to_bytes(ip: str) -> bytes:
    return bytes(int(x) for x in ip.split("."))


@dataclass
class Segment:
    src_port: int
    dst_port: int
    seq: int
    ack: int
    flags: int
    window: int
    checksum: int = 0
    urgent: int = 0
    options: bytes = b""
    payload: bytes = b""

    # --- フラグの判定 ---
    @property
    def syn(self) -> bool:
        return bool(self.flags & SYN)

    @property
    def ack_flag(self) -> bool:
        return bool(self.flags & ACK)

    @property
    def fin(self) -> bool:
        return bool(self.flags & FIN)

    @property
    def rst(self) -> bool:
        return bool(self.flags & RST)

    @property
    def seg_len(self) -> int:
        """シーケンス番号空間で消費する長さ。SYNとFINは1バイト分として数える。"""
        return len(self.payload) + (1 if self.syn else 0) + (1 if self.fin else 0)

    def __str__(self) -> str:
        return (
            f"{self.src_port} > {self.dst_port}: Flags {flags_str(self.flags)}, "
            f"seq {self.seq}, ack {self.ack}, win {self.window}, length {len(self.payload)}"
        )


def flags_str(flags: int) -> str:
    """tcpdump風のフラグ表記。例: SYN+ACK → [S.]"""
    s = ""
    if flags & SYN:
        s += "S"
    if flags & FIN:
        s += "F"
    if flags & RST:
        s += "R"
    if flags & PSH:
        s += "P"
    if flags & ACK:
        s += "."
    return f"[{s}]"


def build(
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    window: int,
    payload: bytes = b"",
    options: bytes = b"",
) -> bytes:
    """TCPセグメントのバイト列を作る(チェックサム計算済み)。"""
    if len(options) % 4:
        raise ValueError("options は4バイトの倍数にすること(パディングが必要)")
    data_offset = (HEADER_LEN + len(options)) // 4  # ヘッダ長を4バイト単位で

    def pack(csum: int) -> bytes:
        header = struct.pack(
            HEADER_FORMAT,
            src_port,
            dst_port,
            seq & 0xFFFFFFFF,
            ack & 0xFFFFFFFF,
            data_offset << 4,  # 上位4ビットがデータオフセット、下位4ビットは予約(0)
            flags,
            window,
            csum,
            0,  # urgent pointer(使わない)
        )
        return header + options + payload

    # まずチェックサム欄を0にして全体を作り、その値を埋め直す
    tcp_bytes = pack(0)
    csum = checksum(pseudo_header(src_ip, dst_ip, len(tcp_bytes)) + tcp_bytes)
    return pack(csum)


def parse(data: bytes) -> Segment:
    """バイト列からSegmentを取り出す。"""
    if len(data) < HEADER_LEN:
        raise ValueError(f"短すぎる: {len(data)} bytes")
    (src_port, dst_port, seq, ack, off_rsvd, flags, window, csum, urgent) = struct.unpack(
        HEADER_FORMAT, data[:HEADER_LEN]
    )
    header_len = (off_rsvd >> 4) * 4
    return Segment(
        src_port=src_port,
        dst_port=dst_port,
        seq=seq,
        ack=ack,
        flags=flags,
        window=window,
        checksum=csum,
        urgent=urgent,
        options=data[HEADER_LEN:header_len],
        payload=data[header_len:],
    )


def verify_checksum(src_ip: str, dst_ip: str, data: bytes) -> bool:
    """受信したセグメントのチェックサムを検証する。

    チェックサム欄を含めて全体を足すと、正しければ結果が0になる。
    """
    return checksum(pseudo_header(src_ip, dst_ip, len(data)) + data) == 0


# --- オプション ---

def mss_option(mss: int) -> bytes:
    """MSSオプション(kind=2, len=4)。SYNにだけ付ける。"""
    return struct.pack("!BBH", 2, 4, mss)


def parse_options(options: bytes) -> dict:
    """オプション列を {kind: value} に分解する。MSS(kind=2)だけ値を解釈する。"""
    result = {}
    i = 0
    while i < len(options):
        kind = options[i]
        if kind == 0:  # End of Option List
            break
        if kind == 1:  # NOP(パディング)
            i += 1
            continue
        if i + 1 >= len(options):
            raise ValueError(f"オプションが途中で切れている: {options.hex()}")
        length = options[i + 1]
        if length < 2 or i + length > len(options):
            # length=0 を許すと i が進まず無限ループになる
            raise ValueError(f"オプション kind={kind} の length={length} が不正: {options.hex()}")
        value = options[i + 2 : i + length]
        if kind == 2:
            result["mss"] = struct.unpack("!H", value)[0]
        else:
            result[kind] = value
        i += length
    return result
