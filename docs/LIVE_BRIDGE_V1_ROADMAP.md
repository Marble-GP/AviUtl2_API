# Live Bridge v1.0実用性ロードマップ

## 現在できること

- プロセス単位で外部APIを明示的にEnable/Disable
- PIDを指定した安全な接続先選択
- 現在シーンと全オブジェクトのrevision付きsnapshot
- Pythonモデル/Aliasからのオブジェクト作成
- 既存オブジェクトのitem更新、テキスト、X/Y、配置移動、削除
- stale snapshot、配置衝突、出力中状態の拒否
- 一操作を一つのAviUtl2 Undo単位として実行
- GUIからのオブジェクト単位API編集ロック
- AviUtl2本体によるメディア対応判定・情報取得・ネイティブ配置（0.5.0）
- effect/item/track、移動モード、有効/ロック状態の構造化inspection（0.5.0）
- AviUtl2本体レンダーによるrevision付きPNG取得（0.6.0）
- native effect catalog、layer snapshot、effect追加・削除、verified duplicate（0.7.0）
- timeline長を保つ再生速度変更と基本video/audio clipのguarded split（0.7.1）
- 8 client、FIFO SDK queue、session idempotency、event watch（0.8.0）
- layer/name/effect/section/catalogのSDK直結編集（0.8.1）
- verified duration/trim/reorder、transaction、ripple、object group（0.8.2）
- current scene設定と、SDK不足scene/historyのfail-honest gate（0.9.0）
- media inventory/relink、字幕、native音声、contact sheet、preflight（0.9.1）
- Python `EditingSession`、version/capability manifest統合（0.9.2）

## 追加フェーズ実行計画（2026-07-28更新）

| Release | 実装範囲 | 状態 |
|---|---|---|
| 0.5.1 | 0.5.0機能一式、非ドッキングアクセス制御UI | 実機確認完了 |
| 0.6.0 | AviUtl2本体レンダーPNG、capture chunk/TTL/メモリ制限 | 実装・自動試験・実機確認完了 |
| 0.6.1 | release baseline、文書同期、version方針、CI、0.6 security/lifecycle再試験 | 0.7系へ統合 |
| 0.7.0 | catalog/layer snapshot、verified create、effect操作、duplicate | 実装・実機確認完了 |
| 0.7.1 | playback rate、guarded media split、置換方式のSDK実証 | 実装・実機確認完了 |
| 0.8.0 | 8 client、FIFO scheduler、session、idempotency、event watch | 実装・自動試験完了 |
| 0.8.1 | layer/name/effect/section/catalog詳細編集 | 実装・自動試験完了 |
| 0.8.2 | duration/trim/reorder、transaction、ripple/group | 実装・自動試験完了 |
| 0.9.0 | current scene、scene/history SDK release gate | 実装・自動試験完了 |
| 0.9.1 | inventory/relink、字幕、native audio/QC、contact sheet/preflight | 実装・自動試験完了 |
| 0.9.2 | `EditingSession`、version/changelog/capability manifest統合 | 実装・自動試験完了 |
| 1.0.0 | scene CRUD/switchとBridge専用Undo/Redoを公式SDKで実機完走 | SDK待ち・未達 |

各リリースはprotocol v1へ後方互換メソッドを追加する。破壊的変更が必要な場合だけ
protocol v2を新設し、v1を同時提供する。0.5.0の実機確認ではユーザー所有
オブジェクトを変更せず、専用メディアを空きlayerへ配置してUndoまで確認する。

## v1.0設計原則

1. **意味が確認出来ない成功を返さない**
   - 構造だけでなくeffect/item catalogと作成後inspectionで値を確認する。
   - AviUtl2が未知のAliasキーを無視した場合はwarningまたはerrorとして返す。
2. **競合時に推測して再実行しない**
   - 全mutationでrevisionを必須にし、stale時の自動再試行は禁止する。
   - 通信再試行だけはidempotency keyで重複作成を防ぐ。
3. **複数clientでもSDK編集は直列化する**
   - 接続は並行、SDK read/edit/render投入は共有schedulerで制御する。
   - long-pollや切断待ちはSDK schedulerを占有しない。
4. **eventは変更の合図であり、状態そのものではない**
   - event callbackからSDKを呼ばず、sequenceとtypeだけを記録する。
   - `UPDATE_OBJECT`受信後は必ず新しいsnapshotを取得する。
