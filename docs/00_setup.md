# Step 0: ラボ環境

> **本書の対応箇所**:『Rustで始めるTCP自作入門』第2章「準備編」(2.1 検証環境構築、2.2 RSTフラグの扱い、2.3 チェックサムオフロードの無効化)

## ゴール

- 自作TCPとカーネルのTCPが安全に通信できる仮想ネットワークを作る
- 自作スニファで、ネットワークを流れるTCPパケットを見られるようにする

## なぜ仮想ネットワークが必要か(本書 2.1〜2.3)

自作TCPはrawソケットで直接パケットを送ります。すると、**同じマシンのカーネルも同じパケットを受け取ります**。カーネルは「そんな接続は知らない」と判断して、相手にRST(接続リセット)を返してしまい、せっかくの接続が壊されます(本書 2.2)。

対策はRSTをiptablesで捨てることですが、普段使っているネットワークでそれをやると、ほかの通信に影響します。そこで、専用の仮想マシンのようなもの(network namespace)を作り、その中だけで実験します。本書と同じく、ホスト2台の間にルーターを1台挟みます。

```
 ┌── host1 (自作TCP) ──┐        ┌────── router ──────┐        ┌── host2 (カーネルTCP) ──┐
 │ 10.0.0.1            ├─ veth ─┤ 10.0.0.254         ├─ veth ─┤ 10.0.1.1                │
 │ host1-veth1         │        │ 10.0.1.254         │        │ host2-veth1             │
 │ RSTはiptablesで捨てる│        │ ここでロスを入れる   │        │ nc で相手役をする        │
 └─────────────────────┘        └────────────────────┘        └─────────────────────────┘
```

- `veth` は仮想のLANケーブルです。両端がそれぞれの名前空間に刺さっています。
- **host1** で自作TCPを動かし、**host2** のカーネルTCP(`nc`)を相手にします。host2 は普通のLinuxなので、`ss` で接続の状態を見たり、`nc` で待ち受けたりできます。
- **router** は2つのネットワーク(10.0.0.0/24 と 10.0.1.0/24)の間でパケットを転送します。Step 6 では、ここでわざとパケットを落とします(本書 3.7.5)。
- もう1つ、チェックサムの計算をNICに任せる機能(オフロード)を切っています(本書 2.3)。切らないと、カーネルが送るパケットのチェックサム欄が未計算のまま届いて、自作側の検証が失敗します。

### 本書との違い

- 本書は `sudo ip netns exec host1 ...` で操作しますが、この教材は**ユーザー名前空間**(`unshare --user`)の中に同じ構成を作るので、sudoがいりません。`lab/lab.sh exec host1 ...` が `sudo ip netns exec host1 ...` に相当します。
- 本書は host1・host2 の両方で自作TCPを動かすので両方でRSTを捨てていますが、この教材は host2 でカーネルのTCPを相手役にするので、host1 だけで捨てています。host2 でも自作TCPを動かしたいときは `lab/lab.sh drop-rst host2` を実行してください(そのあとは、Step 2 の「待ち受けなし → RST」の実験はできなくなります)。
- `ethtool` の代わりに、同じ設定を `ioctl` で行う `lab/txoff.py` を使っています。

## 使い方

```bash
lab/lab.sh start                          # 起動(一度起動すれば止めるまで使える)
lab/lab.sh exec host1 <コマンド>           # host1 の中でコマンドを実行
lab/lab.sh exec host2 <コマンド>           # host2 の中で
lab/lab.sh shell host2                    # host2 の中でシェルを開く(exit で戻る)
lab/lab.sh loss 30%                       # router で両方向の30%のパケットを落とす(Step 6)
lab/lab.sh loss 30% host2                 # host1→host2 方向だけ落とす(本書と同じ片方向)
lab/lab.sh loss off                       # ロスをやめる
lab/lab.sh status
lab/lab.sh stop                           # 片付け
```

`exec` はカレントディレクトリを引き継ぐので、リポジトリのルートで実行してください。

各ステップでは、端末を2〜3枚開いて使います。

