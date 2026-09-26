# Step 3: 3ウェイハンドシェイク

> **本書の対応箇所**:『Rustで始めるTCP自作入門』3.5「スリーウェイハンドシェイク: アクティブオープン」(3.5.5 connect API、3.5.7 受信ハンドラ)

## ゴール

- 3発目のACKを返して、接続を ESTABLISHED まで持っていく
- 接続の状態を表す変数(snd_una, snd_nxt, rcv_nxt)の意味を理解する
- 状態遷移(CLOSED → SYN_SENT → ESTABLISHED)をコードに落とす

## 接続の状態を表す変数

TCPの接続は、送信側と受信側それぞれについて「バイト列のどこまで進んだか」を数字で持っています。RFC 9293の名前をそのまま使います。

```
送信側(自分が送るバイト列)

   ...送信済み・ACK済み | 送信済み・未ACK | まだ送っていない...
                        ^                 ^
                     snd_una           snd_nxt

   snd_una  Send UNAcknowledged  相手からまだ確認されていない最古のバイト
   snd_nxt  Send NeXT            次に送るバイトの番号
   iss      Initial Send Seq     自分が最初に選んだ番号(SYNのseq)
   snd_wnd  Send WiNDow          相手が「あとこれだけ受け取れる」と言った量

受信側(相手が送ってくるバイト列)

   ...受信済み | まだ来ていない...
              ^
           rcv_nxt

   rcv_nxt  Receive NeXT         次に受け取るはずのバイト = 相手に返す ack の値
   irs      Initial Receive Seq  相手が最初に選んだ番号(SYN/ACKのseq)
```

`snd_una == snd_nxt` なら、送ったものはすべて確認済みです。

## 状態遷移(能動オープン)

```
   CLOSED
     │  connect(): SYN を送る
     ▼
   SYN_SENT
     │  SYN/ACK を受け取る → ACK を送る
     ▼
   ESTABLISHED
```

「能動オープン」は自分から接続しにいく側(クライアント)、「受動オープン」は待ち受ける側(サーバー、Step 4)です。

## コードを読む

`steps/step03_connect.py` の `Connection` クラスです。

### `connect()`

```python
self.iss = random.randint(0, 2**32 - 1)
self.snd_una = self.iss
self.snd_nxt = self.iss
self._send(SYN, options=mss_option(1460))   # seq = snd_nxt = iss
self.snd_nxt += 1                            # SYN はシーケンス番号を1つ消費する
self._set_state("SYN_SENT")

while self.state == "SYN_SENT":
    seg = self._recv()
    if seg is not None:
        self._handle_syn_sent(seg)
```

送った直後、`snd_una = iss`、`snd_nxt = iss + 1`。「iss番のバイト(SYN)を送ったが、まだ確認されていない」という状態です。

### `_handle_syn_sent()`

```python
if seg.ack_flag and not (self.snd_una < seg.ack <= self.snd_nxt):
    return                                  # 範囲外の ack は無視
if seg.syn and seg.ack_flag:
    self.irs = seg.seq
    self.rcv_nxt = seg.seq + 1              # 相手の SYN も1つ消費する
    self.snd_una = seg.ack                  # 自分の SYN が確認された
    self.snd_wnd = seg.window
    self._send(ACK)                         # 3発目。seq = snd_nxt, ack = rcv_nxt
    self._set_state("ESTABLISHED")
```

**ackの受け入れ条件** `snd_una < ack <= snd_nxt` は、これ以降のすべての状態で使う重要な式です。

- `ack <= snd_una`:すでに確認済みの部分へのACK。古い重複なので無視
- `ack > snd_nxt`:まだ送っていないものへのACK。ありえないので無視
- その間だけが「新しい確認」

### `_recv()` のフィルタ

rawソケットには、このホストに届いたTCPパケットが全部流れてきます。**4つ組(自分のIP・ポート、相手のIP・ポート)** が一致するものだけを自分の接続のものとして扱います。この4つ組が、TCP接続の識別子です。

## 動かす

端末A:

```bash
lab/lab.sh exec host2 nc -l -p 9000
```

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step03_connect.py 9000
```

### 期待される出力

```
--> 50301 > 9000: Flags [S], seq 297318338, ack 0, win 65535, length 0
    state: CLOSED -> SYN_SENT
<-- 9000 > 50301: Flags [S.], seq 935025631, ack 297318339, win 64240, length 0
--> 50301 > 9000: Flags [.], seq 297318339, ack 935025632, win 65535, length 0
    state: SYN_SENT -> ESTABLISHED

接続完了。snd_nxt=297318339 rcv_nxt=935025632
10秒待ちます。今のうちに  lab/lab.sh exec host2 ss -tan  で相手側の状態を見てください
```

3発目の `seq 297318339` は iss+1、`ack 935025632` は相手のseq+1 になっています。

### 相手側の状態

10秒のあいだに端末Cで:

```bash
$ lab/lab.sh exec host2 ss -tan
State   Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN  0      1          0.0.0.0:9000        0.0.0.0:*
ESTAB   0      0         10.0.1.1:9000       10.0.0.1:50301
```

**カーネルのTCPが、自作TCPとの接続を ESTAB と認めています。** これが、このステップの合格ラインです。

## 壊してみる

**closeしないで終了する(スクリプトはそうなっている)**
10秒後にスクリプトが終わったあと、もう一度 `ss -tan` を見てください。host2側には `ESTAB` が残ったままです。TCPには「相手のプロセスが死んだ」ことを知る手段がなく、FINかRSTが届くまで接続が存在し続けます。実際のサーバーでは、これを片付けるためにkeepaliveやアプリ側のタイムアウトが使われます。

さらに端末Aの `nc` を Ctrl+C で止めると、host2のカーネルはFINを送ります。返事がないので、FINを何度か再送してからあきらめます(`ss -tan` で `FIN-WAIT-1` が見え、やがて消えます)。スニファで眺めてみてください。

**3発目のackをわざとずらす**
`self._send(ACK)` の前に `self.rcv_nxt += 1` を入れてみてください。相手は、身に覚えのないackを受け取ったとき何をするか。`ss` の状態はESTABになるか。

**ackの受け入れ条件を消す**
最初の `if` を消して、Step 2のように待ち受けていないポート(9999)につないでみてください。RSTをどう扱うべきかが見えてきます。

## 理解チェック

1. `snd_una` と `snd_nxt` の違いを、SYNを送った直後の値で説明する
2. `rcv_nxt` は何に使う値か。ハンドシェイクのどこで決まるか
3. ackを受け入れる条件 `snd_una < ack <= snd_nxt` の、両端がそれぞれ何を弾いているか
4. 自作側のプロセスが終了しても、相手側の接続が残るのはなぜか

## 参考

- RFC 9293 3.3.1 "Send Sequence Variables" / "Receive Sequence Variables"
- RFC 9293 3.10.7.3 "SYN-SENT STATE"
