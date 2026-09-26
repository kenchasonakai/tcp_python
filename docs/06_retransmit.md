# Step 6: 再送

> **本書の対応箇所**:『Rustで始めるTCP自作入門』3.7.4「確認応答と再送」、3.7.5「動作確認」(tc netem によるロス)

## ゴール

- ACKが来ないセグメントを、タイムアウト後に再送する
- わざとパケットを落としても、最終的に全部届くことを確認する
- 「ACKが落ちた」ときにも同じ仕組みで回復することを見る

## 考え方

IPはパケットをなくします。TCPが信頼性を持てるのは、**送ったものをACKされるまで手元に取っておき、一定時間たっても確認されなければもう一度送る**からです。これだけです。

必要なものは3つ。

1. **再送キュー**:送信済み・未ACKのセグメントのコピー
2. **タイマー**:最後に送ってから何秒たったか
3. **RTO**(Retransmission TimeOut):何秒待ったら再送するか

RTOの決め方が実は難しく、本物のTCPは往復時間(RTT)を測って動的に計算します(RFC 6298)。ここでは1秒固定から始めて、再送のたびに2倍にします(指数バックオフ)。Step 2 でカーネルがSYN/ACKを 1秒、2秒、4秒 の間隔で再送していたのと同じです。

## コードを読む(`[Step 6]` の部分)

### 送るときにキューへ入れる:`_send()`

```python
if seg.seg_len:
    self.rtx_queue.append(RtxEntry(seg.seq, seg.seq + seg.seg_len, seg_bytes, time.time()))
```

SYN、FIN、データを持つセグメント(= seg_len > 0)だけをキューに入れます。純粋なACKは入れません。ACKが落ちても、相手がデータを再送してくればまたACKを返せるからです。

### ACKが来たらキューから消す:`_ack_received()`

```python
self.rtx_queue = [e for e in self.rtx_queue if e.end > ack]
```

ackの値より手前で終わるエントリは「届いた」ので消します。

### タイムアウトを見る:`_check_retransmit()`

```python
head = self.rtx_queue[0]                      # 一番古い未ACKセグメント
if time.time() - head.sent_at < self.rto:
    return
if head.count > MAX_RETRANSMITS:
    raise ConnectionError(...)
self._transmit(head.data, ..., retransmit=True)
head.sent_at = time.time()
head.count += 1
self.rto = min(self.rto * 2, 60.0)            # 指数バックオフ
```

`_step()` が回るたびに呼ばれます。キューの先頭(最古)だけを見れば十分です。先頭がタイムアウトしていなければ、後ろはもっと新しいので、当然していません。

### わざと落とす:`loss` / `loss_in`

```python
if self.loss and random.random() < self.loss:
    self._log(f"-x> {seg}  (わざと落とした)")
    return
```

送信直前(`_transmit`)と受信直後(`_recv`)に、指定した確率で捨てる処理を入れてあります。本書(3.7.5)はルーター上で `tc qdisc add dev router-veth2 root netem loss 30%` を実行して、ネットワークの側で落とします。この教材でも `lab/lab.sh loss 30%` で同じことができます。自分のコードで落とすほうが「どのパケットが落ちたか」がログで分かるので、まずは `--loss` で仕組みを理解し、そのあと netem で「本当にネットワークで落ちる」場合を試すのがおすすめです。

## 動かす

端末A(受け取ったデータをファイルに保存):

```bash
lab/lab.sh exec host2 nc -l -p 9000 > /tmp/received.txt
```

端末B(送信の30%を落とす):

```bash
lab/lab.sh exec host1 python3 steps/step06_retransmit.py 9000 --loss 0.3 --lines 3 --seed 1
```

`--seed 1` を付けると毎回同じ落ち方をします。外すと毎回変わります。

### 期待される出力

```
[  0.000] -x> 44402 > 9000: Flags [S], seq 271041745, ack 0, win 65535, length 0  (わざと落とした)
[  0.000]     state: CLOSED -> SYN_SENT
[  1.003]     RTO=1.0s 経過。seq 271041745 を再送(1回目)
[  1.004] -R> 44402 > 9000: Flags [S], seq 271041745, ack 0, win 65535, length 0  (再送)
[  1.004] <-- 9000 > 44402: Flags [S.], seq 1829332, ack 271041746, win 64240, length 0
[  1.004] --> 44402 > 9000: Flags [.], seq 271041746, ack 1829333, win 65535, length 0
[  1.004]     state: SYN_SENT -> ESTABLISHED
[  1.004] --> 44402 > 9000: Flags [P.], seq 271041746, ack 1829333, win 65535, length 7
[  1.004] <-- 9000 > 44402: Flags [.], seq 1829333, ack 271041753, win 64233, length 0
[  1.004] -x> 44402 > 9000: Flags [P.], seq 271041753, ack 1829333, win 65535, length 7  (わざと落とした)
[  2.007]     RTO=1.0s 経過。seq 271041753 を再送(1回目)
[  2.007] -R> 44402 > 9000: Flags [P.], seq 271041753, ack 1829333, win 65535, length 7  (再送)
[  2.007] <-- 9000 > 44402: Flags [.], seq 1829333, ack 271041760, win 64226, length 0
[  2.007] --> 44402 > 9000: Flags [P.], seq 271041760, ack 1829333, win 65535, length 7
[  2.007] <-- 9000 > 44402: Flags [.], seq 1829333, ack 271041767, win 64219, length 0

3 行すべて ACK された。再送キューの残り: 0
```

