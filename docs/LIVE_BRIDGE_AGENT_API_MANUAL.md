# AviUtl2 Live Bridge エージェント向け完全APIマニュアル

対象バージョン: plugin / Python client 0.9.2

Wire protocol: v1 additive

対象OS: Windows

最終更新: 2026-07-29

この文書は、Codex、Claude Code、Copilot CLI、Agent ZeroなどのAIエージェントが、
ユーザーの開いているAviUtl2プロジェクトをLive Bridge経由で安全に編集するための
規範的な利用マニュアルである。

通常はPythonの`aviutl2_api.live.LiveClient`を使用する。Named Pipeを直接実装する
場合だけ「低水準Wire API」を参照すること。

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

`LiveClient.connect()`は0.9.2 pluginに対して自動的に`session.open`を呼ぶ。

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

`EditingSession`はcapability確認、preflight、transaction、fresh snapshot、
native reviewをまとめる。

```python
from aviutl2_api.live import (
    EditingSession,
    ItemUpdate,
    LiveClient,
    TimelineTransactionCommand,
)

with LiveClient.connect(pid=32652) as client:
    editing = EditingSession(client)

    report = editing.preflight(
        subtitle_layers=(10, 11, 12),
        audio_range=(0, 899),
    )
    if not report.ready:
        for issue in report.errors:
            print(issue.code, issue.message, issue.object_ids)
        raise RuntimeError("preflight failed")

    snapshot = report.snapshot
    title = snapshot.objects[0]
    commands = [
        TimelineTransactionCommand.set_items(
            title,
            (
                ItemUpdate("テキスト", "テキスト", "新しいタイトル"),
                ItemUpdate("標準描画", "X", 120.0),
            ),
        )
    ]
    edited = editing.apply_transaction(commands)
    print(edited.undo)

    review = editing.review(
        frames=(0, 30, 60, 90),
        audio_range=(0, 899),
    )
    print(review.audio_analysis)
```

`EditingSession.undo()`と`redo()`は公式SDK capabilityが追加されるまで
`CapabilityUnavailableError`を送出する。`UndoReceipt.grouped=True`はGUIの
Undo単位が一つであることを示すが、Bridgeから実行可能という意味ではない。

## 6. Python APIリファレンス

以下では`timeout`引数の説明を省略する。指定しなければ接続時の
`default_timeout`が使用される。

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

0.9.2では公式SDKに実行APIがなく、呼ぶと`SDK_METHOD_UNAVAILABLE`になる。
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
全objectが入る。以後一緒に扱うなら`ObjectGroup(created.objects)`を作る。

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
    minimum_subtitle_frames=6,
    audio_range=(0, 899),
    clipping_threshold=1.0,
)
```

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

## 7. 実践レシピ

### 7.1 既存textを探して変更する

object indexやlabelだけを信用せずinspectionでtext itemを確認する。

```python
with LiveClient.connect(pid=32652) as client:
    snapshot = client.get_snapshot(include_alias=False)
    text_objects = []
    for obj in snapshot.objects:
        inspection = client.inspect_object(obj)
        if any(
            item.type == "text"
            for effect in inspection.effects
            for item in effect.items
        ):
            text_objects.append(obj)

    if len(text_objects) != 1:
        raise RuntimeError("対象text objectを一意に特定できません")

    client.set_text(text_objects[0], "差し替え後の字幕")
    fresh = client.get_snapshot(include_alias=False)
```

### 7.2 動画と同時生成音声を一緒に移動する

```python
created = client.add_video(
    r"D:\assets\intro.mp4",
    layer=2,
    frame=0,
    length=0,
)
if not created.objects:
    raise RuntimeError("fresh created object references are unavailable")

group = ObjectGroup(created.objects)
receipt = client.move_group(
    group,
    frame_delta=90,
    layer_delta=2,
)
```

### 7.3 複数変更を一つのUndo単位にする

```python
snapshot = client.get_snapshot(include_alias=False)
obj = snapshot.objects[0]
inspection = client.inspect_object(obj)
draw = next(effect for effect in inspection.effects if effect.name == "標準描画")

commands = (
    TimelineTransactionCommand.set_name(obj, "API edited title"),
    TimelineTransactionCommand.set_items(
        obj,
        (
            ItemUpdate(draw.selector, "X", 100.0),
            ItemUpdate(draw.selector, "Y", -50.0),
        ),
    ),
)

validation = client.validate_transaction(
    expected_revision=snapshot.revision,
    commands=commands,
)
if not validation.valid:
    raise RuntimeError(validation)

receipt = client.apply_transaction(
    expected_revision=snapshot.revision,
    commands=commands,
    operation_id="title-layout-0001",
)
fresh = client.get_snapshot(include_alias=False)
```

### 7.4 cutしてgapを閉じる

```python
snapshot = client.get_snapshot(include_alias=False)
clip = next(
    obj for obj in snapshot.objects
    if obj.frame_start < 300 <= obj.frame_end
)

