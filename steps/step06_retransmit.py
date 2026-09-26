"""Step 6: 再送。わざとパケットを落として、再送で回復する様子を見る。

    lab/lab.sh exec host2 nc -l -p 9000 > /dev/null
    lab/lab.sh exec host1 python3 steps/step06_retransmit.py 9000 --loss 0.3

送信の30%を落としても、最終的にすべてのデータが ACK されることを確認する。
--loss-in 0.3 にすると、相手(カーネル)からのパケットを落とす。カーネル側の再送が見える。
"""

import argparse
import random

from tcp.conn import Connection


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int, nargs="?", default=9000)
    ap.add_argument("--loss", type=float, default=0.3)
    ap.add_argument("--loss-in", type=float, default=0.0)
    ap.add_argument("--lines", type=int, default=5)
    ap.add_argument("--seed", type=int, help="乱数の種。同じ落ち方を再現したいとき")
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    conn = Connection(
        "10.0.0.1", random.randint(40000, 60000), "10.0.0.2", args.port,
        loss=args.loss, loss_in=args.loss_in,
    )
    conn.connect()
    for i in range(1, args.lines + 1):
        conn.send(f"line {i}\n".encode())
    print(f"\n{args.lines} 行すべて ACK された。再送キューの残り: {len(conn.rtx_queue)}")
    conn.close()


if __name__ == "__main__":
    main()
