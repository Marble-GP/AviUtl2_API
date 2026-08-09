# AviUtl2 Live Bridge エージェント向け完全APIマニュアル

> Current target: Python package / plugin 0.9.6, additive protocol v1.

## 0.9.6: safe local files and explicit synchronization

0.9.6 adds two opt-in layers without changing `LiveClient` or `LiveProject`:

- `LocalProject` is a stateful in-memory `.aup2` working copy with guarded
  checkpoint/save operations.
- `SyncSession` applies one new `EditPlan` to a verified Local/Live pair only
  when the caller invokes `sync.apply(plan)`.
- There is no background watcher, automatic push/pull/merge, or implicit save.
- AviUtl2 Open/Save execution remains unsupported. `project_loaded` and
  `project_saving` are observations; the latter occurs before save and does not
  prove success.

対象バージョン: plugin / Python client 0.9.6

Wire protocol: v1 additive

対象OS: Windows

最終更新: 2026-08-02

## 0.9.5の主な変更

- 通常のプログラム編集入口を低水準`LiveClient`から高水準`LiveProject`へ移した。
- 複数操作を事前検証して原則一つのGUI Undo単位で適用する`EditPlan`を追加した。
- text、shape、image、video、audio、transform、検索、カット、native reviewを
  英語名と自然単位で操作できる。
- 主要20 Effectを`effect("glow", ...)`形式でLiveと`.aup2`生成の双方へ適用できる。
- native mediaが映像・音声別objectでも、一つのcombined MP4 objectでも正しく
  transformと映像・音声Effectを振り分ける。
- `LiveClient`と既存protocol v1 endpointは削除せず、raw操作用に維持する。

LLMへ渡す最小仕様は`AGENT_API_CARD.md`、移行の最短例は
`LIVE_BRIDGE_AGENT_QUICK_START.md`、配布時の変更点と既知制約はrelease noteを参照する。

この文書は、Codex、Claude Code、Copilot CLI、Agent ZeroなどのAIエージェントが、
ユーザーの開いているAviUtl2プロジェクトをLive Bridge経由で安全に編集するための
規範的な利用マニュアルである。

通常の編集はPythonの`aviutl2_api.live.LiveProject`を使用する。完全Alias、raw
item、manual revisionまたは未ラップendpointが必要な場合は`LiveProject.client`の
`LiveClient`へ降りる。Named Pipeを直接実装する場合だけ「低水準Wire API」を
参照すること。

## 1. エージェントが必ず守る規則

1. AviUtl2の外部API連携が有効なプロセスを列挙し、複数あれば必ずPIDをユーザーに
   対応付ける。対象プロセスを推測しない。
2. 接続直後に`system.get_capabilities`を取得し、利用するメソッドが`methods`に
   含まれることを確認する。未対応機能を別手段で成功したように見せない。
3. 編集前にfresh snapshotを取得し、その`revision`と`SnapshotObject`だけを使う。
4. mutationが一つ成功するたびにrevisionが変わる。古い`SnapshotObject`を次の
   mutationへ再利用せず、receiptまたはfresh snapshotで参照を更新する。
5. `api_locked=True`のobject、`locked=True`のlayer/effectは変更しない。APIから
   ロックを解除しようとしない。
6. effect名、item名、selector、font、moduleを推測しない。実機のcatalogと
   `object.inspect`で存在と型を確認する。
7. 動画と同時生成された音声、字幕などを一緒に扱う場合は`ObjectGroup`で対象を
   明示する。暗黙の関連付けを推測しない。
8. 可変速、reverse、animated source position、意味を保持できない第三者effectの
   構造編集が`STRUCTURAL_EDIT_UNSAFE`で拒否されたら迂回しない。
9. edit後はfresh snapshotを取得し、AviUtl2 native frame/audio renderで検証する。
10. project open/save/save-as、encoder/export/upload、playback、scene deleteは
    Live Bridgeの対象外である。GUI自動操作や非公開window messageで補わない。
11. frame/contact sheet/PCMは既定でmemoryへ取得する。ファイルへ書く場合は出力先を
    ユーザーの指示範囲に限定し、既存ファイルを上書きするときだけ
    `overwrite=True`を明示する。
12. 現在の公式SDKにはscene CRUD/switchとUndo/Redo実行APIがない。capabilityが
    falseの間は`history.undo/redo`を呼ばない。

## 2. 対応範囲

### 2.1 対応している作業

- 接続対象AviUtl2プロセスの発見とPID指定
- 最大8クライアントの同時接続
- session単位のmutation冪等性
- AviUtl2 SDK eventのlong-poll監視
- scene、layer、timeline objectの取得
- 画像、動画、音声、Alias object、text、字幕の配置
- object名、text、座標、typed/raw item値の変更
- effectの追加、有効化、無効化、削除、並べ替え
- object sectionの一覧、作成、削除、移動
- objectの移動、削除、複製、分割、duration変更、trim
- 複数object transaction、group移動・削除
- shift、ripple insert/delete、gap close
- media inventory、missing media検査、relink
- native PNG frame render、複数frame、contact sheet
- native stereo float PCM render
- peak、RMS、clipping、silence、EBU R128方式のintegrated loudness解析
- missing media/font/effect、lock、collision、gap、字幕、音声preflight

### 2.2 対応していない作業

- project open/save/save-as
- encoder、出力プラグイン、動画ファイル出力、投稿
- preview playbackの再生・停止
- scene delete
- 音声認識、翻訳、素材生成
- APIからのobject/layer/effect lock解除
- 公式SDKにないscene list/create/duplicate/switch
- 公式SDKにないUndo/Redo実行
- section単位のtyped track/check setter
- 可変速、reverse、animated source positionを含む安全でない構造編集

## 3. 準備と接続

### 3.1 Python package

リポジトリから使用する場合:

```powershell
cd C:\Users\s.watanabe\Documents\Dev\AviUtl2-SDK-BRIDGE\AviUtl2_API
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Python APIは`aviutl2_api.live`からimportする。

### 3.2 AviUtl2側

1. `AviUtl2LiveBridge.aux2`をAviUtl2のPluginディレクトリへ配置する。
2. AviUtl2を起動し、対象プロジェクトをユーザーが開く。
3. `設定 > 外部API連携設定...`を開く。
4. `このウィンドウの外部API連携を許可`をONにする。
5. 表示されたPIDを確認する。

外部API連携はプロセスごとで、起動時の既定値はOFFである。設定は永続化されない。
OFFにするとNamed Pipeとinstance discovery entryが停止・削除される。

### 3.3 instance discovery

```python
from aviutl2_api.live import discover_instances

for instance in discover_instances():
    print(
        instance.pid,
        instance.plugin_version,
        instance.protocol_version,
        instance.scene_id,
        instance.project_path,
    )
```

`InstanceInfo`のfield:

| field | 意味 |
|---|---|
| `pid` | AviUtl2プロセスID |
| `pipe` | `\\.\pipe\AviUtl2.LiveBridge.<PID>` |
| `protocol_version` | Wire protocol version |
| `plugin_version` | plugin version |
| `sdk_baseline` | build時のSDK baseline |
| `project_path` | SDKから取得可能な場合のproject path。`None`もある |
| `scene_id` | discovery公開時のscene ID |
| `started_at` | instance開始時刻 |

接続:

```python
from aviutl2_api.live import LiveClient

with LiveClient.connect(pid=32652) as client:
    print(client.hello())
```

有効なinstanceが一つだけなら`LiveClient.connect()`でもよい。複数ある場合は
`AmbiguousInstanceError`になるため、`pid=`を指定する。

Pipe名を明示する場合:

```python
client = LiveClient.connect(
    pipe_name=r"\\.\pipe\AviUtl2.LiveBridge.32652",
    timeout=5.0,
)
```

### 3.4 最小の安全確認

```python
from aviutl2_api.live import LiveClient

with LiveClient.connect(pid=32652) as client:
    hello = client.hello()
    capabilities = client.get_capabilities()
    snapshot = client.get_snapshot(include_alias=False)

    assert hello["protocol_version"] == 1
    assert "project.get_snapshot" in capabilities["methods"]
    print(snapshot.revision, snapshot.scene_id, len(snapshot.objects))
```

## 4. 重要なデータモデル

### 4.1 revisionとobject ID

`ProjectSnapshot`は現在sceneのimmutable viewである。

```python
snapshot = client.get_snapshot(include_alias=False)
print(snapshot.revision, snapshot.scene_id)
for obj in snapshot.objects:
    print(
        obj.object_id,
        obj.layer,
        obj.frame_start,
        obj.frame_end,
        obj.duration_frames,
        obj.name,
        obj.api_locked,
    )
