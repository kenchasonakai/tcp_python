# Step 7: 切断(close)

## ゴール

- FINを送って接続を閉じる(能動クローズ)
- 相手のFINを受けてから閉じる(受動クローズ)
- TIME_WAIT がなぜ必要かを、動きで理解する

## 4ウェイの切断

TCPの接続は双方向で、**片方向ずつ独立に閉じます**。「もう送らない」がFINで、それを受け取った側はまだ送ることができます(half-close)。両方がFINを送って、両方がACKされたら終わりです。

```
   能動クローズ側(先に閉じる)                受動クローズ側
   ESTABLISHED                               ESTABLISHED
     │  close(): FIN を送る                     │
     ▼                                         │  FIN を受け取る → ACK を返す
   FIN_WAIT_1                                  ▼
     │  自分の FIN への ACK を受け取る          CLOSE_WAIT   ← アプリが close() するまでここに留まる
     ▼                                         │  close(): FIN を送る
   FIN_WAIT_2                                  ▼
     │  相手の FIN を受け取る → ACK を返す     LAST_ACK
     ▼                                         │  ACK を受け取る
   TIME_WAIT                                   ▼
     │  2*MSL 待つ                            CLOSED
     ▼
   CLOSED
```

ACKとFINが1つのセグメントにまとめられて届くこともあり(FIN_WAIT_1 から直接 TIME_WAIT に見える)、両方が同時にFINを送る「同時クローズ」(CLOSING状態)もあります。

## TIME_WAIT はなぜあるか

能動クローズ側は、最後のACKを送ったあと、すぐには消えずにしばらく待ちます。理由は2つ。

1. **最後のACKが落ちたときのため**。相手はLAST_ACKでACKを待っていて、来なければFINを再送します。こちらが消えていると、そのFINに答えられず、相手はいつまでも閉じられません。
2. **古いセグメントが次の接続に混ざらないため**。同じ4つ組で新しい接続をすぐ張ると、ネットワークに残っていた前の接続のセグメントが届いてしまうかもしれません。待つことで、それらが消えるのを保証します。

待ち時間は 2·MSL(Maximum Segment Lifetime:セグメントがネットワーク上で生き延びる最大時間)で、Linuxは60秒です。この教材では実験のしやすさのために2秒にしています。

サーバーが大量の短い接続を能動クローズすると、TIME_WAIT が大量に溜まってポートが枯渇する、という実運用でよく出る問題は、ここから来ています。

## コードを読む(`[Step 7]` の部分)

### `close()`

```python
if self.state == "ESTABLISHED":
    self._send(FIN | ACK); self._set_state("FIN_WAIT_1")    # 能動クローズ
elif self.state == "CLOSE_WAIT":
    self._send(FIN | ACK); self._set_state("LAST_ACK")      # 受動クローズ
...
self._run_until(lambda: self.state in ("TIME_WAIT", "CLOSED"))
if self.state == "TIME_WAIT":
    self._run_until(lambda: False, timeout=TIME_WAIT_SECONDS)   # 待つあいだもセグメントは処理する
    self._set_state("CLOSED")
```

FINは `seg_len = 1` なので、`_send()` が再送キューに入れ、`snd_nxt` を1つ進めます。データと同じです。

### `_handle_other()` の遷移

ACKを受け取ったとき:

```python
all_acked = self.snd_una == self.snd_nxt        # 自分の FIN まで確認されたか
if self.state == "FIN_WAIT_1" and all_acked:   self._set_state("FIN_WAIT_2")
elif self.state == "CLOSING" and all_acked:    self._set_state("TIME_WAIT")
elif self.state == "LAST_ACK" and all_acked:   self._set_state("CLOSED")
```

FINを受け取ったとき:

