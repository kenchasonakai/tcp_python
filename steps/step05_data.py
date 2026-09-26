"""Step 5: データの送受信(send / recv)。

エコーサーバー(自作TCP側が待ち受け、受け取った文字をそのまま返す):

    lab/lab.sh exec host1 python3 steps/step05_data.py server 9000
    lab/lab.sh exec host2 nc 10.0.0.1 9000          # 別の端末で。文字を打つと返ってくる

クライアント(カーネルの nc が待ち受け、自作TCP側から送る):

    lab/lab.sh exec host2 nc -l -p 9000
    lab/lab.sh exec host1 python3 steps/step05_data.py client 9000 "hello from toy tcp"

--loss 0.3 を付けると送信の30%をわざと落とす(Step 6 で使う)
"""

import argparse
import random

from tcp.conn import Connection


def server(port: int, loss: float, loss_in: float):
    conn = Connection("10.0.0.1", port, loss=loss, loss_in=loss_in)
    conn.listen()
    print(f"port {port} で待っています")
    conn.accept()
    while True:
        data = conn.recv()
        if not data:
            print("相手が FIN を送ってきた(これ以上データは来ない)")
            break
        print(f"    受信: {data!r} → そのまま送り返す")
        conn.send(data)
    print("終了します(close は Step 7 で実装する)")


def client(port: int, message: str, loss: float, loss_in: float):
    conn = Connection("10.0.0.1", random.randint(40000, 60000), "10.0.1.1", port, loss=loss, loss_in=loss_in)
    conn.connect()
    conn.send(message.encode() + b"\n")
    print("    送信完了(すべて ACK された)。相手からの返事を待ちます(nc 側で何か打って Enter)")
    reply = conn.recv()
    print(f"    受信: {reply!r}")
    print("終了します(close は Step 7 で実装する)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["server", "client"])
    ap.add_argument("port", type=int, nargs="?", default=9000)
    ap.add_argument("message", nargs="?", default="hello from toy tcp")
    ap.add_argument("--loss", type=float, default=0.0, help="送信をわざと落とす確率")
    ap.add_argument("--loss-in", type=float, default=0.0, help="受信をわざと落とす確率")
    args = ap.parse_args()
    if args.mode == "server":
        server(args.port, args.loss, args.loss_in)
    else:
        client(args.port, args.message, args.loss, args.loss_in)


if __name__ == "__main__":
    main()