5. **SDKで原子的に出来ない操作を原子的と表示しない**
   - partial apply、Undo単位、復旧手順をresult/capabilityに含める。
6. **外部APIロックをAPI自身から解除しない**
   - object API lock、locked layer/effectの解除はGUI操作として残す。

## SDK実現可能性

| 機能 | SDK根拠 | 判定 |
|---|---|---|
| effect catalog | `enum_effect_name`, `enum_effect_item` | 直接実装可能 |
| effect add/delete/enable | `create_effect`, `delete_effect`, `set_effect_enable` | 直接実装可能 |
| layer snapshot/control | `get/set_layer_name`, `get/set_layer_enable`, `get/set_layer_lock` | 直接実装可能 |
| duplicate | `get_object_alias`, `create_object_from_alias` | 実装可能、作成後検証必須 |
| section操作 | `get/create/delete/move_object_section` | 直接実装可能 |
| 再生速度の値変更 | `set_object_item_value(..., "再生速度", ...)` | 現在も低水準操作可能 |
| 速度に応じた長さ変更 | 長さの直接setterなし | 置換方式のSDK spike必須 |
| 独立clipへの分割 | split専用SDK APIなし | 2 clone置換のSDK spike必須 |
| trim/length | 長さの直接setterなし | 一時layer置換のSDK spike必須 |
| cursor/focus | `set_cursor_layer_frame`, `set_focus_object` | 直接実装可能、別permission必須 |
| change event | `register_event_listener`と4種の`EVENT_TYPE` | 直接実装可能、callback制約あり |
| multi-client | SDK外のNamed Pipe server構造 | Bridge側で実装可能 |
| idempotency | SDK機能なし | Bridge側LRU/result cacheで実装 |

## v1.0リリースゲート

### 1. メディアの調査・配置・差し替え

SDK:

- `is_support_media_file(file, strict)`
- `get_media_info(file, MEDIA_INFO)`
- `create_object_from_media_file(file, layer, frame, length)`

公開候補:

- `media.probe`（0.5.0実装済み）
- `object.create_from_media_file`（0.5.0実装済み）
- Python `set_media_file()`（0.5.0実装済み）
- Python `add_image()`, `add_video()`, `add_audio()`（0.5.0実装済み）

パスを絶対パスへ正規化し、存在、通常ファイル、AviUtl2での実読込可否を確認する。
結果には映像/音声トラック数、総時間、解像度を含める。`length=0`による
AviUtl2本体の自動長・追加位置調整も、明示的なautoオプションとして扱う。

### 2. 既存オブジェクトの構造化されたinspection

現在のsnapshotはAlias全体を返すが、AIクライアントはeffect/item名と値の形式を
自分で推測する必要がある。

SDK:

- `get_effect_list()`, `get_effect_name()`
- `enum_effect_item()`とitem type
- `get_object_item_value()`
- `get_object_track_info()`, `get_object_track_value()`
- effect enable/lock

公開候補:

- `object.inspect`（0.5.0実装済み）
- `effect.catalog`
- `object.get_value`
- Python `set_property()`（0.5.0実装済み）
- Python `set_animation()`（0.5.0実装済み）

effectは同名重複に備えてsnapshot内indexで識別する。itemは整数、数値、
チェック、テキスト、ファイル、色、選択肢等の型、raw値、現在フレーム値、
移動モード、移動パラメータを返す。

### 3. AviUtl2本体によるフレームPNG

SDKの`rendering_scene_video(frame, ...)`は現在シーンをAviUtl2本体で非同期
レンダリングし、コールバックへ`PIXEL_RGBA`、width、height、pitchを返す。
これはOpenCVによる近似再現ではなく、現在ロードされている入力・スクリプト・
フィルタを使ったAviUtl2の合成結果である。

公開候補:

- `frame.render`（0.6.0実装済み）
- `frame.read_chunk`（0.6.0実装済み）
- `frame.release`（0.6.0実装済み）
- Python `render_frame(frame, output_path)`（0.6.0実装済み）

実装条件:

- read/edit lock内で`wait_rendering_task()`を呼ばない
- レンダリングコールバック内でSDKバッファを即時コピー
- WICまたはPillowでPNG圧縮。圧縮は再レンダリングではない
- 1 MiB JSON上限を維持し、capture IDとチャンク読出しを使う
- capture数、総メモリ、TTL、同時render数を制限
- 出力中はretryableな`HOST_EXPORTING`
- PNGにframe、scene ID、revision、width/height、SHA-256を付随

