#!/bin/bash
# portable-time.sh — 時刻変換の GNU/BSD 両対応 helper (source して使う。直接実行しない)
#
# Linux (WSL2) と macOS の date/stat コマンド差分の吸収はこのファイルに集約する
# (リポジトリ CLAUDE.md の両 OS 対応規約)。python3 等の追加依存は使わない。
#
# 提供する関数 (I/O 契約。すべて stdout に値を返し、失敗時は非ゼロ exit):
#   now_epoch            : 現在時刻の epoch 秒
#   now_iso              : 現在時刻の ISO 8601 UTC (%Y-%m-%dT%H:%M:%SZ)
#   epoch_to_iso <epoch> : epoch 秒 → ISO 8601 UTC。GNU: date -u -d @N / BSD: date -u -r N
#   iso_to_epoch <iso>   : ISO 8601 UTC → epoch 秒。
#                          ※自前生成の固定フォーマット (%Y-%m-%dT%H:%M:%SZ) のみ対応すれば
#                          よい (cache written_at は cache-write-wrapper.sh が同形式で書く)。
#                          GNU: date -u -d "$iso" / BSD: date -u -j -f '%Y-%m-%dT%H:%M:%SZ'
#
# 呼び出し側の注意: 入力が不正な場合に握り潰さず非ゼロ exit を返すこと
# (fetch-rate-limit.sh はこれを「キャッシュ stale」判定に使う)。

now_epoch() {
  date +%s
}

now_iso() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

# epoch_to_iso <epoch>
# GNU date は `-d @N`、BSD/macOS date には `-d` が無く `-r N` を使う。
# 両方試して、成功した方の出力を採用する (失敗した側の stderr は捨てる)。
epoch_to_iso() {
  local epoch="$1"
  [ -n "$epoch" ] || return 1
  date -u -d "@$epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -r "$epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null
}

# iso_to_epoch <iso>
# 自前生成の固定フォーマット (%Y-%m-%dT%H:%M:%SZ) のみ対応すればよい。
# GNU date は `-d "$iso"` で直接パースできる。BSD/macOS date は `-j -f` で
# 入力フォーマットを明示する必要がある。
iso_to_epoch() {
  local iso="$1"
  [ -n "$iso" ] || return 1
  date -u -d "$iso" +%s 2>/dev/null \
    || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$iso" +%s 2>/dev/null
}
