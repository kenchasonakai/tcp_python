# Step 1: TCPヘッダを組み立てる・読む

## ゴール

- TCPヘッダの各フィールドを、バイト列として自分で組み立てられる
- 受け取ったバイト列からフィールドを取り出せる
- チェックサムを計算・検証できる

このステップは仮想ネットワーク不要です。`python3 -m unittest` だけで進みます。

## TCPヘッダの構造

TCPが送る単位を**セグメント**と呼びます。セグメント = ヘッダ(20バイト以上) + データです。

```
     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-------------------------------+-------------------------------+
    |          Source Port          |       Destination Port        |  0-3
    +-------------------------------+-------------------------------+
    |                        Sequence Number                        |  4-7
    +---------------------------------------------------------------+
    |                    Acknowledgment Number                      |  8-11
    +-------+-------+-+-+-+-+-+-+-+-+-------------------------------+
    | Data  | Rsvd  |C|E|U|A|P|R|S|F|                               |
    | Offset|       |W|C|R|C|S|S|Y|I|            Window             |  12-15
    |       |       |R|E|G|K|H|T|N|N|                               |
    +-------+-------+-+-+-+-+-+-+-+-+-------------------------------+
    |           Checksum            |         Urgent Pointer        |  16-19
    +-------------------------------+-------------------------------+
    |                    Options (あれば)                     |  Pad |  20-
    +---------------------------------------------------------------+
    |                             data                              |
    +---------------------------------------------------------------+
```

| フィールド | 大きさ | 役割 |
|---|---|---|
| Source / Destination Port | 各2バイト | どのアプリ宛てか。IPアドレスが建物なら、ポートは部屋番号 |
| Sequence Number | 4バイト | このセグメントのデータの先頭が、バイト列全体の何番目か |
| Acknowledgment Number | 4バイト | 「次はこの番号から送ってほしい」。ACKフラグが立っているときだけ有効 |
| Data Offset | 4ビット | ヘッダの長さ(4バイト単位)。オプションがなければ5(=20バイト) |
| Flags | 8ビット | SYN(接続開始)、ACK(確認)、FIN(終了)、RST(強制切断)、PSH(すぐ渡して)など |
| Window | 2バイト | 「あとこれだけ受け取れる」。フロー制御に使う |
| Checksum | 2バイト | 壊れていないかの検証 |
| Urgent Pointer | 2バイト | 今はほぼ使われない |
| Options | 可変 | MSS(最大セグメントサイズ)など。SYNのときに交換する |

**シーケンス番号はセグメントの番号ではなくバイトの番号**、という点が最重要です。100バイト送ったら次のseqは100増えます。

## コードを読む

`tcp/segment.py` を開いてください。中心は3つの関数です。

### `build()` — 組み立て

```python
HEADER_FORMAT = "!HHIIBBHHH"   # ! = ビッグエンディアン, H = 2バイト, I = 4バイト, B = 1バイト
```

`struct.pack` でフィールドを順番に詰めるだけです。注意点は2つ。

- **ネットワークバイトオーダー**(ビッグエンディアン)。`!` を付け忘れると、ポート80が `0x5000` = 20480 として送られます。
- **Data Offset は上位4ビット**。20バイトなら `5 << 4 = 0x50` です。

### `checksum()` — 検証用の値

```python
def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))   # 2バイトずつ足す
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)              # 桁あふれを下位に足し戻す
    return ~total & 0xFFFF                                    # ビット反転
```

ポイントは「**チェックサム欄を含めて全体を足すと、正しければ0(反転前で0xFFFF)になる**」ことです。検証側は再計算して比較するのではなく、足して0になるかを見るだけで済みます(`verify_checksum`)。

### 疑似ヘッダ

チェックサムの計算には、TCPヘッダとデータに加えて、**送信元IP・宛先IP・プロトコル番号・TCP長**を並べた「疑似ヘッダ」を先頭にくっつけます。これは実際には送信されません。

なぜIPアドレスを混ぜるのか。IPヘッダ自体にもチェックサムはありますが、それはIPヘッダだけを守ります。TCPが疑似ヘッダを含めておくと、**宛先を間違えて届いたセグメント**(IPヘッダが途中で書き換わった場合など)も検出できます。

### `parse()` — 読み取り

`build` の逆です。`Data Offset` を見てヘッダの長さを求め、その後ろをデータとして切り出します。

`Segment.seg_len` は「シーケンス番号空間で消費する長さ」で、データの長さに加えて**SYNとFINを1バイト分として数えます**。あとのステップで「SYNのackはなぜseq+1なのか」の答えになります。

## 動かす

```bash
python3 -m unittest -v
```

```
test_checksum_detects_corruption (tests.test_segment.TestBuildAndParse) ... ok
test_checksum_detects_wrong_destination (tests.test_segment.TestBuildAndParse) ... ok
test_checksum_verifies (tests.test_segment.TestBuildAndParse) ... ok
test_header_layout (tests.test_segment.TestBuildAndParse) ... ok
...
Ran 10 tests in 0.000s
OK
```

## 手で読んでみる

Pythonの対話環境で、自分でバイト列を作って眺めてください。

```python
>>> from tcp.segment import *
>>> seg = build("10.0.0.1", "10.0.0.2", 40000, 80, seq=1000, ack=0, flags=SYN, window=65535)
>>> seg.hex(" ")
'9c 40 00 50 00 00 03 e8 00 00 00 00 50 02 ff ff 25 36 00 00'
```

このhexを、上の図と照らし合わせて読んでみてください。

- `9c 40` = 40000(送信元ポート)、`00 50` = 80(宛先ポート)
- `00 00 03 e8` = 1000(seq)
- `00 00 00 00` = ack(ACKフラグがないので意味なし)
- `50` = Data Offset 5、`02` = SYN
- `ff ff` = window 65535
- `25 36` = checksum、`00 00` = urgent

```python
>>> parse(seg)
Segment(src_port=40000, dst_port=80, seq=1000, ack=0, flags=2, window=65535, ...)
>>> verify_checksum("10.0.0.1", "10.0.0.2", seg)
True
>>> verify_checksum("10.0.0.1", "10.0.0.9", seg)      # 宛先を変えると
False
```

## 壊してみる

- `HEADER_FORMAT` の `!` を消してテストを走らせる。どのテストが、どう落ちるか
- `checksum()` の桁あふれ処理(`while total >> 16`)を消す。テストは落ちるか。落ちないなら、落ちるテストを追加する
- `build()` の `data_offset << 4` を `data_offset` にする。`parse` はどう誤読するか

## 理解チェック

1. seq=1000 で100バイトのデータを送ったとき、次のセグメントの seq はいくつか
2. チェックサムの検証で「再計算して比較」ではなく「足して0か見る」で済むのはなぜか
3. 疑似ヘッダにIPアドレスを含めるのは何を検出するためか
4. SYNフラグの立ったセグメントは、データが0バイトでもシーケンス番号を1つ消費する。それはなぜ必要か(答えはStep 2で確かめる)

## 参考

- RFC 9293 (Transmission Control Protocol) 3.1 "Header Format"
- RFC 1071 (Computing the Internet Checksum)