```

`SnapshotObject.object_id`は`obj-<revision>-<index>`形式の一時参照である。
永続IDではない。mutationまたはGUI編集でrevisionが変わると失効する。

対象objectを指定するWire paramsは常に次の形になる。

```json
{
  "expected_revision": 123456,
  "target": {
    "object_id": "obj-123456-4"
  }
}
```

`SnapshotObject.target_params()`がこのdictを返す。

### 4.2 snapshot paging/filter

```python
page = client.get_snapshot(
    offset=0,
    count=256,
    layer_start=0,
    layer_end=15,
    frame_start=0,
    frame_end=899,
    object_ids=None,
    has_alias=None,
    include_alias=False,
)
print(page.offset, page.total, page.next_offset)
```

- frame filterは指定区間とobject区間が交差するobjectを返す。
- `include_alias=False`はpayloadを減らす。複製・構造処理にAliasが必要な場合だけ
  `True`にする。
- `has_alias=True/False`はAliasの存在でfilterする。
- `object_ids`は対象ID集合だけを返す。
- `next_offset`が`None`になるまでpagingする。

### 4.3 mutation receipt

一般的な成功result:

```json
{
  "applied_count": 1,
  "revision": 123457,
  "snapshot_required": false,
  "updated_object": {
    "object_id": "obj-123457-4",
    "revision": 123457
  },
  "undo_unit": "single_edit_section",
  "undo_grouped": true,
  "warnings": []
}
```

- `revision`: 操作後のrevision。SDK制約で不明な場合は`null`。
- `snapshot_required`: fresh snapshotが必要か。
- `updated_object`: 一意に識別できた場合だけ返る。`null`ならsnapshotを再取得する。
- `undo_unit`: AviUtl2の編集section単位。
- `undo_grouped`: 複数変更が一つのGUI Undo単位か。
- `warnings`: Alias fallback、snapshot再取得要求など。

安全のため、連続編集では原則として各mutation後にfresh snapshotを取得する。

### 4.4 sessionとoperation ID

`LiveClient.connect()`は0.9.5 pluginに対して自動的に`session.open`を呼ぶ。

```python
with LiveClient.connect(pid=32652) as client:
    print(client.session)
```

同一sessionで同じ`operation_id`と同じmethod/payloadを再送すると、最初のresultが
再利用される。異なるpayloadで同じIDを使うと`OPERATION_ID_REUSED`になる。

```python
result = client.call(
    "object.set_name",
    {
        **obj.target_params(),
        "name": "確定タイトル",
    },
    operation_id="rename-title-0001",
)
```

`LiveClient`は通常のmutationへ自動IDを付ける。通信timeout後に同一操作を明示的に
再送したい場合は、最初からcaller管理の`operation_id`を使う。

### 4.5 API lock

`SnapshotObject.api_locked=True`のobjectはLive Bridge mutationを拒否する。
典型errorは`OBJECT_API_LOCKED`である。

layer/effectのlockもそれぞれ`LAYER_LOCKED`、`EFFECT_LOCKED`で拒否される。
ロック解除APIはない。ユーザーがAviUtl2 GUIから解除する。

API lockは既存object自体を守る機能であり、別layerへの新規object作成まで禁止する
scene-wide lockではない。エージェントはロック対象を映像上で覆う編集も避ける。

## 5. 推奨高水準workflow

### 5.0 backendを選ぶ

標準importはパッケージルートへ統一されている。

```python
from aviutl2_api import (
    EditPlan,
    LiveProject,
    LocalProject,
    SyncSession,
    effect,
    linear,
    native_effect,
)
```

- `.aup2`をmemory上で安全に編集して別checkpointを作る: `LocalProject`
- ユーザーが開いているAviUtl2だけを編集・native reviewする: `LiveProject`
- 新しい同一`EditPlan`をLocal memoryとLiveへ明示適用する: `SyncSession`

いずれも`.aup2`を暗黙に保存しない。LocalにもLiveと同じ
`add_text/image/video/audio/media/shape()`、`update()`、`move()`、`delete()`、
Effect即時methodがあり、内部では1 commandの`EditPlan`へ正規化される。

```python
local = LocalProject.load("project.aup2")
title = local.add_text("第一章", duration=90, y=-200)
title = local.update(title.primary, x=120)
local.checkpoint()
```

### 5.1 `LiveProject`: 標準入口

```python
from aviutl2_api import LiveProject

with LiveProject.connect(pid=32652) as project:
    title = project.add_text(
        "第一章",
        duration=90,
        y=-200,
        size=80,
    )
    title = project.update(
        title.primary,
        x=120,
        scale=110,
        opacity=0.9,
    )
    rendered = project.render(title.primary.midpoint)
```

`LiveProject`が内部管理するもの:

- compact snapshotとfresh revision
- connection sessionのoperation ID
- GUI cursor frame、timeline末尾、serial/parallel配置
- Layer 0からの未ロック・非衝突layer探索
- plan内で解決済みの仮想配置、move、delete
- native media probeとscene FPSによる既定duration
- 共通transformから実機の`標準描画`またはnative videoの`映像再生`itemへの変換
- effect/font catalog、既存effect selector/itemのinspection

即時mutationは内部で1 commandの`EditPlan`を作り、更新後の`ObjectGroup`または
`ObjectSelection`を返す。返された`LiveObject`はそのrevisionだけで有効である。

### 5.2 `EditPlan`: 複数操作

```python
from aviutl2_api import EditPlan, LiveProject, PlanValidationError

plan = EditPlan(sequence="parallel")
plan.add_video("intro.mp4", key="intro")
plan.add_shape(
    "rectangle",
    key="panel",
    duration=90,
    width=900,
    height=160,
    y=-200,
)
plan.add_text(
    "第一章",
    key="title",
    duration=90,
    y=-200,
    size=80,
)

with LiveProject.connect(pid=32652) as project:
    validation = project.validate(plan)
    if not validation.valid:
        raise PlanValidationError(validation)
    result = project.apply(plan)
    title = result.objects["title"].primary
```

`sequence="parallel"`は`at`省略objectを同じcursor frameへ置く。
`sequence="serial"`は省略objectを追加順に直列配置する。`at="end"`は明示的な
timeline末尾である。Wireへ送る時点では全frame/layerが解決済みであり、Native側は
自動配置しない。

成功したplanはsingle-useで、再適用はPython側で拒否される。通信再送は同一session
のoperation ID冪等性で処理される。成功時は原則1回のAviUtl2 GUI Undo単位だが、
SDKに完全transaction rollbackがないため`PlanResult.atomic`は常に`False`である。

`PlanApplyError.result.rollback`:

- `attempted`: 自動復元を試したか
- `complete`: Bridgeが把握する作成物・退避objectを復元できたか
- `restored_count`: 復元できた要素数
- `gui_undo_required`: ユーザーのGUI Undoが必要か
- `warnings`: rollback固有の警告

部分適用は成功として返さない。`gui_undo_required=True`なら編集を続行せず、対象PIDと
失敗内容をユーザーへ示してAviUtl2 GUI Undoを依頼する。

### 5.3 自動配置とtransform

| 引数 | 意味 |
|---|---|
| `at=None` | GUI cursor frame |
| `at="end"` | 現在またはplan仮想配置を含むtimeline末尾 |
| `layer=None` | Layer 0から下方向へ探索 |
| `duration=None` | text/image/shapeは60 frame、video/audioはnative duration |
| `x/y/z` | pixel |
| `scale` | 100が等倍のpercent |
| `rotation` | Z軸degree。3Dは`rotation_x/y/z` |
| `opacity` | 0.0..1.0。AviUtl2「透明度」へ反転変換 |
| `color` | `#RRGGBB`または`RRGGBB` |

text/shape/image/videoの`x/y/z`, `scale`, `rotation(_x/_y/_z)`, `opacity`は
固定値の代わりに`linear(start, end)`を受け取る。AviUtl2の「直線移動」へ
変換されるため、
日本語item名、`AnimatedValue`、Aliasを直接扱う必要はない。`scale`の両端は正、
`opacity`の両端は`0.0..1.0`でなければならない。

image/videoは`fit="contain" | "cover"`に対応する。audio methodは視覚transformを
公開せず、汎用`add_media(kind="audio", ...)`へ視覚引数を渡すと
`INVALID_MEDIA_ARGUMENTS`で実行前に拒否する。

video/audioでnative durationを取得できない場合、`duration`を明示しない限り拒否する。
相対media pathはPython processのcurrent working directoryを基準にabsolute pathへ
解決される。

### 5.4 `find`とrevision-scoped参照

```python
with LiveProject.connect(pid=32652) as project:
    title = project.find(text="第一章").one()
    title = project.update(title, text="第1章", x=100).primary
```

`find()` / `find_objects()`では`name`, `name_contains`, `text`,
`text_contains`, `file`, `file_contains`, `effect`, `layer`, `at`,
`overlap=(start, end)`, `api_locked`を利用できる。
通常は`include_alias=False`のsnapshotだけを使い、text/effectは候補objectだけを
inspection、fileはmedia inventoryで遅延調査する。`one()`は0件と複数件を明示的に
拒否する。mutation後の古い参照を暗黙に追跡・推測しない。

### 5.5 高度なworkflow

`LiveProject.preflight()`、`review()`、`contact_sheet()`、`audio_review()`が
native映像・音声検証をまとめる。split/trim/duration/reorder/section/rippleなど
統一plan外の構造操作は、既存endpointを呼ぶ高水準即時methodまたは
`project.client`を使う。

