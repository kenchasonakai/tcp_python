# Step 5: データの送受信(send / recv)

## ゴール

- データを載せたセグメントを送り、ACKで確認されるまで待つ
- 届いたデータを順番どおりに受信バッファへ入れ、ACKを返す
- ウィンドウ(フロー制御)の意味を動きで理解する

## seq と ack の進み方

ハンドシェイクが終わった時点で、両者はこうなっています(数字はStep 3の例)。

```
   自分: snd_nxt = 297318339 (iss+1)     rcv_nxt = 935025632 (irs+1)
   相手: snd_nxt = 935025632             rcv_nxt = 297318339
```

自分が5バイト送ると:

```
   -->  seq 297318339, length 5         ← 297318339〜297318343 の5バイト
        自分の snd_nxt = 297318344       ← 送ったので進む(まだ未確認)
   <--  ack 297318344                   ← 「344 から送ってほしい」= 343 まで受け取った
        自分の snd_una = 297318344       ← 確認済みになる
```

**ackは「受け取った最後の番号」ではなく「次に欲しい番号」**です。この規則のおかげで、「seq X, length N」に対する ack は常に X+N と計算できます。

## バイトストリーム

TCPはメッセージの境界を保存しません。あとで実際に見ますが、ncで `hello` と `world` を別々に打っても、自作側には `b'hello\nworld\n'` が1回で届くことがあります。逆に1回の `send` が複数のセグメントに分かれて届くこともあります。**区切りが必要ならアプリが自分で決める**(改行、長さ、など)のがTCPの約束です。

## フロー制御:ウィンドウ

受信側はセグメントごとに「あとこれだけ受け取れる」(Window)を伝えます。送信側は、**未確認のまま送っていい量**をその値以下に抑えます。

```
   in_flight = snd_nxt - snd_una         送ったが未確認の量
   room      = snd_wnd - in_flight       あとこれだけ送っていい
```

これは「受信側のバッファを溢れさせない」ための仕組みで、「ネットワークを溢れさせない」輻輳制御(Step 8)とは別物です。

## コードを読む(`[Step 5]` の部分)

### 受信:`_handle_other()` の後半

```python
if seg.seg_len == 0:
    return                                   # 純粋なACK。ここで終わり
if seg.seq != self.rcv_nxt:
    self._send(ACK)                          # 順番が違う。今欲しい番号を伝え直す
    return
if seg.payload:
    self.recv_buf += seg.payload             # 受信バッファへ
    self.rcv_nxt += len(seg.payload)
if seg.fin:
    ...                                      # Step 7
self._send(ACK)                              # ack = rcv_nxt
```

`seg.seq == rcv_nxt` のときだけ受け入れる、という単純なルールです。それより前のseqなら重複(すでに持っている)、後ろなら間が抜けている(まだ受け取れない)。どちらの場合も「今欲しいのはrcv_nxtです」とACKで伝え直します。本物のTCPは「間が抜けている」セグメントを取っておいて後で埋めますが、ここでは捨てます。

### 受信:`recv()`

```python
self._run_until(lambda: len(self.recv_buf) > 0 or self.fin_received)
data = bytes(self.recv_buf[:max_bytes])
del self.recv_buf[:max_bytes]
return data
```

バッファに何か入るまでループを回すだけです。相手がFINを送ってきていたら空のバイト列を返します(普通のソケットの `recv` が0を返すのと同じ)。

### 送信:`send()`

```python
while offset < len(data) or self.snd_una < self.snd_nxt:
    in_flight = self.snd_nxt - self.snd_una
    room = self.snd_wnd - in_flight
    if offset < len(data) and room > 0:
        chunk = data[offset : offset + min(MSS, room)]
        self._send(PSH | ACK, chunk)
        offset += len(chunk)
    else:
        self._step()                        # ACK やウィンドウ更新を待つ
```

MSSと相手のウィンドウの両方を守りながら送り、**全部ACKされるまで**(`snd_una == snd_nxt`)戻りません。本物のソケットの `send()` はカーネルのバッファに入れた時点で戻りますが、学習用に単純化しています。

### 広告するウィンドウ:`_send()`

```python
window = max(0, self.rcv_wnd - len(self.recv_buf))   # 空き分だけ広告する
```

アプリが `recv()` で読み出すまでバッファに残るので、その分だけ広告するウィンドウが減ります。

## 動かす

