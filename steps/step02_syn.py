"""Step 2: SYN を1発だけ送って、カーネルの返事を見る。

    lab/lab.sh exec host2 nc -l -p 9000          # 相手役(別の端末で)
    lab/lab.sh exec host1 python3 steps/step02_syn.py 9000

まだ3ウェイハンドシェイクの3発目(ACK)は返さない。
そのとき相手がどう振る舞うか(SYN/ACKの再送)も観察する。
"""

import random
import sys
import time

from tcp.rawsock import RawTCPSocket
from tcp.segment import SYN, build, mss_option, parse, parse_options, verify_checksum

LOCAL_IP = "10.0.0.1"
REMOTE_IP = "10.0.1.1"


def main():
    remote_port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    local_port = random.randint(40000, 60000)
    iss = random.randint(0, 2**32 - 1)  # 初期シーケンス番号(Initial Send Sequence number)

    raw = RawTCPSocket(timeout=1.0)
    syn = build(
        LOCAL_IP, REMOTE_IP, local_port, remote_port,
        seq=iss, ack=0, flags=SYN, window=65535,
        options=mss_option(1460),
    )
    raw.send(REMOTE_IP, syn)
    print(f"--> {parse(syn)}")
    print(f"    (iss = {iss})")

    deadline = time.time() + 8
    while time.time() < deadline:
        got = raw.recv()
        if got is None:
            continue
        src_ip, dst_ip, tcp_bytes = got
        seg = parse(tcp_bytes)
        if seg.dst_port != local_port:
            continue  # 自分宛てでないものは無視
        ok = verify_checksum(src_ip, dst_ip, tcp_bytes)
        print(f"<-- {seg}  checksum={'OK' if ok else 'BAD'}")
        if seg.syn and seg.ack_flag:
            print(f"    ack - iss = {seg.ack - iss}  (相手は iss+1 を要求している)")
            print(f"    相手の options = {parse_options(seg.options)}")
        if seg.rst:
            print("    RST: 相手はこのポートで待ち受けていない")
            return
    print("(8秒たったので終了)")


if __name__ == "__main__":
    main()