`EditingSession`と`LiveClient`は後方互換のadvanced APIとして維持される。
`EditingSession.undo()`と`redo()`は公式SDK capabilityが追加されるまで
`CapabilityUnavailableError`を送出する。`UndoReceipt.grouped=True`はGUIの
Undo単位が一つであることを示すが、Bridgeから実行可能という意味ではない。

## 6. Python APIリファレンス

以下では`timeout`引数の説明を省略する。指定しなければ接続時の
`default_timeout`が使用される。

### 6.0 `LiveProject`と共通editing model

Import:

```python
from aviutl2_api.editing import (
    AppliedEffect,
    EditPlan,
    EffectSpec,
    LinearMotion,
    PlanApplyError,
    PlanResult,
    PlanValidationError,
    ProjectChangedError,
    Transform,
    effect,
    linear,
    native_effect,
)
from aviutl2_api.live import LiveObject, LiveProject, ObjectSelection
```

Connection/state:

```text
LiveProject.connect(pid=None, pipe_name=None, timeout=5.0) -> LiveProject
project.get_snapshot(include_alias=False) -> ProjectSnapshot
project.refresh() -> ObjectSelection
project.snapshot -> ProjectSnapshot | None  # cached property, not callable
project.objects -> ObjectSelection          # cached; refreshes only if empty
project.summary(refresh=True) -> dict[str, object]
project.preflight(**options) -> PreflightReport
project.find/find_objects(name=None, name_contains=None,
             text=None, text_contains=None,
             file=None, file_contains=None, effect=None,
             layer=None, at=None, overlap=None,
             api_locked=None) -> ObjectSelection
project.describe_schema(subject) -> dict[str, object]
project.describe_effect(profile) -> dict[str, object]
project.describe_property(name) -> dict[str, object]
project.describe_api(operation) -> dict[str, object]
project.capabilities -> Mapping[str, object]
project.client -> LiveClient
```

Creation:

```text
project.add_text(text, **placement_transform_style) -> ObjectGroup
project.add_image(file, **placement_transform) -> ObjectGroup
project.add_video(file, **placement_transform) -> ObjectGroup
project.add_audio(file, **placement_effects) -> ObjectGroup
project.add_media(file, kind="auto", **placement_transform) -> ObjectGroup
project.add_shape(shape, **placement_transform_style) -> ObjectGroup
```

`shape`は`circle`, `rectangle`, `triangle`, `pentagon`, `hexagon`, `star`,
`heart`, `background`。
作成系共通引数は`at`, `layer`, `duration`。映像系はさらに`x/y/z`, `scale`,
`rotation`, `rotation_x/y/z`, `opacity`を受け取る。textは`size`, `color`,
`font`、shapeは`width`, `height`, `color`も受け取る。作成時のvisual transformは
固定値または`LinearMotion`を受け取る。`add_audio()`はvisual transformを公開せず、
汎用`add_media(kind="audio")`への指定も実行前に構造化エラーで拒否する。

image/mediaは`fit="contain" | "cover"`と
`apply_exif_orientation=True | False`も受け取る。`fit`はnative probeのsource寸法と
current scene解像度からscaleを計算し、明示`scale`との併用は拒否する。
EXIF 3/6/8はZ回転へ合成し、mirrorを伴う2/4/5/7は画素の事前正規化を要求する。

全creation methodは`effects: Sequence[EffectSpec | NativeEffectSpec]`も
受け取る。指定順、同名Effectの重複、`enabled`を保持し、作成と初期item設定を
一つのGUI Undo単位で行う。成功時は`PlanResult.effects[key]`にfreshな
`AppliedEffect` receiptが入る。

動画はnative probeで可読性、長さ、寸法、音声track数を検証する。音声trackがある
動画は、SDK media loaderが音声を既定で無効化する実機仕様を避けるため、検証済みの
Alias fallbackで`音声付き`を明示有効化したcombined objectとして作成する。
`scope="video"` / `scope="audio"`は同じobjectへ安全にroutingされ、作成後Alias、
Effect順、native PNG/PCMで検証できる。音声trackのない動画と画像・音声単体は
native media loaderを使用する。

```text
linear(start: float, end: float) -> LinearMotion
```

text/shape作成のtransformへ渡すと、指定値をobjectの全durationにわたって
AviUtl2 native「直線移動」で補間する。

Object/timeline:

```text
project.update(target, text=None, name=None, **transform) -> ObjectGroup
project.move(target, at: int, layer: int) -> ObjectGroup
project.delete(target) -> PlanResult
project.split(target, frame: int) -> MediaSplit
project.trim(target, frame_start, frame_end,
             source_position=None) -> ObjectSelection
project.set_duration(target, duration) -> ObjectSelection
```

Effect:

```text
effect(profile, *, enabled=True, **parameters) -> EffectSpec
native_effect(name, values, *, enabled=True, scope="primary") -> NativeEffectSpec
project.available_effect_profiles() -> tuple[str, ...]
project.describe_effect(profile) -> dict[str, object]
project.apply_effect(target, spec) -> AppliedEffect
project.update_effect(target, applied_effect, spec) -> AppliedEffect
project.add_effect(target, effect, values=None) -> ObjectGroup
project.set_effect_values(target, effect, values) -> ObjectSelection
project.set_effect_enabled(target, effect, enabled) -> ObjectGroup
project.delete_effect(target, effect) -> ObjectSelection
project.reorder_effects(target, effects) -> ObjectSelection
project.apply_common_effect(target, semantic, values,
                            effect_name=None) -> (EffectApplication, ObjectSelection)
```

`effect`にはeffect名または正確なselectorを指定する。effect名が同じものが1個なら
selectorを内部解決し、複数なら明示selectorを要求する。`add_effect`はeffect catalog、
`set_effect_values`はobject inspectionで名前・item・lockを確認する。
主要20 profileは`color_adjustment`, `monochrome`, `gradient`, `crop`,
`mask`, `resize`, `mosaic`, `blur`, `directional_blur`, `motion_blur`,
`glow`, `emission`, `outline`, `drop_shadow`, `chroma_key`,
`luminance_key`, `fade`, `wipe`, `audio_gain`, `audio_fade`。
pixel、degree、seconds、0.0..1.0 opacity、`#RRGGBB`、boolを使用し、
動くnumberには`linear(start, end)`を渡す。第三者effectの意味や単位は推測しない。raw値が必要なら
`project.client.set_item(s)`を使用する。

`.aup2` model backendでも同じspecを使う:

```python
from aviutl2_api import (
    apply_effects,
    compare_aup2_roundtrip,
    validate_standard_effects,
)
from aviutl2_api.editing import effect

apply_effects(
    project_model,
    timeline_object,
    effect("glow", strength=50, color="#FFD966"),
    effect("outline", size_px=4),
)
validation = validate_standard_effects(project_model)
```

`apply_effects()`はmemory上のmodelだけを変更し、saveは行わない。manifest ID
`2001901`の完全templateを、AviUtl2のOpen/Save標準に合わせて
`標準描画` / `音声再生`より後へ挿入し、Effect IDを順序どおりに振り直す。
明示的な互換project versionは、生成時の`2001901`と、AviUtl2がOpen/Save時に
更新する`2010200`である。未知の将来versionは推測せず拒否する。無効な標準Effectは
AviUtl2標準の`effect.disable=1`として生成・検証する。
`validate_standard_effects()`は未知item、必須item欠落、
enum、domain/order違反をerror、第三者Effectを`unverified`として返す。
Open/Save後は`compare_aup2_roundtrip(before, after)`で意味比較できる。ID再採番、
数値表記、property順、既知default、`Group2/Group3`は正規化し、Effect消失・置換・
並べ替え・明示値変更は失敗にする。

Review/plan:

```text
project.render(frame_or_object=None, output_path=None,
               overwrite=False, timeout=30.0) -> RenderedFrame
project.render_preview(frame_or_object=None, max_width=480,
               max_height=None, format="jpeg", quality=85,
               output_path=None, overwrite=False) -> RenderedPreview
project.render_previews(frames_or_objects, max_width=480,
               max_height=None, format="jpeg", quality=85) \
               -> tuple[RenderedPreview, ...]
project.contact_sheet(frames=None, columns=4,
                      thumbnail_width=320, output_path=None,
                      overwrite=False) -> ContactSheet
project.render_contact_sheet(...) -> ContactSheet
project.render_audio(frame_start, frame_end) -> RenderedAudio
project.audio_review(frame_start, frame_end) -> (RenderedAudio, AudioAnalysis)
project.review(**options) -> ReviewBundle
project.validate(plan: EditPlan) -> PlanValidation
project.apply(plan: EditPlan) -> PlanResult
```

`EditPlan` command builder:

```text
EditPlan(sequence="parallel" | "serial")
plan.add_text(..., key=None, effects=None)
plan.add_image/video/audio/media(..., key=None, effects=None)
plan.add_shape(..., key=None, effects=None)
plan.update(target, ..., key=None)
plan.move(target, at, layer, key=None)
plan.delete(target, key=None)
plan.add_effect(target, effect, values=None, key=None)
plan.set_effect_enabled(target, selector, enabled, key=None)
```