この機能が返すのは「現在シーンの正確な合成フレーム」であり、AviUtl2の
ウィンドウ枠、タイムライン、マウス、ガイドを含むデスクトップスクリーンショット
ではない。編集結果をVision AIへ共有する用途にはこちらが適切である。

### 4. AviUtl2標準のlayer/effect lockを尊重

SDKにはlayer lockとeffect lockがある。Bridgeのmutationと作成はこれらを
明示的に確認し、`LAYER_LOCKED` / `EFFECT_LOCKED`で拒否する。snapshotにも
layer enable/lock、effect enable/lockを含める。

0.5.0ではmutation/createの拒否と`object.inspect`内のeffect enable/lockを
実装済み。0.7.0で次を追加する。

- `project.get_layers(start, count)`
  - layer、表示名、enable、lock、object count、表示範囲内かを返す
- `layer.set_enabled`
- `layer.set_name`
- `layer.lock`

外部APIからの`layer.unlock`、`effect.unlock`、object API lock解除は公開しない。
APIロックを外部API自身が解除出来る設計にしないためである。locked layer/effectの
編集は従来通り`LAYER_LOCKED` / `EFFECT_LOCKED`で拒否する。

### 5. effect catalogと安全な構造化編集

SDK公開API:

- `enum_effect_name()` / `enum_effect_item()`
- `create_effect()` / `delete_effect()`
- `set_effect_enable()`
- `create_object()`

0.7.0公開候補:

- `effect.catalog`
  - effect名、type、video/audio/filter/camera flag、item名/type
- `object.effect.create`
- `object.effect.delete`
- `object.effect.set_enabled`
- `object.create`
  - source effect、配置、長さ、typed propertiesを指定
- `object.duplicate`

全操作はfresh revision、object API lock、layer lock、effect lockを検査する。
追加・削除・有効化後は同じ編集section内でeffect一覧を再取得し、期待した状態を
確認してから成功を返す。SDKが変更を拒否した場合は
`EFFECT_OPERATION_FAILED`を返す。

#### Aliasの意味検証

現行`batch.validate`は構造と配置だけを検査し、AviUtl2が未知のpropertyを無視しても
作成自体は成功し得る。後方互換性を維持しながら次を追加する。

- `validation_mode: "structure" | "catalog"`
- `verify: "none" | "inspection"`
- commandごとの`applied_items`、`ignored_items`、`value_mismatches`
- Python高水準APIは`catalog + inspection`を既定にする
- raw Alias APIは従来互換の`structure`を維持するがwarningを明示する

作成したobjectを同じ編集section内で削除することはSDKで禁止されるため、検証失敗後の
自動rollbackは行わない。`created_with_warnings`、配置、revision、GUI Undoが必要な
ことを必ず返す。

### 6. duplicate、playback rate、split、section、trim/length

`object.duplicate`は`get_object_alias()`と`create_object_from_alias()`で実装し、
作成後inspectionを行う。複製先の衝突、layer lock、source API lock方針を明示し、
APIロック名は既定で複製しない。

section（中間点）はSDKに直接APIがあるため0.7.1で公開する。

- `object.section.list`
- `object.section.create`
- `object.section.move`
- `object.section.delete`

sectionは一つのobject内の中間点であり、左右を独立して移動・削除出来るclip分割とは
異なる。`object.section.create`を`split`という名前では公開しない。

#### playback rate

動画・音声effectの`再生速度`は現行`object.set_item`でも変更出来る。高水準APIでは
利用者が倍率で指定し、AviUtl2 raw値へ変換する。

- `object.media.set_playback_rate(rate=2.0, duration_mode=...)`
- `duration_mode="keep_timeline"`
  - timeline長は変えず、`再生速度`だけを100%から200%へ変更
  - 0.7.0のtyped property編集で実装可能
- `duration_mode="preserve_source_range"`
  - 同じsource範囲を再生するようtimeline長を倍率の逆数へ変更
  - length置換gate通過後だけstable公開

初期stable版は正の固定倍率だけを扱う。負数の逆再生、速度animation、別objectとして
配置された連携audioは個別の意味検証が必要である。動画effectの`音声付き`で同一
object内に音声を持つ場合は映像・音声同期を実機確認する。