| 端末 | 役割 | 例 |
|---|---|---|
| A | 相手役(host2) | `lab/lab.sh exec host2 nc -l -p 9000` |
| B | 自作TCP(host1) | `lab/lab.sh exec host1 python3 steps/step03_connect.py 9000` |
| C | 観察(どちらでも) | `lab/lab.sh exec host2 ss -tan`、スニファ |

## 自作スニファ

このラボの中では本物の `tcpdump` が動かない(権限を落とす処理で失敗する)ので、Step 1で作るヘッダ解析を流用した小さなスニファを使います。出力形式はtcpdumpに合わせてあるので、本書の第1章やあとで本物に移っても同じように読めます。

```bash
lab/lab.sh exec host1 python3 -m tcp.sniff host1-veth1              # 画面に表示
lab/lab.sh exec host1 python3 -m tcp.sniff host1-veth1 -w out.pcap  # ファイルにも保存
lab/lab.sh exec host1 python3 -m tcp.sniff host1-veth1 -S           # seq/ack を絶対値で表示
```

router 側(`router-veth1` / `router-veth2`)で見ると、ロスを入れたときに「router に届いたが先に進まなかった」パケットを区別できます。

保存した `.pcap` はWiresharkで開けます。WSLなら `-w /mnt/c/Users/<名前>/Desktop/out.pcap` のようにWindows側に書けば、そのままダブルクリックで開けます。

## 動作確認

起動します。

```bash
$ lab/lab.sh start
host1-veth1: tx-checksumming = off
host2-veth1: tx-checksumming = off
lab started (pid 12345)
```

ルーター越しに疎通していることを確認します。

```bash
$ lab/lab.sh exec host1 ping -c1 10.0.1.1
1 packets transmitted, 1 received, 0% packet loss
```

端末Aでスニファ、端末Bと端末Cでncどうしの通信をしてみます(本書 1.2.3「パケットの流れ」に相当)。

```bash
# 端末A
lab/lab.sh exec host1 python3 -m tcp.sniff host1-veth1
# 端末B
lab/lab.sh exec host2 nc -l -p 9000
# 端末C
lab/lab.sh exec host1 nc 10.0.1.1 9000     # 何か打って Enter、Ctrl+C で終了
```

端末Aにこのような表示が出れば成功です(カーネルどうしの通信です)。

```
   0.955  10.0.0.1.36434 > 10.0.1.1.9000: Flags [S], seq 0, win 64240, length 0
   0.955  10.0.1.1.9000 > 10.0.0.1.36434: Flags [S.], seq 0, ack 1, win 65160, length 0
   0.955  10.0.0.1.36434 > 10.0.1.1.9000: Flags [.], seq 1, ack 1, win 63, length 0
   0.955  10.0.0.1.36434 > 10.0.1.1.9000: Flags [P.], seq 1:6, ack 1, win 63, length 5  "ping\n"
   0.955  10.0.1.1.9000 > 10.0.0.1.36434: Flags [.], seq 1, ack 6, win 64, length 0
   0.955  10.0.0.1.36434 > 10.0.1.1.9000: Flags [F.], seq 6, ack 1, win 63, length 0
   ...
```

これが、これから自分で作る動きの「正解」です。この段階で読めなくて構いません。Step 7が終わったころには、1行ずつ説明できるようになっています。

## トラブルシューティング

**`unshare: unshare failed: Operation not permitted`**
ユーザー名前空間が無効になっています。Ubuntu 24.04以降なら `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`、Debian系なら `sudo sysctl kernel.unprivileged_userns_clone=1` で有効にできます(この設定だけはsudoが必要です)。

**`lab is not running`**
`lab/lab.sh start` を実行してください。WSLを再起動すると環境は消えます。

**ポートが使用中、接続が残っている**
実験で閉じ忘れた接続がhost2に残ることがあります。`lab/lab.sh stop && lab/lab.sh start` で作り直すのが確実です。

**自作側の出力に `checksum が壊れているので捨てた` が出る**
オフロードが切れていません。`lab/lab.sh exec host2 python3 lab/txoff.py host2-veth1` を実行してください。

**ロスを入れたままにしてしまった**
`lab/lab.sh loss off` で戻ります。