`plan.commands`はbackend共通の`AddTextInstruction`, `AddMediaInstruction`,
`AddShapeInstruction`, `UpdateObjectInstruction`, `MoveObjectInstruction`,
`DeleteObjectInstruction`, `AddEffectInstruction`,
`SetEffectEnabledInstruction`を保持する。

同一plan内から新規objectを後続commandのtargetにはできない。必要なtransformや
effect初期値をcreate command自身へ含める。`key`はplan内で一意で、成功resultの
`objects[key]`へ対応する。省略時は`command-<index>`になる。

型:

| 型 | 主なfield |
|---|---|
| `LiveObject` | object ID、revision、layer、frame範囲、duration、midpoint、name、lock |
| `ObjectSelection` | iterable、`first()`、厳密な`one()` |
| `PlanValidation` | valid、revision、resolved placements、warnings、errors、structured issues |
| `ValidationIssue` | code、message、machine-readable details |
| `PlacementConflictError` | layer、frame範囲、conflicting object IDs、suggested layer |
| `RenderedPreview` | frame、revision、width/height、mime_type、data、sha256 |
| `PlanResult` | before/after revision、commands、objects、Undo、atomic、rollback、warnings |
| `RollbackReceipt` | attempted、complete、restored_count、gui_undo_required、warnings |

### 6.1 connection、system、event

#### `LiveClient.connect`

```text
LiveClient.connect(
    *,
    pid: int | None = None,
    pipe_name: str | None = None,
    timeout: float = 5.0,
) -> LiveClient
```

#### `client.call`

```text
client.call(
    method: str,
    params: dict | None = None,
    *,
    timeout: float | None = None,
    operation_id: str | None = None,
) -> dict
```

低水準methodを呼ぶ。remote errorは`BridgeRemoteError`として送出される。

#### system

```text
client.hello() -> dict
client.ping() -> bool
client.get_capabilities() -> dict
client.get_project_info() -> dict
client.open_session(client_name="aviutl2-api") -> SessionInfo
```

`get_capabilities()["methods"]`を実行時のauthorityとする。

#### event watch

```text
client.watch_events(
    *,
    after_sequence: int = 0,
    timeout_ms: int = 30_000,
    types: Sequence[str] | None = None,
) -> EventWatchResult
```

event type:

- `object_updated`
- `edit_frame_changed`
- `edit_scene_changed`
- `focus_object_changed`

正しい監視loop:

```python
sequence = 0
while True:
    watched = client.watch_events(
        after_sequence=sequence,
        timeout_ms=30_000,
    )
    sequence = watched.latest_sequence
    if watched.resync_required or watched.events:
        snapshot = client.get_snapshot(include_alias=False)
        # eventは変更通知であり、snapshotそのものではない。
```

`resync_required=True`はring buffer overflowを意味する。必ずfresh snapshotを
取得する。event callback内からSDK APIが呼ばれることはない。

### 6.2 scene

```text
client.get_current_scene() -> SceneInfo
client.update_current_scene(
    *,
    expected_revision: int,
    name: str | None = None,
    width: int | None = None,
    height: int | None = None,
    rate: int | None = None,
    scale: int | None = None,
    sample_rate: int | None = None,
    confirm_non_undoable: bool = False,
) -> SceneInfo
```

`SceneInfo`:

| field | 意味 |
|---|---|
| `scene_id`, `revision`, `name` | current scene識別・名前 |
| `width`, `height` | 解像度 |
| `rate`, `scale` | frame rate = `rate / scale` |
| `sample_rate` | audio sample rate |
| `non_undoable` | この応答が非Undo変更結果か |

scene updateは現SDKでUndoできないため`confirm_non_undoable=True`が必須。
`width/height`、`rate/scale`は必ずpairで指定する。

予約済みPython method:

```text
client.history_undo() -> dict
client.history_redo() -> dict
```

0.9.5では公式SDKに実行APIがなく、呼ぶと`SDK_METHOD_UNAVAILABLE`になる。
`EditingSession.undo()/redo()`は先にcapabilityを検査し、
`CapabilityUnavailableError`を送出する。

### 6.3 catalogとlayer

```text
client.get_effect_catalog(start=0, count=64) -> EffectCatalogPage
client.get_font_catalog(start=0, count=128) -> dict
client.get_palette_catalog(start=0, count=128) -> dict
client.get_module_catalog(start=0, count=128) -> dict
client.get_layers(start=0, count=128) -> LayerPage
client.update_layer(
    *,
    expected_revision: int,
    layer: int,
    name: str | None | omitted = omitted,
    enabled: bool | None = None,
) -> dict
```

`name=None`はlayer名を消去する。nameを変更せずenabledだけ変える場合は
`name`を省略する。layer lockの変更・解除はできない。

effect item type:

| code | type | Python typed value |
|---:|---|---|
| 1 | `integer` | `int` |
| 2 | `number` | `int`, `float`, `StaticValue`, `AnimatedValue` |
| 3 | `check` | `bool` |
| 4 | `text` | `str` |
| 5 | `string` | `str` |
| 6 | `file` | absolute path `str` |
| 7 | `color` | 6桁または8桁hex `str` |
| 8 | `select` | raw値。catalog/inspectionで確認 |
| 9 | `scene` | raw値 |
| 10 | `range` | raw値 |
| 11 | `combo` | raw値 |
| 12 | `mask` | raw値 |
| 13 | `font` | `str` |
| 14 | `figure` | `str` |
| 15 | `data` | raw値 |
| 16 | `folder` | `str` |

schemaが十分でない複雑型は`set_item()`のraw APIを使い、実機inspection後の既知値
だけを設定する。

### 6.4 snapshotとinspection

```text
client.get_snapshot(...) -> ProjectSnapshot
client.inspect_object(
    obj: SnapshotObject,
    *,
    sample_frame: int | None = None,
) -> ObjectInspection
```

inspection hierarchy:

- `ObjectInspection.effects`
- `EffectInspection.index`, `occurrence`, `name`, `selector`
- `EffectInspection.enabled`, `locked`, `items`
- `ItemInspection.name`, `type`, `type_code`, `raw_value`
- `ItemInspection.track`, `sampled_check_value`
- `TrackInspection.mode`, `parameters`, `sampled_value`
- `TrackInspection.accelerate`, `decelerate`, `ignore_midpoints`
- `TrackInspection.time_control`
- `TrackInspection.group_count`, `group_index`, `group_name`, `group_items`

同名effectが複数ある場合は`name`ではなくinspectionが返した`selector`を使う。
`sample_frame`を指定するとtrack/checkのそのframeでのsample値を取得できる。

### 6.5 media

#### probe、配置

```text
client.probe_media(file) -> MediaProbe
client.create_from_media_file(
    file,
    *,
    layer: int,
    frame: int,
    length: int = 0,
) -> CreatedMediaObject

client.add_image(...) -> CreatedMediaObject
client.add_video(...) -> CreatedMediaObject
client.add_audio(...) -> CreatedMediaObject
```

pathはabsolute pathへ正規化される。`length=0`はdurationと必要な配置調整を
AviUtl2へ委ねる。

```python
created = client.add_video(
    r"C:\media\clip.mp4",
    layer=2,
    frame=120,
    length=0,
)
for obj in created.objects:
    print(obj.object_id, obj.layer, obj.frame_start, obj.frame_end)
```

SDKが動画と音声など複数objectを生成した場合、`created.objects`にsnapshot差分の
全objectが入る。一体型MP4などは映像・音声を持つ一つのobjectになる。以後一緒に
扱うなら、要素数に依存せず`ObjectGroup(created.objects)`を作る。

#### inventory、relink

```text
client.get_media_inventory() -> MediaInventory
client.relink_media(
    *,
    expected_revision: int,
    replacements: Mapping[source_path, destination_path],
    operation_id: str | None = None,
) -> MediaRelinkReceipt
```

```python
inventory = client.get_media_inventory()
for item in inventory.files:
    if not item.exists or not item.readable:
        print(item.object_id, item.file, item.probe_error)

receipt = client.relink_media(
    expected_revision=inventory.revision,
    replacements={
        r"C:\old\clip.mp4": r"D:\assets\clip.mp4",
    },
    operation_id="relink-assets-0001",
)
```

`get_media_inventory()`は全file itemをAviUtl2で検査するため、大規模な
編集済みプロジェクトでは時間がかかる。Python clientはこのmethodに限り
既定で120秒待機する。

全destinationをAviUtl2 native probeで確認し、一つのtransaction/Undo単位で
file itemを更新する。

#### 単一file item差し替え

```text
client.set_media_file(
    obj,
    r"D:\assets\replacement.mp4",
    effect=None,
    item=None,
) -> dict
```

strict probe後、対象object内でfile itemが一つに特定できる場合だけ変更する。
複数ある場合はinspectionのselector/item名を`effect=`と`item=`へ指定する。

