# AIエージェント向けエンドツーエンド動画編集の不足機能

更新日: 2026-07-28
対象: AviUtl2 Live Bridge 0.7.1 / protocol v1

## 結論

0.7.1は、ユーザーが開いて許可したAviUtl2へAIが安全に接続し、素材やテキストを
配置・編集して、AviUtl2本体の合成フレームで確認する「監督付きライブ編集beta」
として実用的である。

一方、素材の整理からカット、音声調整、字幕、完成検査、保存、動画書き出しまでを
AIだけで通すには、まだ重要な編集プリミティブとジョブ制御が不足している。
特にtrim/ripple、映像と音声のリンク編集、音声レンダー、複数クライアント、
変更通知、保存・書き出しが1.0の主要な境界になる。

## 0.7.1の現在地

| 工程 | 現在の対応 | 判定 |
|---|---|---|
| 対象選択 | PID指定、複数プロセス時の曖昧選択拒否 | 対応済み |
| アクセス許可 | プロセスごとに既定OFF、GUIでEnable/Disable | 対応済み |
| タイムライン取得 | revision付きsnapshot、layer snapshot | 対応済み |
| 素材確認・配置 | AviUtl2入力プラグインによるprobeとnative create | 対応済み |
| オブジェクト編集 | item/text/X/Y、移動、削除、複製 | 基本対応 |
| エフェクト | catalog、inspection、追加、削除、値変更 | 一部対応 |
| 速度・分割 | 固定位置の基本video/audio clipに限定 | 制限付き |
| アニメーション | track値と移動モードの取得・設定 | 基本対応 |
| 一括操作 | Alias作成batch、事前衝突検査、1 Undo単位 | 作成限定 |
| 映像確認 | AviUtl2本体による単一フレームPNG | 対応済み |
| 音声確認 | 未公開 | 未対応 |
| 保存・書き出し | 未公開 | 未対応 |
| 変更監視 | pollingのみ | 未対応 |
| 複数エージェント | Pipeは単一クライアント | 未対応 |

## 1.0までの必須候補

### 1. セッション、競合、復旧

- 複数クライアントを受け付け、SDK操作だけを公平に直列化する。
- `client_id`、session ID、idempotency keyを全mutationへ付け、通信再送による
  二重作成・二重削除を防ぐ。
- AviUtl2のobject、cursor、scene、focus変更イベントをsequence付きで配信する。
  eventは変更通知に限定し、受信後はsnapshotを再取得する。
- snapshotをlayer、frame range、object IDで絞り込み、長い動画でも全Aliasを毎回
  転送しない。
- revision依存の一時IDとは別に、エージェントが字幕・BGM・話者・素材を再識別
  できる永続的なsemantic IDまたはannotationを持つ。
- mutationのdry-run、対象一覧、予想差分、警告、Undo単位を返す。
- 長時間renderにはprogress、timeout、cancel、上限値を用意する。

### 2. 実用的なカット編集

- clip先頭・末尾のtrim、timeline duration変更、source in/out変更。
- ripple insert/delete、gap close、指定範囲の詰め・空け。
- 複数オブジェクトの相対位置を保つmove、copy、delete。
- 映像と付随音声、字幕、効果音をリンクしてsplit/trim/moveする仕組み。
- linked A/V clipのframe rate・sample rate差を含む同期検証。
- multi-section、移動する再生位置、reverse、loop、可変速を扱えるsource-time map。
- collision解決方針を「拒否」「空きlayerへ移動」「ripple」のように明示する。

現SDKにはオブジェクト長の直接setterとsplit専用APIがない。0.7.1のAlias置換方式を
拡張する場合も、作成後inspection、失敗時の復旧情報、非原子的な範囲の明示が必要。

### 3. layer、effect、sectionの完全な編集

- layerのname、enable、lock変更。
- effectのenable/disable、lock変更。
- 中間点（section）の列挙、追加、削除、移動と、sectionごとの値編集。
- effect順序変更。現SDKには公開されたreorder関数が見当たらないため、
  SDK追加要望または検証付きAlias置換が必要。