```python
if seg.fin:
    self.rcv_nxt += 1                            # FIN も1つ消費する
    self.fin_received = True
    if self.state in ("ESTABLISHED", "SYN_RECEIVED"): self._set_state("CLOSE_WAIT")
    elif self.state == "FIN_WAIT_1":                  self._set_state("CLOSING")
    elif self.state == "FIN_WAIT_2":                  self._set_state("TIME_WAIT")
self._send(ACK)
```

ACKの処理が先、FINの処理が後、という順番が大事です。相手のセグメントに「こちらのFINへのACK」と「相手のFIN」が両方入っていたとき、FIN_WAIT_1 → FIN_WAIT_2 → TIME_WAIT と正しく進みます。

## 動かす

### 能動クローズ(自作側から閉じる)

端末A:

```bash
lab/lab.sh exec host2 nc -l -p 9000
```

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step07_close.py client 9000
```

```
[  0.000] --> 47376 > 9000: Flags [P.], seq 137274219, ack 3286249296, win 65535, length 4
[  0.000] <-- 9000 > 47376: Flags [.], seq 3286249296, ack 137274223, win 64236, length 0

自分から閉じます(能動クローズ)
[  0.000] --> 47376 > 9000: Flags [F.], seq 137274223, ack 3286249296, win 65535, length 0
[  0.000]     state: ESTABLISHED -> FIN_WAIT_1
[  0.001] <-- 9000 > 47376: Flags [F.], seq 3286249296, ack 137274224, win 64235, length 0
[  0.001]     state: FIN_WAIT_1 -> FIN_WAIT_2
[  0.001]     state: FIN_WAIT_2 -> TIME_WAIT
[  0.001] --> 47376 > 9000: Flags [.], seq 137274224, ack 3286249297, win 65535, length 0
[  0.001]     TIME_WAIT: 2.0秒待つ(遅れて届く相手のFIN再送にACKを返すため)
[  2.007]     state: TIME_WAIT -> CLOSED
```

- 自分のFINは `seq 137274223`。相手の `ack 137274224` で確認された(FINが1消費する)
- 相手のFINとそのACKが**1つのセグメント**で届いたので、FIN_WAIT_1 → FIN_WAIT_2 → TIME_WAIT が一瞬で進んだ。ncは相手のFINを受け取ると即座に自分も閉じるので、こうなる
- 終了後、host2の `ss -tan` には何も残らない。TIME_WAIT を持つのは能動クローズ側(自作側)だから

### 受動クローズ(相手が先に閉じる)

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step07_close.py server 9000
```

端末A(何か打ってから Ctrl+D):

```bash
lab/lab.sh exec host2 nc 10.0.0.1 9000
```

```
[  0.470] <-- 45526 > 9000: Flags [F.], seq 4226867707, ack 1974635390, win 64240, length 0
[  0.470]     state: ESTABLISHED -> CLOSE_WAIT
[  0.470] --> 9000 > 45526: Flags [.], seq 1974635393, ack 4226867708, win 65535, length 0

相手が先に閉じた(受動クローズ)。こちらも閉じます
[  0.470] --> 9000 > 45526: Flags [F.], seq 1974635393, ack 4226867708, win 65535, length 0
[  0.470]     state: CLOSE_WAIT -> LAST_ACK
[  0.470] <-- 45526 > 9000: Flags [.], seq 4226867708, ack 1974635394, win 64236, length 0
[  0.470]     state: LAST_ACK -> CLOSED
```

今度は TIME_WAIT を通りません。かわりに host2 側に残ります:

```bash
$ lab/lab.sh exec host2 ss -tan
TIME-WAIT  0  0  10.0.0.2:45526  10.0.0.1:9000
```

カーネルの TIME_WAIT は60秒です。60秒後にもう一度見ると消えています。

## 壊してみる

**最後のACKを落とす**
`tcp/conn.py` の `_set_state()` の末尾に、TIME_WAIT に入った瞬間から送信を全部落とす1行を足します。