### 6.6 object item、text、座標、animation

```text
client.set_property(
    obj,
    *,
    effect: str,
    item: str,
    value: str | int | float | bool | StaticValue | AnimatedValue,
) -> dict

client.set_animation(
    obj,
    *,
    effect: str,
    item: str,
    value: AnimatedValue,
) -> dict

client.set_item(...) -> dict
client.set_items(obj, updates: Sequence[ItemUpdate]) -> dict
client.set_text(obj, text: str) -> dict
client.set_position(obj, *, x: float | None, y: float | None) -> dict
client.set_object_name(obj, name: str | None) -> dict
```

`set_property`はinspectionでitem型を確認してから書く推奨APIである。
`set_item`はraw Alias値APIである。

```python
from aviutl2_api.models import AnimatedValue, AnimationParams

snapshot = client.get_snapshot(include_alias=False)
obj = snapshot.objects[0]

client.set_animation(
    obj,
    effect="標準描画",
    item="X",
    value=AnimatedValue(
        start=-600.0,
        end=600.0,
        animation=AnimationParams("直線移動", "0"),
    ),
)
```

一つの`SnapshotObject`で`set_text()`の直後に`set_position()`を続けてはいけない。
間にfresh snapshotを取得するか、`timeline.transaction.apply`へまとめる。

### 6.7 effect

```text
client.add_effect(
    obj,
    effect: str,
    *,
    items: dict[str, str] | None = None,
) -> dict

client.set_effect_enabled(
    obj,
    selector: str,
    enabled: bool,
) -> dict

client.delete_effect(
    obj,
    selector: str,
) -> dict

client.reorder_effects(
    obj,
    selectors: Sequence[str],
) -> dict
```

追加時の`items`はraw値であり、effect追加と同じUndo単位で設定される。
enable/delete/reorderでは`inspect_object()`が返したselectorを使う。
reorderは全effectの完全な順序を渡す。input/outputの必須位置、effect lock、
section、全item値を検証する。安全に置換できなければ拒否する。

高水準common effect helper:

```python
client.apply_common_effect(
    obj,
    "crop",
    {
        "上": 10.0,
        "下": 10.0,
    },
    effect_name="クリッピング",
)
```

semantic:

- `transition`
- `mask`
- `crop`
- `chroma_key`
- `volume`
- `pan`
- `fade`
- `ducking`

helperはlive catalog上でeffect候補が一つに決まり、指定itemがすべて存在する場合だけ
実行する。localized item名やraw値を自動推測しない。

### 6.8 section

```text
client.list_sections(obj) -> ObjectSections
client.create_section(obj, frame: int) -> dict
client.delete_section(obj, section: int) -> dict
client.move_section(obj, section: int, frame: int) -> dict
```

section index 0は先頭で、delete/move対象は正のmiddle boundary indexである。
sectionはobject内部の中間点であり、独立clipへのsplitとは異なる。

track/check値は`inspect_object(sample_frame=...)`で取得する。現SDKにはsection単位の
typed setterがないため、存在しないsetterを呼ばない。

### 6.9 object配置、複製、移動、削除

```text
client.create_from_alias(
    alias: str,
    *,
    layer: int,
    frame: int,
    length: int,
    client_id: str | None = None,
) -> dict

client.create_object(
    obj: TimelineObject,
    *,
    client_id: str | None = None,
) -> dict

client.add_text(
    text: str,
    *,
    layer: int,
    frame: int,
    length: int,
    x: float = 0.0,
    y: float = 0.0,
    size: float = 34.0,
    color: str = "ffffff",
    client_id: str | None = None,
) -> dict

client.duplicate_object(
    obj,
    *,
    layer: int,
    frame: int,
) -> SnapshotObject

client.move_object(obj, *, layer: int, frame: int) -> dict
client.delete_object(obj) -> dict
```

AliasはAviUtl2 SDKのnative parserへ渡される。placementとdurationはAlias外で
指定する。未知のpropertyが無視される可能性があるため、詳細な生成はcatalogと
inspectionで結果を検証する。

`duplicate_object()`には`include_alias=True`で取得したobjectが必要で、作成後に
snapshot検証を行う。

### 6.10 playback rate、duration、trim、split

```text
client.set_playback_rate(
    obj,
    rate: float,
    *,
    effect: str | None = None,
    duration_mode: Literal["keep_timeline"] = "keep_timeline",
) -> dict

client.set_duration(obj, duration: int) -> dict

client.trim_media(
    obj,
    *,
    frame_start: int,
    frame_end: int,
    source_position: float | None = None,
) -> dict

client.split_media(obj, frame: int) -> MediaSplit
```

- playback `rate=2.0`はAviUtl2 raw 200%。
- `set_playback_rate`はtimeline durationを変えない。
- `set_duration`と`trim_media`は検証付きAlias置換。
- `frame_start/frame_end`はinclusive。
- `source_position`はAviUtl2のnative source position単位。
- split frameはright clipの開始frame。object内部でなければならない。
- 対応範囲は正の固定速度と静的source positionを持つ安全なclip。

構造置換後は元object IDが失効する。receiptの`updated_object`またはfresh
snapshotを使う。

### 6.11 transaction

command factory:

```python
TimelineTransactionCommand.move(obj, layer=3, frame=100)
TimelineTransactionCommand.delete(obj)
TimelineTransactionCommand.set_items(obj, updates=(...))
TimelineTransactionCommand.set_name(obj, "label")
TimelineTransactionCommand.set_effect_enabled(obj, selector, False)
```

実行:

```text
client.validate_transaction(
    expected_revision=snapshot.revision,
    commands=commands,
) -> TransactionReceipt

client.apply_transaction(
    expected_revision=snapshot.revision,
    commands=commands,
    operation_id="edit-block-0001",
) -> TransactionReceipt
```

transaction内で利用できるoperation:

- move
- delete
- set_items
- set_name
- effect.set_enabled

split、trim、duration、effect reorderなどの構造置換はtransaction内に入れられない。
先に独立操作として実行し、fresh snapshotを取得してから次のtransactionを作る。

### 6.12 explicit object group

```text
from aviutl2_api.live import ObjectGroup

group = ObjectGroup(tuple(created.objects))

client.move_group(
    group,
    frame_delta=30,
    layer_delta=1,
) -> TransactionReceipt

client.delete_group(group) -> TransactionReceipt
```

group内objectは同じsnapshot/revisionから取得し、重複してはならない。
動画・音声・字幕などを同時に扱うときに使用する。

### 6.13 shift、ripple、gap

```text
client.shift_after(
    *,
    expected_revision: int,
    frame: int,
    delta: int,
    group: ObjectGroup | None = None,
    layer_start: int | None = None,
    layer_end: int | None = None,
) -> TransactionReceipt

client.ripple_insert(
    *,
    expected_revision: int,
    frame: int,
    length: int,
    group: ObjectGroup | None = None,
) -> TransactionReceipt

client.ripple_delete(
    *,
    expected_revision: int,
    frame_start: int,
    frame_end: int,
    group: ObjectGroup | None = None,
) -> TransactionReceipt

client.close_gap(
    *,
    expected_revision: int,
    frame_start: int,
    frame_end: int,
    group: ObjectGroup | None = None,
) -> TransactionReceipt
```

- `shift_after`は開始frame以降を`delta`だけ移動する。
- `ripple_insert`は開始frame以降を正方向へ移動して空間を作る。
- `ripple_delete`は範囲内に完全に含まれるobjectを削除し、後続を詰める。
- `ripple_delete`がobject途中を横切る場合は拒否する。先にsplit/trimする。
- `close_gap`は指定rangeが空であることを確認して後続を詰める。
- `group`指定時は明示objectだけを対象にする。
- shiftは一時衝突を避ける順序で適用され、最終衝突をpreflightする。

### 6.14 Alias batch

```text
client.validate_batch(
    commands: Sequence[CreateFromAliasCommand],
) -> dict

client.apply_batch(
    commands: Sequence[CreateFromAliasCommand],
) -> dict
```

最大128 commands。`batch.apply`は一つのGUI Undo単位だが
`capabilities["batch"]["atomic"]`はfalseである。部分失敗を避けたい編集では
事前validateし、配置衝突をfresh snapshotで確認する。

### 6.15 subtitle

```text
from aviutl2_api.live import (
    SubtitleCue,
    SubtitleLayerPolicy,
    SubtitleStyle,
)

client.add_subtitles(
    source,
    *,
    layer_policy=SubtitleLayerPolicy(
        base_layer=10,
        max_layers=4,
        overlap="stack",
    ),
    style=SubtitleStyle(
        x=0,
        y=400,
        size=42,
        color="ffffff",
        speaker_colors={"Alice": "ffcc66"},
        include_speaker_label=True,
    ),
    language="ja",
) -> SubtitleBatchResult
```

`source`:

- `.srt` path
- `.vtt` / `.webvtt` path
- `Sequence[SubtitleCue]`

`overlap="stack"`は空いているlayerへ重ね、`"reject"`は重複を拒否する。
一batchは最大128 cuesで、一つのUndo単位になる。frame変換はcurrent sceneの
`rate/scale`を使用する。