- 複数effect/item更新を一つのtransactionにまとめる。
- effect/itemの型だけでなく、選択肢、単位、最小・最大、semantic roleを返す。
  例えば「音量」「不透明度」「source file」を日本語名の推測なしに操作できる
  schemaが望ましい。

### 4. 素材管理

- タイムライン内の参照ファイル一覧、重複素材、未使用素材、missing mediaを取得。
- ファイル差し替えを単体・一括で行い、解像度、duration、track構成の変化を警告。
- 相対パス、UNC、Unicode、長いパス、移動したプロジェクトのrelink試験。
- proxy/cacheの作成状態と、元素材へ戻した最終確認。
- video/audio stream、回転情報、alpha、色空間など、編集判断に必要なprobe情報。
- agentが読み書き可能な素材ディレクトリのallowlistと監査。

### 5. 音声編集と検査

- SDKの`rendering_scene_audio()`を使ったframe/range音声取得。
- waveform、peak、RMS、無音区間、clip検出、integrated loudnessの分析。
- 音量、pan、fade、ducking、noise reduction用effectの高水準操作。
- ナレーション、BGM、SEのroleと、role別のlayer/preset管理。
- 映像と音声の同期点を比較できるpreview API。

最終的なloudness基準は投稿先や運用で変わるため、固定値ではなく出力profileに
持たせる。

### 6. テキスト、字幕、多言語

- SRT、WebVTT、ASS等を正規化し、複数text objectへ一括配置するAPI。
- 字幕の分割・結合、表示時間、読み速度、重なり、safe areaの検査。
- style preset、話者、言語、縦横書き、ルビ相当表現の管理。
- SDKのfont列挙を公開し、missing fontや代替fontを事前検出。
- YouTube/Niconico/bilibili向けに、焼き込み字幕と別ファイル字幕を分離して生成。
- 音声認識・翻訳自体は外部AIの責任とし、Bridgeはtimecode付き結果を安全に
  タイムラインへ反映する。

### 7. トランジション、合成、定型演出

- transition catalogと、前後clipを指定する高水準な追加API。
- crop、mask、chroma key、blend、camera、picture-in-pictureのtyped helper。
- intro、lower third、comment風表示、ending、shorts用縦画面などの
  version付きtemplate。
- template適用前の必要font/effect/script/module検査。
- aspect ratio変更時のreframeとsafe area検査。

低水準の`set_property()`でも多くは操作できるが、エージェントがeffect名とitem名を
推測するだけでは、プラグイン差や言語差に対して安定しない。

### 8. 映像レビューと品質検査

- range render、frame sequence、filmstrip/contact sheet。
- 編集前後またはrevision間の同一frame比較。
- 黒画面、静止画継続、欠落素材、字幕切れ、意図しないgap/overlapの検出。
- sceneの映像と音声を同じrevisionで取得したことを保証するcapture session。
- preview sampleを「先頭・末尾・cut境界・字幕境界・ランダム区間」から自動選択。
- UI全体のスクリーンショットとscene renderを区別する。SDKの
  `rendering_scene_video()`が返すのは完成映像であり、タイムラインやマウスを
  含むユーザー画面ではない。

### 9. scene、プロジェクト、Undo

SDKで現在sceneのname、size、frame rate、sample rateは変更できる。ただしSDK自身が
scene設定変更はUndo非対応と明記しているため、別権限と明示確認が必要。

必要なAPI:

- 現在scene設定の取得・変更とprofile検証。
- scene一覧、作成、複製、切替、削除。
- projectの新規作成、open、save、save as、dirty state。
- checkpoint名と、操作監査ログ。
- Undo/Redoの実行と履歴確認。
- preview再生、停止、seek、loop range。

公開SDKには現在scene以外のscene管理、通常のproject open/save、Undo/Redo実行、
playback transportのAPIが見当たらない。ここはSDK追加要望がない限り、GUI操作または
人間の確認工程を残す必要がある。

### 10. 完成動画の書き出し

最低限必要な契約:

- 利用可能な出力先・encoder・presetの列挙。
- container、codec、解像度、frame rate、bitrate/quality、audio設定の検証。
- output range、出力パス、上書き方針。
- start、progress、cancel、成功・失敗結果、生成ファイルhash。
- 書き出し中の編集拒否と、完了後のsample再読込またはprobe。
- 投稿先profile。YouTube長尺/Shorts、Niconico、bilibili等の要件は
  Bridgeへ固定せず更新可能な設定として持つ。
- thumbnail候補、chapter/timecode、字幕ファイル等の納品物manifest。

SDKの`OUTPUT_PLUGIN_TABLE`は出力プラグインを登録する側のAPIであり、既存出力
プラグインを列挙・選択・起動するホストAPIではない。現状の選択肢は次の二つ。

1. AviUtl2 SDKへ、既存出力プラグインの列挙・設定・開始・進捗・中止APIを要望する。
2. Bridgeでsceneの連続映像・音声をnative renderし、Python側encoderへ渡す。

2は合成結果自体はAviUtl2準拠だが、既存出力プラグインの設定・性能・色処理と
同一とは限らない。1.0で「完全自動書き出し」を保証するなら、この差を仕様に
明記して実機比較試験を行う必要がある。

## AviUtl2 SDKへ希望したい追加機能

優先度順:

1. project open/save/save-as、dirty state、Undo/Redo。
2. output pluginの列挙、設定schema、start/progress/cancel。
3. object duration/source in/outの直接setterとnative split。
4. linked/grouped objectの列挙と一括編集。
5. sceneの列挙、作成、複製、切替、削除。
6. effect reorder。
7. playback start/stop/seek/loop。
8. timeline marker/chapter、永続object IDまたはplugin用object metadata。

これらが追加されると、非公開window messageやGUI自動操作に頼らず、本体のUndo、
保存、出力プラグイン互換性を保った自動化が可能になる。

## 推奨リリース順

### 0.8: 安全なagent session

- multi-client scheduler、session identity、idempotency。
- event watch、差分取得、filtered snapshot。
- mutation dry-run、操作結果・警告・監査。

### 0.8.x: core timeline editing

- trim/duration/source range、ripple、gap close。
- linked A/Vと複数object transaction。
- layer/effect/sectionの残りのSDK直結操作。

### 0.9: 制作ワークフロー

- audio render/analysis、字幕batch、asset inventory/relink。
- range render、filmstrip、QC rule。
- scene profile、font/module preflight、template。

### 0.9.x: finalization

- 保存・checkpointの境界を確定。
- SDK出力制御またはBridge-managed native render export。
- 出力profile、progress/cancel、納品物manifest。
- clean install、upgrade、rollback、互換性試験。

### 1.0 release gate

以下を一つのテストprojectで中断なく完走する。

1. 明示したprocess/sessionへ接続し、素材と環境をpreflight。
2. 素材を配置し、linked A/Vをcut、trim、ripple編集。
3. 字幕、BGM、SE、effect、animationを適用。
4. native映像・音声renderでcut境界と代表区間を検査。
5. GUI編集や別clientとの競合を検知し、再同期。
6. checkpointを作り、失敗時に文書化した方法で復旧。
7. profileに従って完成動画と字幕・thumbnail・chapter manifestを生成。
8. 生成物をprobeし、duration、映像、音声、hash、警告を最終報告。

このgateを満たすまでは「AIが編集作業をすべて通しで実施できる」とは表現せず、
「ユーザー監督下で安全にライブ編集・native frame確認できる」と説明するのが
妥当である。

## Bridgeの対象外に保つもの

- 動画サイトからの無断download、権利処理、利用規約判断。
- YouTube/Niconico/bilibiliアカウントの資格情報保管。
- 投稿、公開範囲、収益化等の最終操作。
- 音声認識、翻訳、素材生成そのもの。

これらは別のagent toolへ分離し、BridgeはローカルAviUtl2の編集状態・完成映像・
編集権限に責任を限定する。投稿toolへ渡す場合は、完成ファイルとmanifestだけを
明示的なユーザー承認後に受け渡す。