```python
def _set_state(self, new):
    self._log(f"    state: {self.state} -> {new}")
    self.state = new
    if new == "TIME_WAIT":
        self.loss = 1.0      # 実験用: 最後の ACK とその後の再ACKを全部落とす
```

`steps/step07_close.py client 9000` を実行すると:

```
[  0.000]     state: FIN_WAIT_2 -> TIME_WAIT
[  0.000] -x> 48402 > 9000: Flags [.], seq 1572339886, ack 1552906984, win 65535, length 0  (わざと落とした)
[  0.000]     TIME_WAIT: 2.0秒待つ(遅れて届く相手のFIN再送にACKを返すため)
[  0.201] <-- 9000 > 48402: Flags [F.], seq 1552906983, ack 1572339886, win 64235, length 0
[  0.201]     seq 1552906983 != rcv_nxt 1552906984。捨てて ACK を返す
[  0.202] -x> 48402 > 9000: Flags [.], ...  (わざと落とした)
[  0.610] <-- 9000 > 48402: Flags [F.], seq 1552906983, ...
[  1.442] <-- 9000 > 48402: Flags [F.], seq 1552906983, ...
[  2.043]     state: TIME_WAIT -> CLOSED
```

最後のACKが届かなかったので、カーネルは LAST_ACK のまま **FINを 0.2秒、0.4秒、0.8秒 と間隔を倍にしながら再送**しています(Linuxの最小RTOは200ms)。こちらはTIME_WAITにいるあいだ、その再送FINを「すでに受け取ったもの」として認識し、ACKを返そうとしています(実験なので落ちますが)。もし TIME_WAIT がなくてすぐ消えていたら、そもそもこの再送FINを受け取る者がいません。

自作側が CLOSED になった直後に host2 を見ると:

```
$ lab/lab.sh exec host2 ss -tan
LAST-ACK  0  1  10.0.0.2:9000  10.0.0.1:48402
```

相手が取り残されています。実験が終わったら追加した1行は消してください。

**TIME_WAIT を0にする**
上の実験で `loss = 1.0` の行を消し、かわりに `TIME_WAIT_SECONDS = 0` にしてみてください。正常時はFINとACKが1往復で終わるので違いは出ませんが、最後のACKが実際に落ちたときには相手のFIN再送に誰も答えられません。「TIME_WAIT は落ちたときのための保険」だと分かります。

**CLOSE_WAIT に留まる**
`server()` の `conn.close()` を消すと、相手のFINにACKは返すもののこちらのFINを送らないので、host2 は `FIN-WAIT-2` のまま残ります(Step 5 で見たものと同じ)。「CLOSE_WAIT が大量に溜まる」は、アプリが close を忘れている典型的な症状です。

**同時クローズ**
両方が同時に `close()` すると CLOSING 状態を通ります。nc相手には再現しにくいので、`_handle_other()` のコードで「FIN_WAIT_1 で相手のFINを受け取ったら CLOSING、その後 自分のFINへのACKが来たら TIME_WAIT」と遷移する道筋を追ってみてください。

## 理解チェック

1. FINを送ったあと、相手からデータを受け取ることはできるか
2. TIME_WAIT を持つのは、能動クローズ側と受動クローズ側のどちらか。なぜそちらか
3. `ss` で `CLOSE_WAIT` が大量に残っているサーバーがあるとき、疑うべきは何か
4. 相手のセグメントに「こちらのFINへのACK」と「相手のFIN」が同時に入っていたとき、状態はどう遷移するか

## ここまでで作ったもの

Step 7 が終わった時点で、自作TCPは次のことができます。

- ハンドシェイク(能動・受動)、データ送受信、再送、切断(能動・受動)
- カーネルの本物のTCPと、どちらの立場でも正しく会話できる

Step 0 のスニファで見た「正解」のログを、今なら1行ずつ説明できるはずです。もう一度眺めてみてください。

## 参考

- RFC 9293 3.6 "Closing a Connection"、3.10.7.4 "Other States" の FIN 処理