- **最初のSYNが落ちた**。1秒後に再送され、ハンドシェイクが完了した。SYNもデータと同じ仕組みで再送される
- **2行目のデータが落ちた**。1秒後に再送され、ackが返った
- `/tmp/received.txt` を見ると、`line 1`〜`line 3` が欠けも重複もなく入っている

```bash
lab/lab.sh exec host2 cat /tmp/received.txt
```

### ネットワークで落とす(本書の方法)

```bash
lab/lab.sh loss 30%                                            # ルーターで30%落とす
lab/lab.sh exec host1 python3 steps/step06_retransmit.py 9000 --loss 0 --lines 10
lab/lab.sh loss off                                            # 終わったら戻す
```

今度は自作側のログに `-x>` は出ません。送ったつもりのパケットがルーターで消え、ACKが来ないので再送する、という流れになります。両方向で落ちるので、SYN/ACKやACKが落ちてカーネル側が再送する様子も混ざります。どのパケットが落ちたかを推理しながらログを読んでください。

### ACKが落ちる場合

```bash
lab/lab.sh exec host1 python3 steps/step06_retransmit.py 9000 --loss 0 --loss-in 0.5 --lines 2 --seed 3
```

```
[  0.000] --> 47797 > 9000: Flags [P.], seq 560161642, ack 3438512549, win 65535, length 7
[  0.000] <x- 9000 > 47797: Flags [.], seq 3438512549, ack 560161649, win 64233, length 0  (わざと落とした)
[  1.004]     RTO=1.0s 経過。seq 560161642 を再送(1回目)
[  1.004] -R> 47797 > 9000: Flags [P.], seq 560161642, ack 3438512549, win 65535, length 7  (再送)
[  1.004] <-- 9000 > 47797: Flags [.], seq 3438512549, ack 560161649, win 64233, length 0
```

データは届いていたのに、その**ACKが落ちた**ので、送信側からは「届かなかった」と区別がつきません。同じデータを再送し、相手(カーネル)は重複を検出して捨てつつ、もう一度ACKを返しています。送信側は「データが落ちた」と「ACKが落ちた」を区別する必要がない、というのがこの設計の美しいところです。

## 壊してみる

**あきらめさせる**
`--loss 1.0` にすると全部落ちます。RTOが 1, 2, 4, 8, 16秒と伸び、5回で `ConnectionError` になります。カーネルはSYNなら6回(`net.ipv4.tcp_syn_retries`)、データなら15回(`tcp_retries2`、約15分)粘ります。

**RTOを短くしすぎる**
`RTO_INITIAL = 0.001` にして、ロスなしで大きなデータを送ってみてください。ACKが返る前にタイムアウトして、必要のない再送(spurious retransmission)が起きます。RTOが短すぎると無駄な再送でネットワークを混ませ、長すぎると回復が遅れる。だから本物はRTTから計算します。

**再送でバックオフしない**
`self.rto = min(self.rto * 2, 60.0)` を消して `--loss 1.0` にすると、1秒ごとに再送し続けます。相手が落ちているのか混雑しているのか分からないとき、間隔を空けていくことに意味があります。

**受信ロスをスニファと組み合わせる**
`--loss-in` は「自分のコードで捨てる」だけなので、スニファ(`python3 -m tcp.sniff host1-veth1`)にはそのパケットが映ります。自作側のログには `<x-` と出て、スニファには普通に見える。両方を見比べると「ネットワークには届いたが、受信側が捨てた」という状況を再現していることが分かります。

## 理解チェック

1. 再送キューに入れるセグメントと入れないセグメントの違いは何か
2. `_check_retransmit()` がキューの先頭だけを見れば十分なのはなぜか
3. ACKが落ちたとき、送信側と受信側はそれぞれ何をするか
4. RTOを1秒固定にしたときの問題は何か。短すぎるとき、長すぎるとき

## 本物との違い(Step 8 の課題)

- **RTTの計測とRTOの計算**(RFC 6298):SRTTとRTTVARを更新して RTO = SRTT + 4·RTTVAR
- **高速再送**(RFC 5681):同じackが3回続いたら、タイムアウトを待たずに再送
- **SACK**(RFC 2018):「ここからここは届いた」を細かく伝え、必要な分だけ再送
- **順序入れ替わりの保持**:今の実装は間の抜けたセグメントを捨てるので、1つ落ちると後続を全部再送させてしまう

## 参考

- RFC 9293 3.8.1 "Retransmission Timeout"
- RFC 6298 (Computing TCP's Retransmission Timer)
