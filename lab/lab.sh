#!/usr/bin/env bash
# ラボ環境の操作。sudo 不要(ユーザー名前空間を使う)。
#
#   lab/lab.sh start                 仮想ネットワークを作る
#   lab/lab.sh stop                  片付ける
#   lab/lab.sh status
#   lab/lab.sh exec host1 <cmd...>   host1 の中でコマンドを実行する
#   lab/lab.sh shell host2           host2 の中でシェルを開く
#
# host1 = 10.0.0.1 (自作TCPを動かす側)
# host2 = 10.0.0.2 (カーネルのTCP。nc で相手役をする側)
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
    *)      sed -n '2,12p' "$0"; exit 1 ;;
esac
