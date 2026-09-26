"""Step 7: 切断(close)。能動クローズと受動クローズの両方を見る。

能動クローズ(自作側から FIN を送る → FIN_WAIT_1 → FIN_WAIT_2 → TIME_WAIT):

    lab/lab.sh exec host2 nc -l -p 9000
    lab/lab.sh exec host1 python3 steps/step07_close.py client 9000

受動クローズ(相手が先に FIN → CLOSE_WAIT → 自分も FIN → LAST_ACK):

    lab/lab.sh exec host1 python3 steps/step07_close.py server 9000
    lab/lab.sh exec host2 nc 10.0.0.1 9000        # 何か打ってから Ctrl+C か Ctrl+D で閉じる
"""

import random
import sys

from tcp.conn import Connection


def client(port: int):
    conn = Connection("10.0.0.1", random.randint(40000, 60000), "10.0.0.2", port)
    conn.connect()
    conn.send(b"bye\n")
    print("\n自分から閉じます(能動クローズ)")
    conn.close()
    print("閉じました。 lab/lab.sh exec host2 ss -tan で相手側に何も残っていないことを確認")


def server(port: int):
    conn = Connection("10.0.0.1", port)
    conn.listen()
    print(f"port {port} で待っています")
    conn.accept()
    while True:
        data = conn.recv()
        if not data:
            break
        conn.send(data)
    print("\n相手が先に閉じた(受動クローズ)。こちらも閉じます")
    conn.close()
    print("閉じました。 lab/lab.sh exec host2 ss -tan で相手側が TIME-WAIT なのを確認")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "client"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    (client if mode == "client" else server)(port)


if __name__ == "__main__":
    main()