#### true media split

独立clipへの分割専用SDK APIは存在しない。`object.split_media(frame)`は次の
2 clone置換方式をspikeする。

1. split直前にsource Alias、再生位置、再生速度、effect/track/sectionをinspection
2. 空き一時layerへleft/right cloneを対象lengthで作成
3. right側のsource再生位置をsplit時点へ進める
4. 両cloneをinspectionし、source範囲と境界値を検証
5. 元objectを削除
6. left/rightを元layerの非重複範囲へmove

最初のstable対象は次を満たす基本media clipに限定する。

- main effectが`動画ファイル`または`音声ファイル`
- split frameがobject内部
- 再生位置と再生速度が固定値
- sectionなし
- 時間依存track/effectをpreflightで安全と判定出来る
- 同一object内の`音声付き`、または音声なし

animated track、時間制御、script、既存section、別objectの連携audio等を正確に
rebase出来ない場合は`SPLIT_UNSAFE`で編集前に拒否する。対応範囲を推測で広げない。

#### length replacement

オブジェクト長を直接設定するSDK APIは存在しない。trim/lengthは次の置換方式を
隔離した実機spikeで検証する。

1. 元objectのAliasを取得
2. 空き一時layerへ指定lengthでclone
3. cloneのeffect/item/長さをinspection
4. 元objectを削除
5. cloneを元のlayer/frameへ移動

同一編集sectionで「作成したobjectのmove」が保証されるか、失敗時にUndoで完全復旧
出来るかを実機確認する。どちらかを満たさない場合、v1.0でtrim/lengthを安全な操作
として公開せず、`object.duplicate(length=...)`までに限定する。

### 7. 複数clientと公平な実行

現在は1本のPipe接続を直列処理し、notificationは空である。複数のAIツールが
同時に接続すると、先に接続を保持したclientが他を待たせる。

0.8.0ではPipeServerを次の構造へ変更する。

- acceptor 1本 + bounded client worker（既定8接続）
- 接続ごとの`connection_id`
- 全SDK read/edit要求をbounded FIFO schedulerで公平に直列化
- `system.ping`、chunk read/release、event waitはSDK scheduler外で処理
- clientごとに同時要求1件、全体pending 64件を上限
- Disable/終了時は全pipe I/Oをcancelし、waiter/workerをjoin
- captureはconnection ownerを記録し、他clientからのread/releaseを拒否
- client切断時に所有captureを即時解放

mutationは引き続きrevisionで楽観的排他制御する。同じrevisionから競合した
mutationは最初の1件だけ成功し、それ以外は`STALE_PROJECT_STATE`になる。

session公開候補:

- `session.open(client_name, client_version, client_instance_id)`
- `session.get`
- `session.list_clients`

session identityは認証ではなく、監査・公平制御・idempotencyの名前空間である。
Named Pipeの同一ユーザー境界は変わらない。

#### idempotency

作成・mutation要求は`operation_id`を任意指定出来るようにする。

- `(client_instance_id, operation_id)`とrequest digestをLRU/TTL保存
- 同じkey/digestは以前のresultを返す
- 同じkeyで異なるdigestは`IDEMPOTENCY_CONFLICT`
- 既定上限1024件、TTL 10分

これによりresponse受信前の切断後に再接続しても、同じobjectを重複作成しない。

### 8. 変更eventとUI連携

SDKイベント:

- `UPDATE_OBJECT`
- `CHANGE_EDIT_FRAME`
- `CHANGE_EDIT_SCENE`
- `CHANGE_FOCUS_OBJECT`

公開候補:

- `events.wait(after_sequence, timeout_ms)`
- `timeline.set_cursor`
- `object.focus`

0.8.1ではplugin登録時にevent listenerを一度だけ登録する。SDKにはlistener解除APIが
ないため、listenerの保存先はEnable/DisableされるBridgeStateではなくplugin lifetime
のEventHubとする。

event callbackはイベント用threadから呼ばれるため、SDKを呼ばず次だけを行う。

- 64-bit sequenceを増加
- typeと時刻を固定長ring bufferへ記録
- condition variableを通知

