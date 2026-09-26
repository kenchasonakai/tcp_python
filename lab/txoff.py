"""チェックサムオフロード(tx-checksumming)を無効にする。`ethtool -K <if> tx off` と同じ。

ethtool が無い環境でも動くように、ioctl(SIOCETHTOOL) を直接呼ぶ。
"""

import ctypes
import fcntl
import socket
import struct
import sys

SIOCETHTOOL = 0x8946
ETHTOOL_GTXCSUM = 0x16  # 取得
ETHTOOL_STXCSUM = 0x17  # 設定


def ethtool(sock, iface: str, cmd: int, value: int) -> int:
    buf = ctypes.create_string_buffer(struct.pack("II", cmd, value))
    ifreq = struct.pack("16sP", iface.encode(), ctypes.addressof(buf))
    fcntl.ioctl(sock, SIOCETHTOOL, ifreq)
    return struct.unpack("II", buf.raw[:8])[1]


def main():
    iface = sys.argv[1]
    with socket.socket() as s:
        ethtool(s, iface, ETHTOOL_STXCSUM, 0)
        now = ethtool(s, iface, ETHTOOL_GTXCSUM, 0)
    print(f"{iface}: tx-checksumming = {'off' if now == 0 else 'on'}")


if __name__ == "__main__":
    main()