parserのみ:

```text
parse_srt(text, language=None) -> tuple[SubtitleCue, ...]
parse_webvtt(text, language=None) -> tuple[SubtitleCue, ...]
load_subtitles(path, language=None, encoding="utf-8-sig")
```

### 6.16 native frame render

```text
client.render_frame(
    frame: int,
    *,
    output_path=None,
    overwrite=False,
    timeout=30.0,
) -> RenderedFrame

client.render_frames(
    frames: Sequence[int],
    *,
    expected_revision: int | None = None,
    timeout_per_frame=30.0,
) -> tuple[RenderedFrame, ...]

client.render_review_contact_sheet(
    *,
    snapshot: ProjectSnapshot | None = None,
    columns=4,
    thumbnail_width=320,
    boundary_padding=1,
    include_midpoints=True,
    max_frames=64,
    output_path=None,
    overwrite=False,
) -> ContactSheet
```

これはAviUtl2 current sceneの合成結果であり、GUI window全体のスクリーンショット
ではない。OpenCV再現ではなく、AviUtl2 SDKのnative scene rendererを使用する。

PNGはchunk受信後にbyte size、PNG signature、SHA-256をPython clientが検証する。
複数frameは一つのrevisionであることを検証する。

```python
rendered = client.render_frame(120)
vision_input = rendered.png
rendered.save("review-0120.png")  # 既存ならFileExistsError
```

高水準`LiveProject.render_preview()` / `render_previews()`はnative PNGをmemory内で
縮小し、既定ではJPEGの`RenderedPreview`を返す。`data`と`mime_type`をagent
frameworkの実際の画像attachmentへ渡す。`save()`やPNG生成の成功だけを
Vision確認完了として扱わない。`to_base64()` / `to_data_url()`はbinary transportが
使えない場合のための変換で、AviUtl2_API本体は特定agent製品へ依存しない。

### 6.17 native audio renderとQC

```text
client.render_audio(
    *,
    frame_start: int,
    frame_end: int,
    expected_revision: int | None = None,
    output_path=None,
    overwrite=False,
    timeout=120.0,
) -> RenderedAudio
```

出力はinterleaved stereo IEEE float32 little-endian PCM。

```python
audio = client.render_audio(
    frame_start=0,
    frame_end=899,
    expected_revision=snapshot.revision,
)
analysis = audio.analyze(
    clipping_threshold=1.0,
    silence_threshold_dbfs=-60.0,
)
print(
    analysis.peak,
    analysis.peak_dbfs,
    analysis.rms,
    analysis.rms_dbfs,
    analysis.clipping_samples,
    analysis.non_finite_samples,
    analysis.silence_ratio,
    analysis.integrated_lufs,
)
```

`integrated_lufs`はITU-R BS.1770のK-weightingとabsolute/relative gatingを用いた
EBU R128方式の解析値。短すぎる、またはgate後に有効blockがない場合は`None`。

### 6.18 preflight

```python
from aviutl2_api.live import run_preflight

report = run_preflight(
    client,
    subtitle_layers=(10, 11, 12),
    subtitle_overlap="warn",
    minimum_subtitle_frames=6,
    audio_range=(0, 899),
    clipping_threshold=1.0,
)
```

`subtitle_layers=None`、`subtitle_overlap="allow"`が既定。一般textを字幕と
推測しないため、異なるlayerで同時表示されるタイトル・注釈・装飾textだけでは
字幕warningを出さない。字幕の重複を診断するときだけlayerと`warn`または`error`
を明示する。

検査code:

- `MISSING_MEDIA`
- `UNREADABLE_MEDIA`
- `LOCKED_LAYERS`
- `API_LOCKED_OBJECTS`
- `TIMELINE_COLLISION`
- `TIMELINE_GAP`
- `LOCKED_EFFECTS`
- `EFFECT_OR_MODULE_MISSING`
- `FONT_MISSING`
- `SUBTITLE_TOO_SHORT`
- `SUBTITLE_OVERLAP`
- `AUDIO_NON_FINITE`
- `AUDIO_CLIPPING`
- `AUDIO_SILENCE`

`TIMELINE_COLLISION`と`TIMELINE_GAP`は、transitionや意図的な空白でも
発生するためwarningである。missing media、unreadable media、missing
font/effect、audio異常などのerrorだけが`report.ready`をfalseにする。

`PreflightReport.ready`はerror severityがない場合にtrue。warningは編集方針に応じて
agentが報告・判断する。

### 6.19 公開helperとreturn type索引

`aviutl2_api.live`が公開するmodule-level helper:

| helper | 用途 |
|---|---|
| `discover_instances()` | enabledなAviUtl2 instanceを列挙 |
| `serialize_object_alias(obj)` | `TimelineObject`をplacementなしのnative Aliasへ変換 |
| `make_text_object(...)` | text用`TimelineObject`を構築 |
| `parse_srt(text, language=None)` | SRT文字列をcue列へ変換 |
| `parse_webvtt(text, language=None)` | WebVTT文字列をcue列へ変換 |
| `load_subtitles(path, ...)` | 拡張子からSRT/WebVTTを読み分け |
| `apply_common_effect(...)` | catalog検証付きcommon effect適用 |
| `review_sample_frames(snapshot, ...)` | cut/object境界とmidpointのframeを選択 |
| `make_contact_sheet(frames, ...)` | native render済みPNGからmemory contact sheetを作成 |
| `analyze_pcm_f32le(pcm, ...)` | stereo/指定channel float PCMを解析 |
| `run_preflight(client, ...)` | read-only project QC |

主要return typeのfield:

| type | field |
|---|---|
| `SessionInfo` | `session_id`, `connection_id`, `client_name`, `max_cached_operations` |
| `BridgeEvent` | `sequence`, `timestamp_ms`, `type` |
| `EventWatchResult` | `events`, `latest_sequence`, `resync_required`, `timed_out` |
| `SceneInfo` | `scene_id`, `revision`, `name`, `width`, `height`, `rate`, `scale`, `sample_rate`, `non_undoable` |
| `LayerInfo` | `layer`, `name`, `enabled`, `locked`, `visible`, `object_count` |
| `LayerPage` | `revision`, `scene_id`, `layer_max`, `display_start`, `display_count`, `start`, `layers` |
| `EffectFlags` | `video`, `audio`, `filter_object`, `camera` |
| `CatalogItem` | `name`, `type`, `type_code` |
| `CatalogEffect` | `name`, `type`, `type_code`, `flags`, `items` |
| `EffectCatalogPage` | `start`, `total`, `next_start`, `effects` |
| `ProjectSnapshot` | `revision`, `scene_id`, `objects`, `offset`, `total`, `next_offset` |
| `SnapshotObject` | `object_id`, `revision`, `layer`, `frame_start`, `frame_end`, `name`, `alias`, `api_locked` |
| `ObjectInspection` | `object_id`, `revision`, `sample_frame`, `effects` |
| `EffectInspection` | `index`, `occurrence`, `name`, `selector`, `enabled`, `locked`, `items` |
| `ItemInspection` | `name`, `type`, `type_code`, `raw_value`, `track`, `sampled_check_value` |
| `TrackInspection` | `mode`, `parameters`, `sampled_value`, motion flags、track group field |
| `MediaProbe` | `exists`, `regular_file`, `extension_supported`, `readable`, `has_media_info`, `kind`, track counts、duration、width、height |
| `CreatedMediaObject` | actual `layer/frame_start/frame_end`, `revision`, `objects`, `snapshot_required`, `undo_grouped`, `warnings` |
| `MediaInventoryItem` | object/effect/item/file、existence/readability、duplicate/API lock、probe error |
| `MediaInventory` | `revision`, `scene_id`, `files`, unique/missing/unreadable counts |
| `MediaRelinkReceipt` | affected/matched counts、`revision`, `undo_grouped`, `snapshot_required`, `warnings` |
| `MediaSplitRange` | `layer`, `frame_start`, `frame_end` |
| `MediaSplit` | `left`, `right`, source positions、`playback_rate`, `revision`, `snapshot_required` |
| `ObjectSection` | `index`, `frame` |
| `ObjectSections` | `revision`, `sections` |
| `TransactionReceipt` | `valid`, `applied_count`, `revision`, `undo_grouped`, `snapshot_required`, `warnings` |
| `SubtitleCue` | `start_seconds`, `end_seconds`, `text`, `speaker`, `language` |
| `SubtitlePlacement` | `cue`, `layer`, `frame_start`, `frame_end`, `client_id` |
| `SubtitleBatchResult` | previous/new revision、placements、created objects、`undo_grouped` |
| `RenderedFrame` | frame、dimensions、scene/revision、SHA-256、`png` |
| `RenderedPreview` | frame、dimensions、scene/revision、SHA-256、MIME、`data` |
| `ContactSheet` | sampled frames、revision、dimensions、`png` |
| `RenderedAudio` | inclusive frame range、sample metadata、scene/revision、SHA-256、`pcm_f32le` |
| `AudioAnalysis` | peak/RMSとdBFS、clip/non-finite count、silence ratio、integrated LUFS |
| `PreflightIssue` | `severity`, `code`, `message`, `object_ids` |
| `PreflightReport` | revision/scene、snapshot、media inventory、issues、optional audio analysis |
| `EffectApplication` | semantic、resolved effect name/selector、追加か更新か、raw result |
| `UndoReceipt` | operation ID、before/after revision、grouped、Bridge実行可否、warnings |
| `EditingTransactionResult` | transaction receipt、fresh snapshot、Undo receipt |
| `ReviewBundle` | revision、contact sheet、optional rendered audio/analysis |

