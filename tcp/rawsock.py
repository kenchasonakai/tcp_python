"""rawソケットでTCPセグメントをIP層に直接出し入れする(Step 2 で作る)。

    socket(AF_INET, SOCK_RAW, IPPROTO_TCP)

- 送信: TCPセグメントのバイト列を渡すと、カーネルがIPヘッダを付けて送ってくれる
- 受信: このホストに届いた TCP パケット全部が「IPヘッダ付き」で読める
  (カーネル自身のTCPにも同じパケットが渡る。二重に処理される点に注意)
"""

from __future__ import annotations

import socket
import struct
from typing import Optional


def _ip_str(b: bytes) -> str:
    return ".".join(str(x) for x in b)


class RawTCPSocket:
    def __init__(self, timeout: float = 0.1):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
        self.sock.settimeout(timeout)

    def send(self, dst_ip: str, segment: bytes) -> None:
        # ポート番号はTCPヘッダの中にあるので、アドレスタプルのポートは使われない
        self.sock.sendto(segment, (dst_ip, 0))

    def recv(self) -> Optional[tuple[str, str, bytes]]:
        """(送信元IP, 宛先IP, TCPセグメント) を返す。タイムアウトなら None。"""
        try:
            packet, _ = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        # IPv4ヘッダ: 先頭バイトの下位4ビットがヘッダ長(4バイト単位)
        ihl = (packet[0] & 0x0F) * 4
        total_len = struct.unpack("!H", packet[2:4])[0]
        src_ip = _ip_str(packet[12:16])
        dst_ip = _ip_str(packet[16:20])
        return src_ip, dst_ip, packet[ihl:total_len]

    def close(self) -> None:
        self.sock.close()
