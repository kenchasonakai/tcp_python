# Step 2: SYNを1発送る

## ゴール

- rawソケットで、自分で組み立てたTCPセグメントをネットワークに送り出す
- カーネルのTCPから返ってくるSYN/ACKを読む
- 3発目を返さないとどうなるかを観察する

## rawソケット:どこまで自分でやるか

```python
socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
```

このソケットは、TCP層を素通りしてIP層に直接つながります。

| | 普通のソケット | rawソケット(IPPROTO_TCP) |
|---|---|---|
| TCPヘッダ | カーネルが作る | **自分で作る** |
| IPヘッダ | カーネルが作る | カーネルが作る(宛先IPだけ渡す) |
| 受信 | 自分の接続のデータだけ届く | このホストに届いた**TCPパケット全部**が、IPヘッダ付きで届く |

つまり、作るのはTCP層だけで、IPより下はカーネルに任せます。受信側はIPヘッダを自分で読み飛ばす必要があります(`tcp/rawsock.py` の `recv`)。

```python
ihl = (packet[0] & 0x0F) * 4      # IPヘッダ長(4バイト単位)
src_ip = packet[12:16]             # 送信元IP
tcp_bytes = packet[ihl:total_len]  # その後ろがTCPセグメント
```

## コードを読む

`steps/step02_syn.py` は40行ほどです。やることは3つ。

1. ランダムな送信元ポートと**初期シーケンス番号(ISS)**を決める
2. SYNフラグを立てたセグメントを1つ送る(MSSオプション付き)
3. 自分のポート宛てに返ってきたセグメントを表示し続ける

## 動かす

端末A(相手役):

```bash
lab/lab.sh exec host2 nc -l -p 9000
```

端末B:

```bash
lab/lab.sh exec host1 python3 steps/step02_syn.py 9000
```

### 期待される出力

```
--> 53540 > 9000: Flags [S], seq 1168351259, ack 0, win 65535, length 0
    (iss = 1168351259)
<-- 9000 > 53540: Flags [S.], seq 4262162347, ack 1168351260, win 64240, length 0  checksum=OK
    ack - iss = 1  (相手は iss+1 を要求している)
    相手の options = {'mss': 1460}
<-- 9000 > 53540: Flags [S.], seq 4262162347, ack 1168351260, win 64240, length 0  checksum=OK
    ack - iss = 1  (相手は iss+1 を要求している)
    相手の options = {'mss': 1460}
<-- 9000 > 53540: Flags [S.], seq 4262162347, ack 1168351260, win 64240, length 0  checksum=OK
    ...
(8秒たったので終了)
```

## 何が起きたか

**1. カーネルは自分で作ったSYNをちゃんと受け付けた**
`checksum=OK` で、SYN/ACKが返ってきています。Step 1のヘッダ組み立てが正しい証拠です。

**2. ack = iss + 1**
こちらのSYNにはデータが0バイトなのに、相手は「次は iss+1 から」と言っています。SYNがシーケンス番号を1つ消費するからです。こうしておくと、「SYNを受け取った」という事実そのものをACKで確認できます(データと同じ仕組みで)。

**3. 相手も自分のISSをランダムに選んでいる**
`seq 4262162347` が相手のISSです。毎回変わります。ISSがランダムなのは、古い接続のセグメントが遅れて届いたときに混ざらないようにするためと、外から接続を乗っ取りにくくするためです。

**4. MSSの交換**
双方がMSSオプション(1セグメントに載せるデータの最大値)を伝え合っています。1460 = Ethernetの1500 − IPヘッダ20 − TCPヘッダ20 です。

**5. SYN/ACKが再送されている**
こちらが3発目のACKを返さないので、カーネルは「SYN/ACKが届かなかったのかも」と判断して再送しています。タイミングを見ると、**約1秒、2秒、4秒**と間隔が倍々に伸びます(指数バックオフ)。Step 6で自分でも実装します。

## 観察する

出力が流れている8秒のあいだに、端末Cで相手側の状態を見てください。

```bash
$ lab/lab.sh exec host2 ss -tan
State     Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN    0      1          0.0.0.0:9000        0.0.0.0:*
SYN-RECV  0      0         10.0.0.2:9000       10.0.0.1:53540
```

`SYN-RECV` が、3発目を待っている「半開き(half-open)」の接続です。この状態の接続をわざと大量に作って、相手のメモリを食いつぶす攻撃が **SYN flood** です。対策として、Linuxは半開きが増えすぎるとSYN cookieという仕組みに切り替えて、状態を持たずにSYN/ACKを返します。

## 壊してみる

**待ち受けていないポートに送る**

```bash
lab/lab.sh exec host1 python3 steps/step02_syn.py 9999
```

```
--> 52506 > 9999: Flags [S], seq 397704388, ack 0, win 65535, length 0
<-- 9999 > 52506: Flags [R.], seq 0, ack 397704389, win 0, length 0  checksum=OK
    RST: 相手はこのポートで待ち受けていない
```

`[R.]` = RST+ACK。「そこには誰もいない」という即答です。`curl` で "Connection refused" と出るのは、これを受け取ったときです。

**チェックサムを壊す**
`build()` の呼び出しのあと、`syn = syn[:16] + b"\x00\x00" + syn[18:]` を挟んでチェックサム欄をゼロにして送ってください。相手は何も返してきません。壊れたセグメントは黙って捨てられます。

**ACKフラグも立てて送る**
`flags=SYN | ACK` にしてみてください。LISTEN状態の相手は、身に覚えのないACKに対してRSTを返します。

## 理解チェック

1. rawソケットを使うとき、自分で作るヘッダはどれで、カーネルに任せるヘッダはどれか
2. SYN/ACKの ack がこちらの iss + 1 になる理由
3. SYN/ACKが何度も届いたのはなぜか。間隔はどう変化したか
4. `ss` で見えた `SYN-RECV` は何を待っている状態か

## 参考

- RFC 9293 3.4 "Sequence Numbers"、3.5 "Establishing a Connection"
- RFC 4987 (TCP SYN Flooding Attacks and Common Mitigations)