公開exception/constant:

- `BridgeRemoteError`: pluginのstructured error。
- `ProtocolError`: framing/envelope/result型が不正。
- `AmbiguousInstanceError`: PID未指定で複数instance。
- `CapabilityUnavailableError`: truthful capabilityが不足。
- `PROTOCOL_VERSION`: `1`。
- `MAX_PAYLOAD_BYTES`: `1 MiB`。
- `EffectSemantic`: common effect semanticのLiteral。
- `EffectValue`: common effectで許可されるtyped value union。

`LiveClient`はcontext managerとして使い、終了時に`close()`する。

## 7. 実践workflow

実行例は重複掲載せず、通常の編集は
[`LIVE_BRIDGE_AGENT_QUICK_START.md`](LIVE_BRIDGE_AGENT_QUICK_START.md)を正本とする。
この完全マニュアルでは、必要になった型・method・errorの節だけを参照する。

実践時の原則:

1. `LiveProject.connect(pid=...)`で対象を固定し、`summary()`とcapabilityを確認する。
2. `find(...).one()`で対象を一意にし、mutation後は返されたfresh参照を使う。
3. 複数作成・更新・削除・Effectは`EditPlan`でvalidateしてからapplyする。
4. split/trim/rippleなどの構造編集後はsnapshotを更新し、関連A/V objectをgroupで扱う。
5. Effectはsemantic profileとcatalog/inspectionを使い、unknown schemaを推測しない。
6. boundary/midpointのnative PNGと必要範囲のPCMを取得し、preflightとQCを再実行する。
7. `atomic=False`、rollback warning、`gui_undo_required`を結果から省略しない。

字幕、Effect、cut、native reviewの短いコード例はQuick Start、Wire payload例は
`LIVE_BRIDGE_PROTOCOL.md`を参照する。

## 8. 低水準Wire API

通常のagentはNamed Pipeを直接実装せず、`LiveProject`または`LiveClient`を使う。
framing、request/response envelope、全method、limits、payload、error codeの正本は
[`LIVE_BRIDGE_PROTOCOL.md`](LIVE_BRIDGE_PROTOCOL.md)であり、このマニュアルへ複製しない。

Wire APIを直接使うのは次の場合に限る:

- Python以外のclientを実装する。
- plugin/client protocolの互換試験を行う。
- transport、chunk lifecycle、operation ID冪等性を検証する。

直接実装でも、`system.hello`とcapability確認、`session.open`、fresh revision、
per-window consent、lock、payload/queue limit、frame/audio chunkの`release`、
partial rollbackの扱いを省略してはならない。SDK handleやpointerはWireへ公開されない。

## 9. error処理

Python:

```python
from aviutl2_api.live import BridgeRemoteError

try:
    client.move_object(obj, layer=3, frame=120)
except BridgeRemoteError as error:
    print(
        error.code,
        error.message,
        error.details,
        error.retryable,
        error.request_id,
    )
```

### 9.1 agentの判断が必要なerror

| code | 対応 |
|---|---|
| `STALE_PROJECT_STATE` | 自動再実行しない。fresh snapshotを取得し、対象と意図を再評価 |
| `OBJECT_API_LOCKED` | 停止し、ユーザーのロックを尊重 |
| `LAYER_LOCKED` | 別layerを勝手に選ばず、計画を再評価 |
| `EFFECT_LOCKED` | effect変更を停止 |
| `PLACEMENT_COLLISION` | fresh snapshotで衝突objectを確認 |
| `PLAN_APPLY_FAILED` / `PLAN_SCRATCH_FAILED` | rollback receiptを確認。GUI Undo要求なら停止 |
| `STRUCTURAL_EDIT_UNSAFE` / `SPLIT_UNSAFE` | 迂回や推測をせず、未対応として報告 |
| `OPERATION_ID_REUSED` | programming error。同じIDへ異なるpayloadを送らない |
| `SDK_METHOD_UNAVAILABLE` | capability falseとして扱う |
| `NON_UNDOABLE_CONFIRMATION_REQUIRED` | ユーザー意図が明示されるまでscene変更しない |
| `GAP_NOT_EMPTY` | 指定rangeを再検査 |
| `MEDIA_FILE_NOT_FOUND` / `UNSUPPORTED_MEDIA` | pathとnative probeを確認 |
| `HOST_EXPORTING` | `retryable`を確認し、export終了後にfresh snapshotから再開 |
| `BRIDGE_STOPPING` | 接続を閉じ、ユーザーの再Enableを待つ |

### 9.2 retry規則

- `retryable=False`: 同じpayloadを盲目的に再送しない。
- `retryable=True`: 状態を再取得し、対象が同じことを確認してから再計画する。
- network timeout: 同じsession、同じ`operation_id`、同じpayloadならresult再利用が
  可能。
- `STALE_PROJECT_STATE`: 古いobject IDを新revisionへ文字列置換してはならない。
- partial failure detailに`applied_count`があればfresh snapshotとGUI Undo状態を確認。

## 10. concurrencyとlifecycle

- 同時接続は最大8。
- 各connectionはserial request stream。
- SDK read/edit/renderは全client共有のFIFO queueで直列化。
- `event.watch`はSDK queueを占有しない。
- session result cacheはconnection単位、最大256 operations。
- client切断時はそのclientのcaptureとwaiterを解放する。
- API Disable、plugin終了時はqueue、render、event waiterをcancelする。
- 複数agentが同一revisionからmutationを開始しても、最初の一つ以外は通常staleに
  なる。各agentはfresh snapshotから再計画する。

## 11. security境界

- 外部API連携はAviUtl2の各ウィンドウ・processで起動時に必ずOFFへ戻る。Disable時は
  Named Pipeとdiscovery entryを公開しない。
- Named Pipeはremote接続を拒否する。
- Pipe DACLはLocal SystemとPipe所有者へ制限する。ただし外部連携Enable後は、
  同一Windowsユーザーで動くlocal processがclientになり得る。
- session IDは認証tokenではない。
- mutationはSDK edit callback内でfresh state、`expected_revision`、object ID、
  API/object/layer/effect lockを再検査する。余分な`unlock`fieldや古いsnapshotで
  lockを迂回できるとは扱わない。
- object名先頭のBridge markerはGUI表示用であり、clientはsnapshotの
  `api_locked`を正式な判定として使う。APIからlock解除methodは提供しない。
- API lockはLive Bridge mutationに対するlockで、GUI、別plugin、process injection、
  project file直接編集を防ぐものではない。
- lock対象とは別layerへの新規作成を禁止するscene-wide lockではないため、映像上から
  lock対象を覆うことは可能である。
- Aliasはlock中もsnapshotから読み取れる。機密情報保護機能ではない。
- AIへGUI操作権限を同時に与えると、AIがGUIからAPI lockを解除できる可能性がある。
- project pathやmedia pathをログ・外部サービスへ送る場合はユーザーのデータ境界を
  別途守る。

## 12. 1.0 release gate

`system.get_capabilities`の例:

```python
caps = client.get_capabilities()
print(caps["release_gate"])
# {
#   "ready_for_1_0": False,
#   "blocked_by": ["sdk_scene_crud", "sdk_undo_redo"]
# }
```

0.9.5で機能を偽装しない外部依存:

- 公式SDKのscene list/create/duplicate/switch
- 公式SDKのUndo/Redo実行API

SDKへ追加された場合も公開API名は維持し、capabilityと内部backendだけを切り替える。

## 13. エージェント用実行チェックリスト

編集開始前:

- [ ] 対象PIDを確認した
- [ ] 外部API連携がEnable
- [ ] `hello()`のversion/PIDを確認
- [ ] capabilitiesで必要methodを確認
- [ ] preflightまたはfresh snapshotを取得
- [ ] API/object/layer/effect lockを確認
- [ ] mediaをnative probeした
- [ ] effect/item/font/moduleをlive catalog/inspectionで確認

編集時:

- [ ] mutationはfresh revisionを使用
- [ ] retryが必要な操作にcaller管理operation IDを付与
- [ ] A/V/字幕の関連objectをexplicit group化
- [ ] 複数itemは`set_items`またはtransactionへまとめた
- [ ] structural fallbackのwarningを確認
- [ ] unsafe errorを迂回していない
- [ ] mutation後に古いobject IDを再利用していない

編集後:

