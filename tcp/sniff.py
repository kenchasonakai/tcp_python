"""自作ミニtcpdump。

ラボ環境(ユーザー名前空間)では本物の tcpdump が動かないので、
Step 1 で作った segment.parse() を使って TCP パケットを tcpdump 風に表示する。

使い方:
    python3 -m tcp.sniff <interface> [-w out.pcap] [-S]

    -w  pcapファイルにも保存する(Wiresharkで開ける)
    -S  シーケンス番号を絶対値で表示する(既定は tcpdump と同じく SYN を 0 とした相対値)
"""

import argparse
import socket
import struct
import sys
import time

from tcp.segment import flags_str, parse

ETH_P_ALL = 0x0003
ETH_P_IP = 0x0800
ETH_HEADER_LEN = 14


def ip_str(b: bytes) -> str:
    return ".".join(str(x) for x in b)


class PcapWriter:
    """pcap形式(Wiresharkが読める最もシンプルな形式)で書き出す。"""

    def __init__(self, path: str):
        self.f = open(path, "wb")
        # magic, version 2.4, thiszone, sigfigs, snaplen, linktype(1=Ethernet)
        self.f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))

    def write(self, frame: bytes, ts: float):
        sec = int(ts)
        usec = int((ts - sec) * 1_000_000)
        self.f.write(struct.pack("<IIII", sec, usec, len(frame), len(frame)))
        self.f.write(frame)
        self.f.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iface")
    ap.add_argument("-w", "--write", help="pcapファイルに保存")
    ap.add_argument("-S", "--absolute", action="store_true", help="seq/ackを絶対値で表示")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    sock.bind((args.iface, 0))
    pcap = PcapWriter(args.write) if args.write else None
    isn = {}  # (src_ip, src_port, dst_ip, dst_port) → 初期シーケンス番号
    print(f"listening on {args.iface} (Ctrl+C で終了)", file=sys.stderr)
    start = time.time()

    while True:
        frame, _ = sock.recvfrom(65535)
        now = time.time()
        if pcap:
            pcap.write(frame, now)

        ethertype = struct.unpack("!H", frame[12:14])[0]
        if ethertype != ETH_P_IP:
            continue
        ip = frame[ETH_HEADER_LEN:]
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 6:  # プロトコル番号 6 = TCP
            continue
        src_ip, dst_ip = ip_str(ip[12:16]), ip_str(ip[16:20])
        total_len = struct.unpack("!H", ip[2:4])[0]
        seg = parse(ip[ihl:total_len])

        # tcpdump 風の相対シーケンス番号
        seq, ack = seg.seq, seg.ack
        if not args.absolute:
            fwd = (src_ip, seg.src_port, dst_ip, seg.dst_port)
            rev = (dst_ip, seg.dst_port, src_ip, seg.src_port)
            if seg.syn:
                isn[fwd] = seg.seq
            if fwd in isn:
                seq = (seg.seq - isn[fwd]) & 0xFFFFFFFF
            if seg.ack_flag and rev in isn:
                ack = (seg.ack - isn[rev]) & 0xFFFFFFFF

        seq_str = f"{seq}:{seq + len(seg.payload)}" if seg.payload else str(seq)
        ack_str = f", ack {ack}" if seg.ack_flag else ""
        line = (
            f"{now - start:8.3f}  {src_ip}.{seg.src_port} > {dst_ip}.{seg.dst_port}: "
            f"Flags {flags_str(seg.flags)}, seq {seq_str}{ack_str}, win {seg.window}, "
            f"length {len(seg.payload)}"
        )
        if seg.payload:
            preview = seg.payload[:40].decode("ascii", "replace").replace("\n", "\\n")
            line += f'  "{preview}"'
        print(line, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
