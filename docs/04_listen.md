# Step 4: 受動オープン(listen / accept)

## ゴール

- 待ち受け側(サーバー)としてハンドシェイクを完成させる
- Step 3 のコードを `tcp/conn.py` に移し、ここから育てていく

## 状態遷移(受動オープン)

```
   CLOSED
     │  listen()
     ▼
   LISTEN            相手はまだ決まっていない(IP・ポートが未定)
     │  SYN を受け取る → SYN/ACK を送る
     ▼
   SYN_RECEIVED      3発目を待っている(Step 2 で見た "SYN-RECV")
     │  ACK を受け取る
     ▼
   ESTABLISHED
```

Step 3 との違いは、**SYNを受け取ってはじめて相手が決まる**ことです。LISTEN状態では4つ組のうち相手側の2つが空いていて、届いたSYNの送信元がそのまま相手になります。

## ここからの設計:`tcp/conn.py`

Step 3 のクラスをベースに、以降のステップで機能を足していきます。コード中の `[Step N]` コメントが、どのステップで足したかを示しています。今の時点では `[Step 4]` までを読めば十分で、`[Step 5]` 以降は次のステップで読みます。

中心にあるのは、たった1つのループです。

```python
def _step(self):
    """イベントループの1周。"""
    got = self._recv()           # セグメントを1つ受け取る(無ければ None)
    if got is not None:
        self._handle(*got)       # 今の状態に応じて処理する
    self._check_retransmit()     # [Step 6] 再送タイマー

def _run_until(self, cond, timeout=None):
    while not cond():
        self._step()
```

`connect()`、`accept()`、`recv()`、`send()`、`close()` はすべて「目的の状態になるまで `_step()` を回す」だけです。スレッドもロックもありません。

`_handle()` は状態で分岐します。

```python
def _handle(self, src_ip, seg):
    if self.state == "LISTEN":     self._handle_listen(src_ip, seg)
    elif self.state == "SYN_SENT": self._handle_syn_sent(seg)
    else:                          self._handle_other(seg)   # SYN_RECEIVED 以降は共通
```

RFC 9293 も同じ構造で、LISTEN と SYN_SENT だけが特別扱いで、残りの状態はひとまとめに書かれています。

## コードを読む

### `_handle_listen()`

```python
if seg.rst or seg.ack_flag:
    return                                   # ACK付きが来たら本来はRSTを返す(省略)
if not seg.syn:
    return
self.remote_ip, self.remote_port = src_ip, seg.src_port   # ここで相手が決まる
self.irs = seg.seq
self.rcv_nxt = seg.seq + 1                   # 相手の SYN を1つ消費
self.iss = random.randint(0, 2**32 - 1)
self.snd_una = self.snd_nxt = self.iss
self.snd_wnd = seg.window
self._send(SYN | ACK, options=mss_option(MSS))   # seq = iss, ack = irs + 1
self._set_state("SYN_RECEIVED")
```

`_send()` は Step 3 と少し変わっていて、`snd_nxt += seg.seg_len` を内部でやります(SYN/FIN/データを送ると自動で進む)。

### `_handle_other()` の SYN_RECEIVED 部分

```python
if self.snd_una < seg.ack <= self.snd_nxt:      # Step 3 と同じ受け入れ条件
    self._ack_received(seg.ack, seg.window)      # snd_una を進める
    if self.state == "SYN_RECEIVED":
        self._set_state("ESTABLISHED")
```

3発目のACKで自分のSYN/ACKが確認されたら、ESTABLISHEDです。

## 動かす

今回は自作側が先です。端末B:

```bash
lab/lab.sh exec host1 python3 steps/step04_listen.py 9000
```

端末A(カーネル側から接続する):

```bash
lab/lab.sh exec host2 nc 10.0.0.1 9000
```

### 期待される出力(端末B)

```
[  0.000]     state: CLOSED -> LISTEN
port 9000 で待っています。 lab/lab.sh exec host2 nc 10.0.0.1 9000 で接続してください
[  0.459] <-- 36928 > 9000: Flags [S], seq 431432907, ack 0, win 64240, length 0
[  0.459] --> 9000 > 36928: Flags [S.], seq 591235350, ack 431432908, win 65535, length 0
[  0.459]     state: LISTEN -> SYN_RECEIVED
[  0.459] <-- 36928 > 9000: Flags [.], seq 431432908, ack 591235351, win 64240, length 0
[  0.459]     state: SYN_RECEIVED -> ESTABLISHED

接続完了: 10.0.0.2:36928 から
```

端末Cで `lab/lab.sh exec host2 ss -tan` を見ると、host2側に `ESTAB` があります。今度はカーネルが「クライアント」で、自作TCPが「サーバー」です。

## 壊してみる

**SYN/ACKを落とす**
`steps/step04_listen.py` の `Connection("10.0.0.1", port)` を `Connection("10.0.0.1", port, loss=1.0)` にすると、送信を全部落とします(この仕組みは Step 6 で使うものです)。カーネル側のncはSYNを何度再送するか、間隔はどうか、何秒であきらめるか。`ss -tan` でクライアント側の `SYN-SENT` も見てください。

**2つ目の接続**
ESTABLISHED のあと、もう1つ別の端末から `nc 10.0.0.1 9000` してみてください。今の実装は1接続しか扱えないので、2つ目のSYNは `_handle_other` に入り、ACKフラグがないので無視されます。本物のTCPは、LISTENソケットと接続済みソケットを別々に持ち、SYNが来るたびに新しい接続を作ります(Step 8 の課題)。

## 理解チェック

1. LISTEN状態のとき、4つ組のうち決まっているのはどれか
2. SYN/ACKの seq と ack にはそれぞれ何が入るか
3. 自作TCPがSYN/ACKを送ったあと、3発目が来なかったらどうなるべきか(Step 6 で実装する)
4. なぜ SYN_RECEIVED 以降の状態を1つの関数でまとめて扱えるのか

## 参考

- RFC 9293 3.10.7.2 "LISTEN STATE"
- RFC 9293 3.10.7.4 "Other States"
