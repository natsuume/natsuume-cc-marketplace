<!--
  agent-discipline: 分業規律の自己ゲート前置き (モデル判定不能時)
  #193 で fable-discipline から移設し、連結配送に合わせて無視射程をブロック限定に調整
  (codex review P2 対応)。#194 で判定不能時の配送本文が discipline-fable.md から
  discipline-sonnet.md (非 Fable 向け) に変わったため、文面を「非 Fable はそのまま適用 /
  Fable は要点読みで暫定適用 (one-shot 補正で Fable 版が再配送される)」に更新。
  issue #236 (注入ペイロード分割) で常時ルールと分業規律が別メッセージに分割されたため、
  射程を「本メッセージ (このメッセージ単体) の分業規律ブロック」に再定義した
  (旧: 直前見出し〜メッセージ末尾という位置依存の射程。分割後は隣接メッセージが存在しない
  ため成立しない)。inject-discipline.sh (UserPromptSubmit) がマーカー無し/pending 時に
  見出し「# agent-discipline: 分業規律 (Sonnet)」+ 本ファイル + discipline-sonnet.md の順で
  1 メッセージとして配送する。PR2 (agent-discipline 0.21.0) で inject-discipline.sh が
  fable / opus / その他非 fable の 3-way 配送に拡張されたため、確定後の one-shot 補正への
  言及も Fable 版と Opus 版の両方に拡張した。
-->

(自己ゲート) このセッションのモデルを hook からは判定できなかった。本メッセージの分業規律ブロック (この前置きとセクション 1〜4、discipline-sonnet.md 本文) は、SONNET 向けの書式で書かれた非 Fable モデル向けの分業規律である。あなた (メインセッション) が非 Fable かつ非 Opus のモデル (Sonnet / Haiku 等) の場合はそのまま適用すること。Fable または Opus 系の場合は、それぞれ対応する版の分業規律が後続の one-shot 補正 (最大 1 プロンプト遅延) で再配送されるまでの暫定として、各セクションの見出しと冒頭 1〜2 文 (意図と適用範囲の要点) を優先して読み、細部の列挙は必要な時にのみ参照すること。ただしセクション 2 の「model 未指定の継承でよい」は Fable セッションには適用されない: モデルが確定するまでは、サブエージェントの model を常に非 Fable (Sonnet 系等) で明示し、未指定の継承を使わないこと (未指定だと継承先が Fable になり、この時点では検知層の state も未確定のため機械的に防げない)。自分のモデルは自身の system prompt のモデル情報 (例: Environment セクション) で確認できる。
