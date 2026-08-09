# AviUtl2 API 0.9.6 — Agent API Card

This is the smallest recommended context for an LLM that writes editing code.
Use the complete manual only when an operation is not covered here.

## Choose one backend

| Goal | API | Writes `.aup2` automatically? |
|---|---|---|
| Safely edit a local working copy | `LocalProject` | No |
| Edit the scene open in one AviUtl2 window | `LiveProject` | No |
| Apply one new plan to both | `SyncSession` | No |

There is no background synchronization. Host Open/Save, export, playback, and
API-lock removal are unsupported.

## Standard imports

```python
from aviutl2_api import (
    EditPlan,
    LiveProject,
    LocalProject,
    SyncConflictError,
    SyncPartialApplyError,
    SyncSession,
    SyncValidationError,
    effect,
    linear,
    native_effect,
)
```

## Local working copy

```python
local = LocalProject.load("project.aup2")
title = local.add_text(
    "第一章",
    duration=90,
    y=-200,
    effects=[effect("glow", strength=50)],
)
title = local.update(title.primary, x=120, scale=110)

# Explicit new file; project.aup2 is unchanged.
receipt = local.checkpoint()  # project.ai-0001.aup2
```

`LocalProject.apply()` and immediate methods mutate memory only. Overwriting the
loaded source requires the intentionally verbose call below:

```python
local.save_source(overwrite=True, backup=True)
```

Never call `save_source()` unless the user explicitly requested source-file
replacement. It checks the load-time SHA-256 again immediately before replace.

## Live editing

```python
with LiveProject.connect(pid=46016) as live:
    title = live.add_text("第一章", duration=90, y=-200, size=72)
    title = live.update(title.primary, x=120)
    frame = live.render(title.primary.midpoint)  # native PNG bytes in frame.png
```

The user must enable external API access in that AviUtl2 window. Always pass a
PID when more than one instance may exist. A returned object is revision-scoped;
use the fresh object returned by each mutation.

## Multiple edits and explicit synchronization

```python
plan = EditPlan(sequence="parallel")
plan.add_video("intro.mp4", key="video")
plan.add_text(
    "第一章",
    key="title",
    duration=90,
    y=-200,
    effects=[effect("outline", size_px=4, color="#202040")],
)

local = LocalProject.load("project.aup2")
with LiveProject.connect(pid=46016) as live:
    sync = SyncSession.bind(local, live)
    if not sync.status().clean:
        raise RuntimeError(sync.diff())
    result = sync.apply(plan)  # Live + local memory, one explicit action
    preview = live.render(result.objects["title"].primary.midpoint)

local.checkpoint()  # separate explicit disk write
```

`add_video()` probes with AviUtl2 first. If the source has an audio track, the
high-level Live backend uses a verified combined-object Alias fallback so
embedded audio is enabled and audio Effects reach native PCM review.
`add_audio()` intentionally has no visual transform parameters.

`apply()` performs fresh validation itself. Call `validate()` separately only
for a dry-run UI or diagnostic report. A successful plan is single-use.

## Placement, transforms, and effects

- `at=None`: current GUI cursor for Live; project cursor for Local.
- `at="end"`: after the latest object.
- `layer=None`: first collision-free layer from Layer 0.
- `sequence="parallel"`: omitted positions share one frame.
- `sequence="serial"`: omitted positions are appended in command order.
- Position and size: pixels. Rotation: degrees. Scale: `100` is native size.
- Opacity: `0.0` transparent through `1.0` opaque.
- `linear(start, end)`: linear animation for visual transform values.
- `effect("glow", ...)`: verified semantic profile.
- `native_effect(name, values)`: exact native schema; no guessing.

Image and video support `fit="contain" | "cover"`. Audio deliberately has no
visual transform arguments. Use `live.describe_schema("glow")` or
`live.available_effect_profiles()` instead of guessing Effect parameters.

## Search

```python
titles = project.find(text_contains="chapter", overlap=(0, 300))
title = titles.one()  # rejects zero and multiple results
```

Local, Live, and Sync share `name`, `name_contains`, `text`, `text_contains`,
`file`, `file_contains`, `effect`, `layer`, `at`, `overlap`, and `api_locked`.
Local raises `LOCAL_QUERY_FILTER_UNAVAILABLE` for `name` or `api_locked`, because
their `.aup2` representation is not verified. Sync can evaluate those filters
against its verified Live half.

## Error branching

High-level recoverable errors expose `code`, `details`, `retryable`, and
`required_action`.

```python
try:
    result = sync.apply(plan)
except SyncValidationError as error:
    inspect(error.validation.errors)       # fix the plan
except SyncConflictError as error:
    inspect(error.status, error.diff)      # refresh/rebind; never force merge
except SyncPartialApplyError as error:
    inspect(error.receipt)
    if error.receipt.recovery_required:
        sync.recover(error.receipt)        # local commit only
```

If `gui_undo_required` is true, stop and report it to the user. Do not resend an
ambiguous or consumed plan.

## Review and safety checklist

1. Start from a fresh snapshot/status.
2. Use semantic Effect profiles and natural units.
3. Apply a new plan once and retain fresh returned references.
4. Inspect several native PNG frames; inspect PCM for audio edits.
5. Report warnings, rollback state, and GUI Undo requirements.
6. Write only with an explicit `checkpoint()`, `save_as()`, or user-authorized
   `save_source(overwrite=True)` call.

日本語メモ: `sync.apply()`は明示的に呼んだときだけ同期します。GUIのUndoは
Live側だけを戻すため、その後は`diverged`として扱い、推測でLocalを戻しません。

Full reference: [LIVE_BRIDGE_AGENT_API_MANUAL.md](LIVE_BRIDGE_AGENT_API_MANUAL.md)
