# AviUtl2 Live Bridge Agent Quick Start

対象: plugin / Python package 0.9.5、protocol v1

AIエージェントがプログラムコードから編集するときは、原則として
`LiveProject`を使う。`LiveClient`はraw item、完全Alias、manual revisionなどが
必要な場合だけ使用する。

通常の編集タスクでは、まずこの文書だけをコンテキストへ渡す。完全APIマニュアルや
Wire protocolを最初から読み込まず、ここにない操作・型・errorの詳細が必要になった
時点で該当箇所だけを参照する。

0.9.3以前のコードはそのまま`LiveClient`を利用できる。0.9.5への移行では、手動の
snapshot/revision/operation ID管理を一度に書き換える必要はなく、新しい処理から
`LiveProject`へ移せばよい。create-time Effect stackを使う場合は、Python packageと
pluginの両方を0.9.5へ更新する。

## 1. 接続

AviUtl2側で対象ウィンドウの`設定 > 外部API連携設定...`を開き、外部API連携を
ONにする。複数ウィンドウがある場合はPIDを明示する。

```python
from aviutl2_api.live import LiveProject, discover_instances

print([(item.pid, item.plugin_version) for item in discover_instances()])

with LiveProject.connect(pid=46016) as project:
    print(project.summary())
```

## 2. 単発編集

```python
from aviutl2_api.editing import effect
from aviutl2_api.live import LiveProject

with LiveProject.connect(pid=46016) as project:
    title = project.add_text(
        "第一章",
        duration=90,
        y=-200,
        size=80,
        color="#ffffff",
        effects=[
            effect("glow", strength=50, color="#FFD966"),
            effect("outline", size_px=4, color="#202040"),
        ],
    )
    title = project.update(
        title.primary,
        x=120,
        scale=110,
        rotation=5,
        opacity=0.9,
    )
    frame = project.render(title.primary.midpoint)
    png_bytes = frame.png
```

- `at=None`: AviUtl2 GUIの現在カーソルframe。
- `layer=None`: Layer 0から探した最初の未ロック・非衝突layer。
- text/image/shapeの既定duration: 60 frames。
- video/audioの既定duration: AviUtl2 native media probeの時間をscene FPSへ変換。
- `scale=100`: 等倍。`opacity`は`0.0..1.0`。
- `rotation`はZ軸回転のdegree。必要なら`rotation_x/y/z`を明示する。
- text/shape作成時は`linear(start, end)`をtransform値へ渡すと直線移動になる。
- render結果はmemory上のPNGであり、projectや画像ファイルを自動保存しない。

画像・動画・音声も同じ形で追加できる。

```python
from aviutl2_api.editing import linear

image = project.add_image("assets/logo.png", at="end", scale=75)
video = project.add_video("assets/intro.mp4")
audio = project.add_audio("assets/bgm.wav", layer=10)
shape = project.add_shape(
    "star",
    width=120,
    height=120,
    color="#fff4b8",
    opacity=0.85,
    x=linear(900, -900),
    rotation=linear(0, 360),
)
```

相対素材pathはPython processのcurrent working directoryを基準に絶対pathへ解決
される。曖昧さを避ける場合は絶対pathを渡す。

## 3. 複数操作を一つにまとめる

```python
from aviutl2_api.editing import EditPlan, PlanValidationError, effect
from aviutl2_api.live import LiveProject

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
    effects=[effect("glow", strength=50)],
)

with LiveProject.connect(pid=46016) as project:
    validation = project.validate(plan)
    if not validation.valid:
        raise PlanValidationError(validation)
    result = project.apply(plan)
    title = result.objects["title"].primary
    print(result.revision, result.undo_grouped, result.warnings)
```

- `sequence="parallel"`: `at`省略objectを同じcursor frameへ別layerで配置。
- `sequence="serial"`: `at`省略objectを追加順に直列配置。
- `at="end"`: 現在のtimeline末尾へ配置。
- 同一plan内で先に解決した配置、move、deleteも後続の衝突判定へ反映する。
- 成功した`EditPlan`はsingle-use。同じinstanceをもう一度`apply()`できない。
- 通信retryの重複防止はLive Bridge sessionのoperation IDが担当する。
- 成功は原則1回のGUI Undo単位。ただしSDKに完全rollback機構がないため
  `result.atomic`は`False`。