### エコーサーバー(自作が待ち受け、カーネルが送る)

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step05_data.py server 9000
```

端末A(文字を打って Enter。Ctrl+D で終了):

```bash
lab/lab.sh exec host2 nc 10.0.0.1 9000
```

端末Bの出力(`hello` と `world` を打った例):

```
[  0.435] <-- 59114 > 9000: Flags [P.], seq 521379269, ack 1936195871, win 64240, length 12
[  0.435] --> 9000 > 59114: Flags [.], seq 1936195871, ack 521379281, win 65523, length 0
    受信: b'hello\nworld\n' → そのまま送り返す
[  0.435] --> 9000 > 59114: Flags [P.], seq 1936195871, ack 521379281, win 65535, length 12
[  0.435] <-- 59114 > 9000: Flags [F.], seq 521379281, ack 1936195871, win 64240, length 0
[  0.435]     state: ESTABLISHED -> CLOSE_WAIT
[  0.435] --> 9000 > 59114: Flags [.], seq 1936195883, ack 521379282, win 65535, length 0
相手が FIN を送ってきた(これ以上データは来ない)
終了します(close は Step 7 で実装する)
```

読みどころ:

- `length 12` を受け取って `ack 521379281` = 521379269 + 12 を返している
- そのときの `win 65523` = 65535 − 12。バッファに12バイト残っているので、その分減っている。次の送信では `recv()` で読み出したあとなので 65535 に戻っている
- `[P.]` = PSH+ACK。PSHは「バッファにためずにすぐアプリに渡して」の印で、対話的な通信ではほぼ毎回付く
- 自作側が送り返した `length 12`(seq 1936195871)に対するACKは、すぐには返っていない。次に届いたFINは `ack 1936195871` のままで、その後の `[.] ack 1936195883` でようやく確認されている。ACKはセグメント1つに1つ返るとは限らず、届いた時点での「次に欲しい番号」を伝えるだけ

端末A側には、打った文字がそのまま返ってきているはずです。

### クライアント(カーネルが待ち受け、自作が送る)

端末A:

```bash
lab/lab.sh exec host2 nc -l -p 9000
```

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step05_data.py client 9000 "hello from toy tcp"
```

端末Aに `hello from toy tcp` が表示されます。端末Aで何か打って Enter すると、端末Bに `受信: b'...'` が出ます。

## 観察する:closeしないとどうなるか

エコーサーバーの例では、ncを Ctrl+D で終了させると、自作側はFINを受け取って `CLOSE_WAIT` になったまま、closeせずにプロセスを終了しています。そのあと host2 を見ると:

```bash
$ lab/lab.sh exec host2 ss -tan
FIN-WAIT-2  0  0  10.0.0.2:59114  10.0.0.1:9000
```

カーネル側は「自分はFINを送って確認された。相手のFINを待っている」状態で止まっています。アプリが `close()` を忘れると、相手側にこの状態が残ります(Linuxは一定時間で回収しますが、それはTCPの仕様ではなく実装の親切です)。

## 壊してみる

**大きなデータを送る**
`steps/step05_data.py client 9000 "$(python3 -c 'print("x"*5000)')"` のように5000バイト送ると、MSS(1460)ごとに4つに分かれて送られ、それぞれにACKが返るのが見えます。相手のウィンドウ(64240)より大きいデータ(例:100000バイト)だと、途中で `_step()` してACKを待つ様子が出ます。

**ウィンドウを小さくする**
`Connection.__init__` の `self.rcv_wnd = 65535` を `10` にして、エコーサーバーに長い行を打ち込んでください。カーネル側は10バイトずつしか送ってこなくなります。相手の広告ウィンドウを守っている証拠です。

**順番のチェックを外す**
`if seg.seq != self.rcv_nxt:` の分岐を消して、Step 6 のロス実験(`--loss-in`)をすると、重複したデータが二重にバッファへ入るのが分かります。

## 理解チェック

1. seq=1000 で 300バイト送ったとき、返ってくる ack の値はいくつか
2. `recv_buf` に読み出していないデータが100バイトあるとき、広告するウィンドウはいくつか
3. `snd_una < snd_nxt` のあいだ `send()` が戻らないのはなぜか
4. 「hello」「world」を別々に打ったのに1つのセグメントで届いた。TCPのどの性質によるものか

## 参考

- RFC 9293 3.4 "Sequence Numbers"、3.8.6 "Managing the Window"
