#!/usr/bin/env bash
# unshare で作ったユーザー名前空間の中で実行される。lab.sh から呼ばれる。
#
# 『Rustで始めるTCP自作入門』第2章と同じ構成(ルーターを1台挟む):
#
#   ┌── host1 (自作TCP) ──┐      ┌────── router ──────┐      ┌── host2 (カーネルTCP) ──┐
#   │ 10.0.0.1            ├─veth─┤ 10.0.0.254         ├─veth─┤ 10.0.1.1                │
#   │ host1-veth1         │      │ router-veth1/veth2 │      │ host2-veth1             │
#   └─────────────────────┘      │ 10.0.1.254         │      └─────────────────────────┘
#                                └────────────────────┘
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"

mount -t tmpfs none /run
mkdir -p /run/netns
ip netns add host1
ip netns add router
ip netns add host2

ip link add name host1-veth1 type veth peer name router-veth1
ip link add name router-veth2 type veth peer name host2-veth1
ip link set host1-veth1 netns host1
ip link set router-veth1 netns router
ip link set router-veth2 netns router
ip link set host2-veth1 netns host2

ip netns exec host1  ip addr add 10.0.0.1/24   dev host1-veth1
ip netns exec router ip addr add 10.0.0.254/24 dev router-veth1
ip netns exec router ip addr add 10.0.1.254/24 dev router-veth2
ip netns exec host2  ip addr add 10.0.1.1/24   dev host2-veth1

ip netns exec host1  ip link set host1-veth1 up
ip netns exec router ip link set router-veth1 up
ip netns exec router ip link set router-veth2 up
ip netns exec host2  ip link set host2-veth1 up
for ns in host1 router host2; do
    ip netns exec $ns ip link set lo up
done

# デフォルト経路はルーター経由。ルーターは転送を許可する
ip netns exec host1  ip route add 0.0.0.0/0 via 10.0.0.254
ip netns exec host2  ip route add 0.0.0.0/0 via 10.0.1.254
ip netns exec router sysctl -qw net.ipv4.ip_forward=1

# host1 のカーネルは、自分の知らない接続へのパケットに RST を返してしまう(本書 2.2)。
# 自作TCPが張った接続を壊されないよう、RST を捨てる。
# 本書は host2 でも捨てているが、この教材では host2 はカーネルのTCP(nc)を相手役に
# するので捨てない。host2 でも自作TCPを動かすときは lab.sh の drop-rst を使う。
ip netns exec host1 iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP

# チェックサム計算をNICに任せる設定(オフロード)を切る(本書 2.3 の ethtool -K tx off と同じ)。
# 切らないと、カーネルが送るパケットのチェックサム欄が未計算のまま届く。
ip netns exec host1 python3 "$DIR/txoff.py" host1-veth1
ip netns exec host2 python3 "$DIR/txoff.py" host2-veth1

echo ready
exec sleep infinity