失敗時の`PlanApplyError.result.rollback`を必ず確認する。

```python
from aviutl2_api.editing import PlanApplyError

try:
    result = project.apply(plan)
except PlanApplyError as error:
    if error.result is not None:
        print(error.result.rollback)
        if error.result.rollback.gui_undo_required:
            print("ユーザーにAviUtl2 GUI Undoを依頼する")
    raise
```

## 4. 既存objectを探して編集する

```python
with LiveProject.connect(pid=46016) as project:
    chapter = project.find(text="第一章").one()
    chapter = project.update(chapter, text="第1章", x=100).primary
    chapter = project.move(chapter, at=300, layer=4).primary
```

`find()`のfilter:

- `name`, `text`, `file`, `effect`
- `layer`, `at`
- `api_locked`

`one()`は0件でも複数件でも拒否する。mutation後は返された新しい参照を使い、
古い`LiveObject`を再利用しない。

## 5. カット・effect・review

```python
clip = project.find(file="intro.mp4").one()
parts = project.split(clip, frame=180)

clip = project.find(layer=clip.layer, at=200).one()
clip = project.trim(
    clip,
    frame_start=180,
    frame_end=359,
    source_position=6.0,
).one()
clip = project.set_duration(clip, 120).one()

from aviutl2_api.editing import effect

applied = project.apply_effect(clip, effect("blur", radius_px=12))
clip = project.find(file="intro.mp4", at=200).one()
applied = project.update_effect(
    clip,
    applied,
    effect("blur", radius_px=20),
)
```

主要20 profileは自然単位から完全なAviUtl2 item列へ変換され、実機catalogと
照合される。`available_effect_profiles()`で現在のhostに適用可能なprofileだけを
確認できる。未知・第三者Effectは`native_effect()`または`project.client`を使い、
item名・値を明示する。意味や単位は推測されない。

一般textは字幕と推測されず、同時表示されても字幕overlap warningは出ない。
字幕診断が必要な場合だけ`project.preflight(subtitle_layers=(10, 11),
subtitle_overlap="warn")`のようにlayerと方針を明示する。

```python
sheet = project.contact_sheet(frames=(0, 90, 180, 270))
audio, analysis = project.audio_review(0, 299)
report = project.preflight(audio_range=(0, 299))
```

PNG、contact sheet、PCMはmemory返却が既定である。

## 6. Fail closedとescape hatch

- stale revision: `ProjectChangedError`
- validation失敗: `PlanValidationError`
- apply/rollback失敗: `PlanApplyError`
- 旧pluginで混在plan不可: `CapabilityUnavailableError`
- object/layer/effect lockはAPIから解除しない。
- project open/save/save-as、export、playback操作は提供しない。
- 外部連携はウィンドウごとに既定OFF。同一Windowsユーザーのlocal processは、
  EnableされたPipeへ接続可能であり、session IDは認証tokenではない。
- API lockはBridge経由のmutationを拒否する機能で、GUI操作、別plugin、process
  injection、project fileの直接変更、lock対象を別layerから覆う作成までは防がない。
- lock中もAliasや素材pathは読み取り可能であり、機密情報保護機能ではない。

高水準化されていないsection/ripple/subtitle/raw itemや完全Alias操作は、明示的に
低水準clientへ降りる。

```python
with LiveProject.connect(pid=46016) as project:
    client = project.client
    snapshot = client.get_snapshot(include_alias=True)
    # 完全API: LIVE_BRIDGE_AGENT_API_MANUAL.md を参照
```

完全なPython型、戻り値、error、制約は
[`LIVE_BRIDGE_AGENT_API_MANUAL.md`](LIVE_BRIDGE_AGENT_API_MANUAL.md)を参照する。
Named Pipeを直接実装する場合だけ
[`LIVE_BRIDGE_PROTOCOL.md`](LIVE_BRIDGE_PROTOCOL.md)を参照する。