split = client.split_media(clip, frame=300)

# splitは構造置換なのでfresh snapshotを取り直す。
snapshot = client.get_snapshot(include_alias=False)

# 300..329が完全に空であることを確認した上で後続を30frame詰める。
receipt = client.close_gap(
    expected_revision=snapshot.revision,
    frame_start=300,
    frame_end=329,
)
```

`ripple_delete`がclip途中を横切る場合は、先に境界でsplitするかtrimする。

### 7.5 effectをcatalog確認後に追加する

```python
snapshot = client.get_snapshot(include_alias=False)
obj = snapshot.objects[0]

effects = {}
start = 0
while True:
    page = client.get_effect_catalog(start=start, count=128)
    effects.update({effect.name: effect for effect in page.effects})
    if page.next_start is None:
        break
    start = page.next_start

effect = effects.get("クリッピング")
if effect is None:
    raise RuntimeError("実機にクリッピングeffectがありません")

available_items = {item.name for item in effect.items}
requested = {"上": 10.0, "下": 10.0}
if not requested.keys() <= available_items:
    raise RuntimeError("実機schemaと要求itemが一致しません")

client.apply_common_effect(
    obj,
    "crop",
    requested,
    effect_name="クリッピング",
)
```

### 7.6 字幕を配置してnative reviewする

```python
policy = SubtitleLayerPolicy(
    base_layer=10,
    max_layers=3,
    overlap="stack",
)
result = client.add_subtitles(
    r"D:\captions\episode01.vtt",
    layer_policy=policy,
    language="ja",
)

snapshot = client.get_snapshot(include_alias=False)
sheet = client.render_review_contact_sheet(
    snapshot=snapshot,
    columns=4,
    thumbnail_width=320,
)
print(result.revision, sheet.frames, len(sheet.png))
```

### 7.7 完成前QC

```python
editing = EditingSession(client)
report = editing.preflight(
    subtitle_layers=(10, 11, 12),
    audio_range=(0, 1799),
)

for issue in report.issues:
    print(issue.severity, issue.code, issue.message)

