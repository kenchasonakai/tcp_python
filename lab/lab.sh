#!/usr/bin/env bash
# ラボ環境の操作。sudo 不要(ユーザー名前空間を使う)。
#
#   lab/lab.sh start                 仮想ネットワークを作る
#   lab/lab.sh stop                  片付ける
#   lab/lab.sh status
#   lab/lab.sh exec host1 <cmd...>   host1 の中でコマンドを実行する
#   lab/lab.sh shell host2           host2 の中でシェルを開く
#   lab/lab.sh loss 30%              router で両方向のパケットの30%を落とす(本書 3.7.5 の tc netem)
#   lab/lab.sh loss 30% host2        host1→host2 方向だけ落とす(本書と同じ片方向。host1 で host1→host1 方向)
#   lab/lab.sh loss off              ロスをやめる
#   lab/lab.sh drop-rst host2        host2 でも RST を捨てる(host2 でも自作TCPを動かすとき)
#
# host1  = 10.0.0.1   自作TCPを動かす側
# router = 10.0.0.254 / 10.0.1.254
# host2  = 10.0.1.1   カーネルのTCP。nc で相手役をする側
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}/tcplab"
PIDFILE="$STATE_DIR/pid"

alive() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

cmd_start() {
    if alive; then
        echo "already running (pid $(cat "$PIDFILE"))"
        return
    fi
    mkdir -p "$STATE_DIR"
    unshare --user --map-root-user --net --mount \
        bash "$DIR/setup_inside.sh" > "$STATE_DIR/log" 2>&1 &
    echo $! > "$PIDFILE"
    for _ in $(seq 50); do
        grep -q ready "$STATE_DIR/log" 2>/dev/null && break
        sleep 0.1
    done
    if ! grep -q ready "$STATE_DIR/log"; then
        echo "failed to start:"; cat "$STATE_DIR/log"; exit 1
    fi
    cat "$STATE_DIR/log" | grep -v ready
    echo "lab started (pid $(cat "$PIDFILE"))"
}

cmd_stop() {
    if alive; then
        kill "$(cat "$PIDFILE")"
        echo "lab stopped"
    else
        echo "not running"
    fi
    rm -f "$PIDFILE"
}

cmd_exec() {
    alive || { echo "lab is not running. run: lab/lab.sh start" >&2; exit 1; }
    local host="$1"; shift
    exec nsenter -t "$(cat "$PIDFILE")" --user --preserve-credentials --net --mount --wd="$PWD" \
        ip netns exec "$host" env PYTHONPATH="$ROOT" "$@"
}

case "${1:-}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) alive && echo "running (pid $(cat "$PIDFILE"))" || echo "not running" ;;
    exec)   shift; cmd_exec "$@" ;;
    shell)  shift; cmd_exec "$1" bash --norc -i ;;
    loss)
        # netem はそのインターフェースから「出ていく」パケット(egress)にだけ効く。
        #   router-veth2 の egress = host1 → host2 方向
        #   router-veth1 の egress = host2 → host1 方向
        # 本書 3.7.5 は router-veth2 だけ(片方向)。この教材は既定で両方に付けて、
        # カーネル側(host2)の再送も観察できるようにしている。
        shift
        [ -n "${1:-}" ] || { echo "usage: lab/lab.sh loss 30% [host1|host2] | off" >&2; exit 1; }
        case "${2:-}" in
            host1) devs="router-veth1" ;;   # host2 → host1 方向だけ
            host2) devs="router-veth2" ;;   # host1 → host2 方向だけ
            "")    devs="router-veth1 router-veth2" ;;
            *)     echo "usage: lab/lab.sh loss 30% [host1|host2] | off" >&2; exit 1 ;;
        esac
        if [ "$1" = off ]; then
            cmd_exec router sh -c "for d in router-veth1 router-veth2; do tc qdisc del dev \$d root 2>/dev/null; done; echo 'loss off'"
        else
            cmd_exec router sh -c "for d in router-veth1 router-veth2; do tc qdisc del dev \$d root 2>/dev/null; done; for d in $devs; do tc qdisc add dev \$d root netem loss $1 || exit 1; done; echo 'loss $1 ($devs)'"
        fi ;;
    drop-rst)
        shift; [ -n "${1:-}" ] || { echo "usage: lab/lab.sh drop-rst host2" >&2; exit 1; }
        cmd_exec "$1" iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP ;;
    *)      sed -n '2,15p' "$0"; exit 1 ;;
esac
