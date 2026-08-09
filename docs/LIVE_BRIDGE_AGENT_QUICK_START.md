# AviUtl2 API 0.9.6 エージェント・クイックスタート

AIエージェントが最初に読む日本語ガイドです。最小の英語コンテキストが必要なら
[Agent API Card](AGENT_API_CARD.md)、全methodの詳細が必要なら
[完全APIマニュアル](LIVE_BRIDGE_AGENT_API_MANUAL.md)を参照してください。

## 1. Local・Live・Syncの選択

| やりたいこと | API | `.aup2`自動保存 |
|---|---|---|
| ファイルを安全な作業コピーとして編集 | `LocalProject` | しない |
| 開いているAviUtl2を編集・native確認 | `LiveProject` | しない |
| 新しいplanを両方へ明示適用 | `SyncSession` | しない |

自動同期、暗黙保存、host Open/Save、export、playbackはありません。

標準import:

```python
from aviutl2_api import (
    EditPlan,
    LiveProject,
    LocalProject,
    SyncSession,
    effect,
    linear,
)
```

## 2. Local編集とcheckpoint

```python
local = LocalProject.load("project.aup2")

title = local.add_text(
    "第一章",
    duration=90,
    y=-200,
    size=72,
    effects=[effect("glow", strength=50)],
)
title = local.update(title.primary, x=120, scale=110)

# 元のproject.aup2は変更しない。
saved = local.checkpoint()  # project.ai-0001.aup2
print(saved.path)
```

`add_text()`などの即時methodも内部では1コマンドの`EditPlan`として処理されます。
元ファイルの置換は、ユーザーが明示的に求めた場合だけ実行してください。

```python
local.save_source(overwrite=True, backup=True)
```

引数なしの`save_source()`は書き込まず拒否します。置換時にはload時SHA-256を
再検査し、既定でbackupを作ります。

## 3. 開いているAviUtl2の編集

AviUtl2の対象ウィンドウで外部API連携をONにします。複数ウィンドウがあり得る
場合はPIDを必ず指定します。

```python
with LiveProject.connect(pid=46016) as live:
    print(live.summary())
    title = live.add_text("第一章", duration=90, y=-200, size=72)
    title = live.update(title.primary, x=120)
    rendered = live.render(title.primary.midpoint)
    png_bytes = rendered.png
```

`render()`はAviUtl2 native PNGをmemoryで返します。ファイル保存は
`rendered.save(path)`を明示した場合だけ行い、既存ファイルは`overwrite=True`なしで
上書きしません。

object参照はrevision scopedです。mutation前の古い参照を再利用せず、返された
fresh objectを次の操作に使います。

## 4. 複数操作を一つにまとめる

```python
plan = EditPlan(sequence="parallel")
plan.add_video("intro.mp4", key="video", fit="contain")
plan.add_text(
    "第一章",
    key="title",
    duration=90,
    y=-200,
    effects=[effect("outline", size_px=4, color="#202040")],
)
plan.add_shape(
    "star",
    key="star",
    duration=90,
    x=linear(900, -900),
    rotation=linear(0, 360),
)

with LiveProject.connect(pid=46016) as live:
    validation = live.validate(plan)  # dry-runが必要な場合
    if not validation.valid:
        raise RuntimeError(validation.errors)
    result = live.apply(plan)
```

成功したplanはsingle-useです。`sequence="parallel"`では省略したframeを共有し、
`sequence="serial"`では追加順に並べます。`at=None`はcursor、`at="end"`は末尾、
`layer=None`は最初の空きlayerです。

## 5. LocalとLiveへの明示同期

Liveに開いているsceneと同じ`.aup2`を読み込みます。既存内容が一致しない場合は
自動統合せず拒否します。

```python
local = LocalProject.load("project.aup2")
plan = EditPlan().add_text("第一章", key="title", duration=90, y=-200)

with LiveProject.connect(pid=46016) as live:
    sync = SyncSession.bind(local, live)
    status = sync.status()
    if not status.clean:
        raise RuntimeError(sync.diff())

    result = sync.apply(plan)  # この呼出しだけが同期trigger
    title = result.objects["title"].primary
    png = live.render(title.midpoint).png

# 上ではdisk未保存。必要なら別操作としてcheckpointを作る。
local.checkpoint()
```

`sync.apply()`は内部でfresh validationを行います。`sync.validate()`はdry-run表示や
診断が必要な場合だけ先に呼びます。

GUIでCtrl+Zを行うとLive側だけが戻り、Syncは`diverged`になります。Localを推測で
rollbackしません。ユーザーが保存内容を整理したあと`local.reload()`し、新しい
`SyncSession`をbindしてください。

## 6. 検索

```python
titles = live.find(text_contains="章", overlap=(0, 300))
title = titles.one()
```

Local・Live・Syncで次のfilter名を共有します。

- `name`、`name_contains`
- `text`、`text_contains`
- `file`、`file_contains`
- `effect`、`layer`、`at`、`overlap`、`api_locked`

`one()`は0件・複数件を明示的に拒否します。Localの`.aup2`だけでは安全に判定
できない`name`と`api_locked`は`LOCAL_QUERY_FILTER_UNAVAILABLE`になります。

## 7. Effect・単位・media

- 座標・サイズ: pixel
- rotation: degree
- scale: 100が等倍
- opacity: 0.0～1.0
- color: `#RRGGBB`
- animation: `linear(start, end)`
- 標準Effect: `effect("glow", strength=50)`
- exact native schema: `native_effect(name, values)`

image/videoは`fit="contain" | "cover"`に対応します。audioには視覚transform引数が
ありません。Effect名やitem値を推測せず、Liveでは
`live.describe_schema("glow")`または`live.available_effect_profiles()`を使います。

## 8. エラーと完了判定

高水準の復旧可能な例外は`code`、`details`、`retryable`、`required_action`を持ちます。

- `SyncValidationError`: planを修正する。
- `SyncConflictError`: diffを確認し、refresh/rebindする。force mergeしない。
- `SyncPartialApplyError`: receiptを確認し、許可されていれば`recover()`する。
- `gui_undo_required=True`: 停止してユーザーへ報告する。
- `LocalFileChangedError`: hash競合。reloadまたは別pathを選ぶ。

編集完了を報告する前に、代表frame・cut境界・字幕境界をnative PNGで確認します。
音声編集ではPCMのpeak/RMSも確認します。PNGを生成しただけではVision確認済みとは
扱いません。

## 9. 実行例

```powershell
python examples/local_checkpoint.py project.aup2
python examples/explicit_sync.py project.aup2 --pid 46016 --checkpoint
```

低水準の`LiveClient`、完全Alias、raw item、manual revisionが必要な場合だけ
`live.client`をescape hatchとして使用してください。