`events.wait`は最大30秒、1回最大256件を返す。ring buffer overflow時は
`resync_required=true`を返し、clientはsnapshotを再取得する。
`CHANGE_EDIT_FRAME`は高頻度なので短時間内の同種eventをcoalesceする。

Pythonには同期`wait_events()`と再接続・overflow処理を含む`watch_events()`を追加する。
long-poll専用接続を使い、通常の編集clientを待たせない。

`timeline.set_cursor`と`object.focus`はユーザーのGUIへ干渉するため、設定windowに
「外部UI操作を許可」を追加し既定OFFとする。content edit permissionとは分離する。

### 9. process単位のアクセスモード

現在のEnable/Disableに加え、0.8.1までにprocess単位のscopeを導入する。

- Off（既定）
- Read / Inspect / Render
- Edit
- UI Control（独立、既定OFF）

これはclient認証ではなく、そのAviUtl2 processで外部APIに許す最大権限である。
読み取り専用のVision確認でmutationを受け付けない運用を可能にする。

### 10. 配布形態とバージョン整合

0.7.1で次のバージョンを同期した。

- `pyproject.toml`: 0.7.1
- `aviutl2_api.__version__`: 0.7.1
- CLI: 0.7.1
- Live Bridge plugin: 0.7.1

v1.0ではPython package、CLI、同梱plugin、protocol互換表を一つのrelease
manifestで管理する。wheelへのWindows x64 plugin同梱または署名済みrelease
asset、ハッシュ検証付きinstaller、`py.typed`、更新/rollback手順を用意する。

## フェーズ別受け入れ基準

### 0.6.1 release baseline

- Live Bridge関連sourceをGit管理し、clean checkoutから同一SHAのaux2を生成
- Python/package/CLI/pluginを一つのversion manifestから生成、または別version方針を明記
- README、protocol、security、development、roadmapの現状を同期
- Windows native build/CTest、Python 3.10/3.11/3.12 pytestをCI化
- Live Bridge対象ruff/mypy strictをCI gate化
- 0.6.0でsecurity probe、Enable/Disable、起動終了反復、render中終了を再試験
- plugin、LICENSE、SDK LICENSE、SHA-256、install/update/rollbackをrelease archive化

### 0.7.0 safe typed editing

- catalogで列挙したeffect/itemだけをstrict creationへ渡せる
- 意図的な未知itemを作成前に`UNKNOWN_EFFECT_ITEM`で拒否
- inspection verificationで適用値と期待値の差を検出
- effect add/delete/enableがGUI表示と一致し、それぞれ1 Undo単位
- locked object/layer/effectへの全新規mutationがfail-closed
- layer snapshotがGUIの名前、表示、lockと一致
- duplicateがtext/media/animation/effectを保持し、衝突時は元objectを変更しない
- Pythonにtyped resultと高水準helperを追加

### 0.7.1 playback/split/section/length SDK gate

- section create/move/deleteを境界フレーム、複数section、lock状態で試験
- `rate=0.5/1.0/2.0`を設定し、native renderと音声同期を実機確認
- `keep_timeline`でobject長が不変であることを確認
- `preserve_source_range`でsource終端と新しいtimeline長を確認
- 基本動画/音声clipを先頭・中央・終端付近でsplit
- right clipの再生位置とsplit前後のnative frame連続性を確認
- embedded audio付き動画のsplit前後でA/V同期を確認
- animated track、script、section、別object連携audioをpreflight拒否
- 一時layer方式で作成済みobjectのmove可否を実AviUtl2で確認
- trim成功時に元objectと置換objectのAlias/effect/itemを比較
- 各失敗点を注入し、GUI Undoで完全復旧するか確認
- 完全復旧を保証出来なければsplit/trim/lengthをstable APIから除外

### 0.8.0 multi-client

- 8 idle接続中も9番目を明示的な`SERVER_BUSY`で拒否
- 4 clientから各100回ping/snapshotして欠落・混線・starvationなし
- 同一revisionへの競合mutationは1件だけ成功し、残りはstale
- slow/切断clientが他clientを停止させない
- 同じidempotency keyの再送でobjectが重複しない
- 異なるpayloadで同じkeyを使うと`IDEMPOTENCY_CONFLICT`
- captureを別connectionから読めず、切断後にメモリが解放される
- long render中もping/chunk処理と別client接続が継続
- client接続・SDK待機中のDisable/終了が5秒以内に完了

### 0.8.1 events/access modes