- [ ] fresh snapshotで配置・値・effect順を確認
- [ ] boundary/midpointをnative PNG render
- [ ] 必要範囲をnative audio render
- [ ] clipping、silence、loudnessを確認
- [ ] missing media/font/effect、collision、gap、字幕をpreflight
- [ ] project save/export/playbackをBridgeが実行していない
- [ ] 結果と未対応事項をユーザーへ報告

## 14. `LocalProject` and explicit `SyncSession` API (0.9.6)

### 14.1 `LocalProject`

```python
from aviutl2_api import LocalProject

local = LocalProject.load("project.aup2")
print(
    local.path,
    local.source_sha256,
    local.display_scene_id,
    local.revision,
    local.dirty,
)
```

`LocalProject.load(path)` validates UTF-8, NUL-free structure and ambiguous
duplicate known sections/keys. The document layer retains ordered sections,
unknown lines/keys, third-party Effects, and untouched property ordering. It
patches only known sections changed by the high-level backend. Output is UTF-8
without BOM and uses CRLF.

`LocalProject.create(width=..., height=..., fps=..., ...)` creates an unbound
working copy. `reload(discard_changes=False)` refuses to discard dirty memory;
pass `True` only after deciding that those edits may be lost.

Query methods and properties:

```text
local.revision -> int
local.dirty -> bool                 # memory differs from bound disk source
local.path -> Path | None
local.source_sha256 -> str | None
local.display_scene_id -> int
local.summary() -> dict
local.objects -> LocalObjectSelection
local.get_snapshot() -> LocalSnapshot
local.find(
    name=..., name_contains=...,
    text=..., text_contains=...,
    file=..., file_contains=...,
    effect=..., layer=..., at=..., overlap=..., api_locked=...,
)
```

Local・Live・Syncでfilter名は共通である。ただし`.aup2`から安全に復元できない
`name`と`api_locked`をLocalへ指定すると、空集合ではなく
`LOCAL_QUERY_FILTER_UNAVAILABLE`を返す。Syncでは一致確認済みLive側で判定できる。

`LocalObject` is revision-scoped. After a successful mutation, retrieve a fresh
object from its `PlanResult` or from `local.find()`; stale references fail
instead of guessing.

Editing uses the same backend-neutral plan as Live:

```python
from aviutl2_api.editing import EditPlan, effect

plan = EditPlan(sequence="parallel")
plan.add_text(
    "第一章",
    key="title",
    duration=90,
    y=-200,
    effects=[effect("glow", strength=50)],
)

validation = local.validate(plan)   # no mutation
result = local.apply(plan)          # atomic in-memory commit, no file write
```

The initial local backend supports text/shape/media creation, text/transform
update, move/delete, Effect add/apply, and enable state. Object names and
structural split/trim/duration/reorder/section/ripple operations fail closed
when they cannot be represented by the shared plan. Unknown project versions
may be loaded and checkpointed losslessly but not high-level edited. Until
multi-scene ownership has a verified host fixture, multi-scene `apply()` fails
with `LOCAL_SCENE_OWNERSHIP_UNVERIFIED`; this prevents another scene from being
silently modified.

Local media duration is automatic only when Pillow, OpenCV, or WAVE can provide
a reliable value. Otherwise pass `duration=`. In a sync session, AviUtl2's
native media result and post-apply Alias readback become authoritative.

### 14.2 guarded file output

```python
checkpoint = local.checkpoint()  # project.ai-0001.aup2

copy = local.save_as("edited.aup2")

# Replacing an existing save-as target requires both controls.
copy = local.save_as(
    "edited.aup2",
    overwrite=True,
    expected_sha256="<current target SHA-256>",
)

# Replaces the bound source only after rechecking its load-time hash.
saved = local.save_source(overwrite=True, backup=True)
```

`checkpoint(path=None)` chooses the first unused `.ai-NNNN.aup2` sibling and
does not rebind the project. `save_as()` rebinds only after success. Existing
targets require both `overwrite=True` and the current target hash.
`save_source()` defaults to `overwrite=False` and writes nothing unless
`overwrite=True` is explicit. It rechecks `source_sha256` immediately before
replacement and creates a numbered `.bak` by default. Writes use a same-directory temporary
file, flush/fsync, then non-overwriting rename or authorized replace.

`LocalProject.apply()` and `SyncSession.apply()` never write a project file.
Only the three explicit methods above perform file output.

### 14.3 `SyncSession`

```python
from aviutl2_api.live import LiveProject
from aviutl2_api.sync import SyncSession

with LiveProject.connect(pid=46016) as live:
    sync = SyncSession.bind(local, live)
    status = sync.status()
    difference = sync.diff()
    result = sync.apply(plan, operation_id="agent-step-0001")
```

`apply()`はfresh statusとvalidationを内部で実施する。`validate()`はdry-run UIや
診断が必要な場合だけ個別に呼ぶ。

Bind compares the local display scene with the Live current scene: scene ID,
resolution, frame rate/scale, sample rate, object layer/range, and normalized
Alias. Numeric formatting, Effect IDs, property order, and known
`Group2`/`Group3` additions are normalized. Source path, Effect values/order,
enabled state, and media domain are not ignored. Project path is diagnostic;
semantic equality is authoritative, so an unsaved Live project can still bind.

`SyncStatus.state` values:

| State | Meaning and required action |
|---|---|
| `clean` | The only state in which `validate/apply` is permitted. |
| `local_dirty` | Local revision changed but semantic content still matches; inspect/rebind. |
| `live_dirty` | Live revision changed but semantic content still matches; refresh/rebind. |
| `diverged` | Object content/ranges differ or both revisions changed; reconcile manually. |
| `incompatible` | Scene settings, Alias availability, or capabilities prevent safe binding. |

`LocalProject.dirty` is separate: it reports unsaved disk state and can be true
while synchronization state remains `clean` after a successful sync operation.

Only a `SyncedObject` returned by `sync.find()` or a prior sync receipt can be
used for an existing-object shared edit. One-sided and ambiguous matches are
rejected. Automatic placement is resolved once using Live cursor position and
the union of occupied ranges on both sides. `at="end"` uses their common end.

Apply order is fixed:

1. fresh Local/Live status and semantic comparison;
2. local clone simulation;
3. Live native validation with expected revision;
4. Live apply with session operation ID;
5. fresh Live Alias readback;
6. local in-memory commit.

The cross-backend result reports `atomic=False`; Live is normally one GUI Undo
unit, but a filesystem/process boundary cannot be an atomic transaction. No
disk save follows step 6.

`SyncResult`では`local_simulation_result`がLive適用前のsimulation、
`local_snapshot`がnative Alias readback反映後の最終Local状態、`objects`が最終的な
同期object対応である。`live_result`、`undo_grouped`、`rollback`、
`gui_undo_required`、`warnings`もtop-levelで確認できる。

構造化された高水準例外は`code`、`details`、`retryable`、`required_action`を持つ。
clean状態のplan不正は`SyncValidationError`、状態不一致は`SyncConflictError`、
部分適用は`SyncPartialApplyError`として区別される。

If Live succeeds and the local commit fails, `SyncPartialApplyError` exposes a
`SyncRecoveryReceipt(live_applied=True, recovery_required=True)`. Keep the
session and call `sync.recover(error.receipt)`. Recovery verifies that neither
side has changed, reuses the operation's Alias readback, and commits only local
memory. It does not repeat the Live mutation or write a file. If either side
changed, recovery fails closed and the user must reconcile explicitly.

Calling GUI Undo reverts only Live. The session then reports divergence and
does not infer a local rollback. A normal recovery is: let the user save the
desired GUI state, call `local.reload(discard_changes=True)` only with approval,
and construct a new binding.

### 14.4 lifecycle observation

`project.get_info` includes nullable `project_file_path`. The event journal adds
`project_loaded` and `project_saving`. SDK callbacks only copy callback-scoped
metadata into the journal; they do not enter read/edit sections or mutate the
project. `project_saving` is emitted before AviUtl2 saves, so it must never be
treated as a save-completed receipt.

Capabilities added in 0.9.6:

```text
local_project
lossless_aup2_document
guarded_checkpoint_save
explicit_plan_sync
project_path_observation
project_lifecycle_notifications
```

The unsupported host-execution methods remain `project_open`, `project_save`,
and `project_save_as`. Local file methods are intentionally separate and never
pretend to command AviUtl2.

### 14.5 Related documents

- `docs/AGENT_API_CARD.md`: LLMへ渡す最小の英語中心API契約
- `docs/LIVE_BRIDGE_AGENT_QUICK_START.md`: Agent向け最短workflow
- `docs/releases/v0.9.6.md`: 0.9.6の更新・移行手順と既知制約
- `docs/LIVE_BRIDGE_PROTOCOL.md`: Wire protocol設計と実装根拠
- `docs/LIVE_BRIDGE_DEVELOPMENT.md`: native build、security回帰、manual integration
- `docs/aup2_format_specification.md`: `.aup2` parser/serializer実装ノート
- `protocol/CAPABILITIES_0.9.6.json`: 静的capability manifest
- `protocol/CHANGELOG.md`: protocol変更履歴
