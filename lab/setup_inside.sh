#!/usr/bin/env bash
# unshare で作ったユーザー名前空間の中で実行される。lab.sh から呼ばれる。
#
#   ┌── host1 (自作TCP) ──┐          ┌── host2 (カーネルTCP) ──┐
#   │ 10.0.0.1  h1        ├── veth ──┤ h2  10.0.0.2            │
#   └─────────────────────┘          └─────────────────────────┘
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"

mount -t tmpfs none /run
mkdir -p /run/netns
ip netns add host1
ip netns add host2

ip link add h1 type veth peer name h2
ip link set h1 netns host1
ip link set h2 netns host2

ip netns exec host1 ip addr add 10.0.0.1/24 dev h1
ip netns exec host2 ip addr add 10.0.0.2/24 dev h2
for ns in host1 host2; do
    ip netns exec $ns ip link set lo up
done
ip netns exec host1 ip link set h1 up
ip netns exec host2 ip link set h2 up

# host1 のカーネルは、自分の知らない接続へのパケットに RST を返してしまう。
# 自作TCPが張った接続を壊されないよう、RST を捨てる。
ip netns exec host1 iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP

# チェックサム計算をNICに任せる設定(オフロード)を切る。
# 切らないと、カーネルが送るパケットのチェックサム欄が未計算のまま届く。
ip netns exec host1 python3 "$DIR/txoff.py" h1
ip netns exec host2 python3 "$DIR/txoff.py" h2

echo ready
exec sleep infinity
