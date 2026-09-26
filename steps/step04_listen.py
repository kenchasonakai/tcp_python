"""Step 4: 受動オープン(listen / accept)。カーネルから接続されてみる。

    lab/lab.sh exec host1 python3 steps/step04_listen.py 9000
    lab/lab.sh exec host2 nc 10.0.0.1 9000          # 別の端末で
"""

import sys
import time

from tcp.conn import Connection


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    conn = Connection("10.0.0.1", port)
    conn.listen()
    print(f"port {port} で待っています。 lab/lab.sh exec host2 nc 10.0.0.1 {port} で接続してください")
    conn.accept()
    print(f"\n接続完了: {conn.remote_ip}:{conn.remote_port} から")
    print("10秒待ちます。 lab/lab.sh exec host2 ss -tan で相手側の状態を見てください")
    time.sleep(10)


if __name__ == "__main__":
    main()