review = editing.review(audio_range=(0, 1799))
assert review.revision == report.revision
print(review.audio_analysis)
```

## 8. 低水準Wire API

通常のagentはこの節を直接使わず`LiveClient`を使う。

### 8.1 endpointとframing

endpoint:

```text
\\.\pipe\AviUtl2.LiveBridge.<PID>
```

各message:

```text
uint32 little-endian payload byte length
UTF-8 JSON payload
```

payloadは1 byte以上1 MiB以下。Pipeはremote clientを拒否し、ownerとLocal System
だけをDACLで許可する。同一ユーザーのlocal processに対するclient認証ではない。

request:

```json
{
  "id": "req-0001",
  "protocol_version": 1,
  "method": "system.hello",
  "params": {}
}
```

success:

```json
{
  "id": "req-0001",
  "ok": true,
  "result": {}
}
```

error:

```json
{
  "id": "req-0001",
  "ok": false,
  "error": {
    "code": "STALE_PROJECT_STATE",
    "message": "The project changed.",
    "details": {
      "current_revision": 123458
    },
    "retryable": true
  }
}
```

### 8.2 全51メソッド

`T`は共通target params
`{"expected_revision": int, "target": {"object_id": str}}`を表す。
mutationにはsession内で一意な`operation_id`を追加できる。

| method | params | 主なresult / 備考 |
|---|---|---|
| `system.hello` | `{}` | version、PID、SDK baseline、edit state |
| `system.ping` | `{}` | `pong` |
| `system.get_capabilities` | `{}` | methods、limits、backend、release gate |
| `session.open` | `client_name` | session ID、connection ID、cache limit |
| `event.watch` | `after_sequence`, `timeout_ms`, optional `types` | events、latest sequence、resync、timeout |
| `scene.get_current` | `{}` | current scene settings/revision |
| `scene.update_current` | revision、confirmation、変更field | 非Undo scene update |
| `effect.catalog` | `start`, `count` | paged effect/item schema |
| `font.catalog` | `start`, `count` | `entries: string[]` |
| `palette.catalog` | `start`, `count` | name、RGBA colors |
| `module.catalog` | `start`, `count` | name、information、type |
| `project.get_info` | `{}` | resolution、fps、sample rate、cursor、max range |
| `project.get_layers` | `start`, `count` | revision-scoped layer page |
| `project.get_snapshot` | paging/filter params | revision-scoped object page |
| `layer.update` | revision、layer、name/enabled | generic mutation receipt |
| `media.probe` | absolute `file` | native readability/media info |
| `media.inventory` | `{}` | all file items、missing/duplicate counts |
| `media.relink` | revision、`replacements[]` | relink receipt |
| `object.create_from_alias` | alias、layer、frame、length、optional client ID | create receipt |
| `object.create_from_media_file` | file、layer、frame、length | actual range、全created objects |
| `object.inspect` | `T`、optional `sample_frame` | effects/items/tracks/locks |
| `object.effect.add` | `T`、effect、optional raw `items` object | mutation receipt |
| `object.effect.delete` | `T`、selector | mutation receipt |
| `object.effect.set_enabled` | `T`、selector、enabled | mutation receipt |
| `object.effect.reorder` | `T`、complete `selectors[]` | Alias replacement receipt/order |
| `object.section.list` | `T` | section boundaries |
| `object.section.create` | `T`、frame | mutation receipt |
| `object.section.delete` | `T`、section | mutation receipt |
| `object.section.move` | `T`、section、frame | mutation receipt |
| `object.set_item` | `T`、effect、item、raw string value | mutation receipt |
| `object.set_items` | `T`、`items[]` | mutation receipt |
| `object.set_name` | `T`、string/null name | mutation receipt |
| `object.move` | `T`、layer、frame | mutation receipt |
| `object.delete` | `T` | mutation receipt |
| `object.split_media` | `T`、frame | left/right range/source position |
| `object.set_duration` | `T`、duration | Alias replacement receipt |
| `media.trim` | `T`、inclusive range、optional source position | Alias replacement receipt |
| `timeline.transaction.validate` | revision、`commands[]` | validation receipt |
| `timeline.transaction.apply` | revision、`commands[]` | transaction receipt |
| `timeline.shift_after` | revision、frame、delta、optional scope | transaction receipt |
| `timeline.ripple_insert` | revision、frame、length、optional object IDs | transaction receipt |
| `timeline.ripple_delete` | revision、inclusive range、optional object IDs | transaction receipt |
| `timeline.close_gap` | revision、inclusive range、optional object IDs | transaction receipt |
| `frame.render` | frame | PNG capture metadata |
| `frame.read_chunk` | capture ID、index | base64 chunk |
| `frame.release` | capture ID | released bool |
| `audio.render` | inclusive frame range、optional revision | f32le capture metadata |
| `audio.read_chunk` | capture ID、index | base64 chunk |
| `audio.release` | capture ID | released bool |
| `batch.validate` | Alias create `commands[]` | placement/structure validation |
| `batch.apply` | Alias create `commands[]` | grouped non-atomic create receipt |

### 8.3 transaction command wire format

```json
{
  "expected_revision": 123456,
  "commands": [
    {
      "op": "move",
      "target": {"object_id": "obj-123456-0"},
      "layer": 2,
      "frame": 120
    },
    {
      "op": "set_items",
      "target": {"object_id": "obj-123456-1"},
      "items": [
        {
          "effect": "標準描画",
          "item": "X",
          "value": "100.000000"
        }
      ]
    },
    {
      "op": "set_name",
      "target": {"object_id": "obj-123456-2"},
      "name": "label"
    },
    {
      "op": "effect.set_enabled",
      "target": {"object_id": "obj-123456-3"},
      "selector": "ぼかし#1",
      "enabled": false
    },
    {
      "op": "delete",
      "target": {"object_id": "obj-123456-4"}
    }
  ],
  "operation_id": "transaction-0001"
}
```

### 8.4 frame/audio chunk lifecycle

1. `frame.render`または`audio.render`でcapture IDとmetadataを得る。
2. `index=0..chunk_count-1`を順にreadする。
3. base64 decodeし、offset、size、EOFを検査する。
4. 全byteのSHA-256をmetadataと比較する。
5. 成否にかかわらず`release`する。

captureにはTTL、個数、総memory limitがある。保持し続けない。
Python `render_frame()`と`render_audio()`はこの処理を自動で行う。

### 8.5 capability falseの予約method

次のmethodは0.9.2の`methods`へ含まれず、直接呼ぶと
`SDK_METHOD_UNAVAILABLE`になる。

- `scene.list`
- `scene.create`
- `scene.duplicate`
- `scene.switch`
- `history.undo`
- `history.redo`

`scene.delete`、project save/export/playback APIは予約methodとしても公開しない。

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

- Named Pipeはremote接続を拒否する。
- 外部連携Enable後は同一Windowsユーザーのlocal processがclientになり得る。
- session IDは認証tokenではない。
- API lockはLive Bridge mutationに対するlockで、GUI、別plugin、process injection、
  project file直接編集を防ぐものではない。
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

0.9.2で機能を偽装しない外部依存:

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

## 14. 関連文書

- `docs/LIVE_BRIDGE_PROTOCOL.md`: Wire protocol設計と実装根拠
- `docs/LIVE_BRIDGE_SECURITY.md`: API lockと脅威モデル
- `docs/LIVE_BRIDGE_DEVELOPMENT.md`: native build/install
- `docs/LIVE_BRIDGE_V1_ROADMAP.md`: version計画とSDK依存
- `protocol/CAPABILITIES_0.9.2.json`: 静的capability manifest
- `protocol/CHANGELOG.md`: protocol変更履歴