- 4種のSDK eventが単調増加sequenceで通知される
- event callback内でSDK call/editを行わないことを構造・試験で保証
- overflow時に`resync_required`を返し、Python watcherがsnapshot再同期
- cursor連続移動でevent数が上限内にcoalesceされる
- long-poll中の切断、Disable、scene変更でwaiterが安全に復帰
- Read modeで全mutationが`PERMISSION_DENIED`
- UI Control OFFでcursor/focus操作が`PERMISSION_DENIED`
- process再起動後はOffへ戻る

### 0.9.0 distribution/compatibility

- clean Windows環境でarchiveまたはinstallerから導入可能
- 旧版aux2を置換し、rollback手順で前版へ戻せる
- plugin/Python/protocol互換表とmachine-readable release manifestを公開
- wheelに`py.typed`を含め、Python 3.10-3.12で型付きimportを確認
- 最低対応AviUtl2版と最新確認版で実機suiteを実行
- Unicode、UNC、長いpath、静止画、動画、音声mediaを回帰試験
- バイナリのSHA-256を公開し、可能ならAuthenticode署名

### 1.0.0 end-to-end gate

- client Aがevent監視、client Bが同じprocessを編集して相互に停止しない
- GUI編集をeventで検知し、fresh snapshotから安全に再計画出来る
- inspect→verified create/duplicate→typed edit→native render→確認を完走
- stale、lock、partial apply、切断再送、render timeoutから文書通り復旧
- Off/Read/Edit/UI Controlの全modeをユーザーがGUIで識別出来る
- 既知制限、非原子的操作、GUI Undo/保存が必要な範囲をrelease noteに明記

## 依存関係

```text
0.6.1 baseline
  └─ 0.7.0 semantic safety / typed editing
       ├─ 0.7.1 section/trim SDK gate
       └─ 0.8.0 multi-client / idempotency
            └─ 0.8.1 events / access modes
                 └─ 0.9.0 distribution / compatibility
                      └─ 1.0.0
```

意味検証を複数client対応より先に行う。意味の曖昧なmutationを並行化すると、
誤編集と再送時の重複を増幅するためである。event long-pollは現行single-client
Pipeを占有するため、multi-client server完成後に追加する。

## 0.7.1検証済み基準

- native effect catalog、layer snapshot、effect追加・削除、verified duplicate。
- timeline長を維持したplayback rate変更。
- 固定source positionの基本video/audio clipを一つのUndo単位で分割。
- 分割境界のnative frame renderと、失敗後に元clipを保持するfail-closed動作。
- animated source positionとmulti-section clipは安全性を保証できないため拒否。
- effect reorderと直接duration変更はSDKに公開setterがないため未提供。

素材準備から完成書き出しまでの工程別の不足は
[`LIVE_BRIDGE_AGENT_WORKFLOW_GAPS.md`](LIVE_BRIDGE_AGENT_WORKFLOW_GAPS.md)を参照する。

## v1.1以降の候補

- scene単位のAPI編集ロックと新規作成拒否
- filmstrip、音声render、renderキャッシュ
- client単位の認証tokenとread/write/render scope
- 永続的な操作監査ログ
- Unicode/UNC/長いパスを含むメディア回帰試験

## SDK上の制限

現在の公開SDKには、ホストのUndo/Redoや通常のプロジェクト保存を外部から実行する
APIが見当たらない。非公開ウィンドウメッセージへ依存せず、これらは人間のGUI操作
として残す。scene設定変更はSDK自身がUndo非対応と明記しているため、Live Bridgeで
公開する場合は別途確認を要求する。

## 推奨するリリース判断

現行0.7.1は本体レンダー、catalog/layer、effect操作、再生速度、基本media splitまで含むbeta。
実機media splitは確認済みであり、次は配布baseline、意味検証、複数client、
event監視を順に追加する。v1.0は少なくとも次の
編集ループを満たしてからとする。

1. inspect
2. media/text/object create
3. typed property/animation edit
4. native frame render
5. Vision/人間による確認
6. GUI Undoによる復旧
7. 複数client競合時のstale/idempotency保証
8. GUI変更eventからのsnapshot再同期

この一巡が揃うと、外部生成だけでなく「ユーザーと同じAviUtl2セッションを観察し、
編集し、本体出力で検証する」APIとしてv1.0を説明できる。
