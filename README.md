# AviUtl2 Project API

[![PyPI version](https://badge.fury.io/py/aviutl2-api.svg)](https://pypi.org/project/aviutl2-api/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Python API for manipulating AviUtl ver.2 project files (.aup2).

## Overview

AviUtl ver.2 uses a text-based project format (.aup2) similar to INI files. This library provides:

- **Parser**: Read .aup2 files into Python objects
- **Serializer**: Write Python objects back to .aup2 format
- **JSON Conversion**: Export/import as JSON for LLM processing
- **Validation**: Timeline collision detection and frame calculations
- **CLI Tool**: Command-line interface for AI agent automation
- **Preset System**: Save and reuse animation/effect combinations
- **Frame Preview**: Render frames to PNG for Vision AI verification
- **Smart Automation**: Auto frame range, layer selection, and media duration detection
- **Live Bridge (experimental)**: Connect to the currently open AviUtl2 project over
  a local Windows named pipe

## What's New in 0.9.5

0.9.5 is a major Live Bridge usability update. New code should start with
`LiveProject`; the lower-level `LiveClient` remains compatible and is now the
documented escape hatch for raw Alias/item access.

- Short operations such as `add_text()`, `add_video()`, `update()`, `find()`,
  `split()`, `trim()`, and native `render()` are available directly on
  `LiveProject`.
- `EditPlan` validates and applies multiple object/media/effect changes as one
  grouped AviUtl2 GUI Undo unit whenever the host accepts the plan.
- `effect("glow", ...)` and 19 other semantic Effect profiles use natural units
  and are shared by Live Bridge and in-memory `.aup2` generation.
- Cursor placement, free-layer selection, revision/operation IDs, compact
  snapshots, media duration, and combined MP4 video/audio routing are handled
  by the high-level API.
- General text overlap is no longer treated as subtitle overlap unless subtitle
  layers and `warn`/`error` policy are explicitly requested.

See the [Agent Quick Start](docs/LIVE_BRIDGE_AGENT_QUICK_START.md), the
[complete API manual](docs/LIVE_BRIDGE_AGENT_API_MANUAL.md), and the
[v0.9.5 release notes](docs/releases/v0.9.5.md).

## Installation

```bash
pip install aviutl2-api
```

## CLI Quick Start

```bash
# Create new project
aviutl2 new project.aup2 --width 1920 --height 1080 --fps 30

# Add objects (frame range is optional - defaults to 60 frames, auto-appends)
aviutl2 add text project.aup2 "Hello World"                    # Auto: frames 0-59
aviutl2 add shape project.aup2 circle --duration 90            # Auto: frames 60-149
aviutl2 add text project.aup2 "Manual" --from 0 --to 90        # Manual: frames 0-90

# View timeline
aviutl2 timeline project.aup2

# Apply preset
aviutl2 preset init                         # Initialize sample presets
aviutl2 preset apply project.aup2 0 fade-in # Apply preset to object

# Preview frame (for Vision AI)
aviutl2 preview project.aup2 --frame 0 -o preview.png
aviutl2 preview project.aup2 --frame 0 -o small.png --max-width 800  # Resized for API

# Add animation
aviutl2 animate project.aup2 0 opacity --start 0 --end 100 --motion smooth

# Add filter
aviutl2 filter add project.aup2 0 blur --strength 10

# Batch edit (regex filtering)
aviutl2 batch project.aup2 --filter-text "Hello.*" --color ff0000  # Change all "Hello" texts to red
aviutl2 batch project.aup2 --filter-layer "1-5" --opacity 50       # Set opacity for layers 1-5

# Fix collisions (auto-resolve layer conflicts)
aviutl2 fix project.aup2                                           # Detect and auto-fix collisions
aviutl2 fix project.aup2 --dry-run                                 # Check only (no changes)
```

## Python API

```python
from aviutl2_api import parse_file, serialize_to_file, to_json

# Load project
project = parse_file("my_project.aup2")

# Access scenes and objects
scene = project.scenes[0]
for obj in scene.objects:
    print(f"Layer {obj.layer}: frames {obj.frame_start}-{obj.frame_end}")

# Save project
serialize_to_file(project, "output.aup2")

# Export as JSON
json_data = to_json(project)
```

### Live Bridge (0.9.5 beta)

`LiveProject` is the standard entry point for short, revision-safe edits of the
project currently open in AviUtl2:

```python
from aviutl2_api.editing import effect
from aviutl2_api.live import LiveProject

with LiveProject.connect(pid=12345) as project:
    title = project.add_text(
        "第一章",
        duration=90,
        y=-200,
        size=64,
        effects=[
            effect("glow", strength=50, color="#FFD966"),
            effect("outline", size_px=4, color="#202040"),
        ],
    )
    title = project.update(
        title.primary,
        x=120,
        scale=110,
    )
    png = project.render(title.primary.midpoint).png
```

Omitted `at` uses the GUI cursor frame; omitted `layer` selects the first
unlocked, collision-free layer from Layer 0. Multiple edits can be validated and
applied as one GUI Undo unit with `EditPlan`:

```python
from aviutl2_api.editing import EditPlan, effect, linear
from aviutl2_api.live import LiveProject

plan = EditPlan(sequence="parallel")
plan.add_video("intro.mp4", key="intro")
plan.add_text(
    "第一章",
    key="title",
    duration=90,
    y=-200,
    size=80,
    effects=[effect("glow", strength=50)],
)
plan.add_shape(
    "star",
    key="star",
    duration=90,
    x=linear(900, -900),
    rotation=linear(0, 360),
)

with LiveProject.connect(pid=12345) as project:
    validation = project.validate(plan)
    if not validation.valid:
        raise RuntimeError(validation.errors)
    result = project.apply(plan)
    title = result.objects["title"].primary
```

The bridge delegates canonical Alias parsing, media loading, edits, and renders
to AviUtl2's official SDK. It is local Windows-only and does not interpret
natural language. `LiveProject.client` is the documented escape hatch for full
Alias, raw item values, manual revisions, and other advanced endpoints. AI
agents should start with
[`docs/LIVE_BRIDGE_AGENT_QUICK_START.md`](docs/LIVE_BRIDGE_AGENT_QUICK_START.md)
and open
[`docs/LIVE_BRIDGE_AGENT_API_MANUAL.md`](docs/LIVE_BRIDGE_AGENT_API_MANUAL.md)
only when a complete reference or advanced operation is needed.

External API access starts disabled in every AviUtl2 process. Open that window's
`設定 > 外部API連携設定...` menu, then check
`このウィンドウの外部API連携を許可` in the independent settings window. It
includes the target PID and reports either:

- `Disabled（接続を受け付けません）`
- `Enabled（接続受付中）`
- `Enabled（起動失敗・ログを確認）`

The window is hidden at startup and is not docked into AviUtl2's editing
workspace. Closing it only hides settings; it does not implicitly change the
current permission.

Permission lasts only for that AviUtl2 process. Disabling it stops that process's
named pipe and removes its discovery entry. If more than one process is enabled,
automatic selection is rejected and the target must be explicit:

```python
from aviutl2_api.live import LiveClient

with LiveClient.connect(pid=12345) as client:
    print(client.get_project_info())
```

#### Advanced: low-level `LiveClient`

Use the following APIs when full Alias data, raw localized item values, explicit
revision handling, or an endpoint not wrapped by `LiveProject` is required.

Existing objects can be edited through revision-scoped snapshots:

```python
with LiveClient.connect(pid=12345) as client:
    snapshot = client.get_snapshot()
    title = snapshot.objects[0]

    client.set_text(title, "更新したタイトル")

    # Every successful edit creates a new revision.
    title = client.get_snapshot().objects[0]
    client.set_position(title, x=120, y=-45)  # X/Y are one Undo unit

    title = client.get_snapshot().objects[0]
    client.move_object(title, layer=2, frame=90)

    title = client.get_snapshot().objects[0]
    client.delete_object(title)
```

If a user changes the project between snapshot and mutation, the bridge returns
`STALE_PROJECT_STATE` instead of guessing the target.

To protect an object from AI/API edits, select it in the AviUtl2 timeline and use
`外部API編集ロック > ロック` in the object context menu. The timeline label is
prefixed with `🔒` while retaining the current text/effect label, and
`snapshot.objects[n].api_locked` becomes `True`.
`set_item(s)`, `move`, and `delete` then fail with `OBJECT_API_LOCKED`. This lock
does not block normal GUI editing; use `外部API編集ロック > 解除` to remove it.
Locking or unlocking changes the project revision, so capture a fresh snapshot
before the next API operation.

Media files can be checked and placed through AviUtl2's own input plugins, and
objects can be inspected without parsing Japanese Alias keys by guesswork:

```python
with LiveClient.connect(pid=12345) as client:
    probe = client.probe_media(r"C:\media\clip.mp4")
    if probe.readable:
        created = client.add_video(
            r"C:\media\clip.mp4",
            layer=2,
            frame=120,
            length=0,  # Let AviUtl2 choose the native duration.
        )
        print(created.frame_start, created.frame_end)

    snapshot = client.get_snapshot()
    details = client.inspect_object(snapshot.objects[0])
    for effect in details.effects:
        print(effect.selector, effect.enabled, effect.locked)
        for item in effect.items:
            print(item.name, item.type, item.raw_value, item.track)

    client.set_property(
        snapshot.objects[0],
        effect="標準描画",
        item="X",
        value=240.0,
    )
```

Plugin/client 0.7 adds host-discovered effects and layers plus practical clip
editing:

```python
with LiveClient.connect(pid=12345) as client:
    catalog = client.get_effect_catalog()
    layers = client.get_layers()
    print(catalog.effects[0].name, layers.layers[0].locked)

    clip = client.get_snapshot().objects[0]
    client.set_playback_rate(clip, 2.0)  # AviUtl2 native 200%; length unchanged

    clip = client.get_snapshot().objects[0]
    split = client.split_media(clip, frame=90)
    print(split.left, split.right)
```

`split_media()` is intentionally conservative: it supports basic video/audio
clips with fixed playback position/speed and refuses animated or multi-section
clips instead of guessing. Effect creation and deletion are available through
`add_effect()` and `delete_effect()`; deletion uses the selector returned by
`inspect_object()`. `duplicate_object()` clones an Alias to an empty destination
and verifies the resulting snapshot.

0.9.2 adds a practical agent workflow without exposing project save/export or
playback controls:

```python
from aviutl2_api.live import (
    EditingSession,
    LiveClient,
    SubtitleLayerPolicy,
)

with LiveClient.connect(pid=12345) as client:
    editing = EditingSession(client)
    report = editing.preflight(audio_range=(0, 299))
    if not report.ready:
        print(report.errors)

    client.add_subtitles(
        "captions.vtt",
        layer_policy=SubtitleLayerPolicy(
            base_layer=10,
            max_layers=3,
        ),
        language="ja",
    )

    snapshot = editing.refresh()
    sheet = client.render_review_contact_sheet(snapshot=snapshot)
    audio = client.render_audio(
        frame_start=0,
        frame_end=299,
        expected_revision=snapshot.revision,
    )
    print(audio.analyze(), len(sheet.png))
```

Frame/contact-sheet/PCM results stay in memory by default. Saving one refuses an
existing destination unless `overwrite=True`. Eight clients can observe the same
enabled AviUtl2 process, but SDK work is FIFO-serialized and mutations are
session-idempotent.

0.9.3 fixes live snapshots for media and other objects that occupy multiple
timeline layers. Such objects are returned once on their base layer instead of
causing `INVALID_HOST_OBJECT_RANGE`.

0.9.4 adds `LiveProject`, backend-neutral `EditPlan`, automatic cursor/layer
placement, compact object search, and mixed `edit.plan.validate/apply`. Native
mixed plans validate their final layout first, run inside one edit section, and
return an explicit best-effort rollback receipt on failure. They intentionally
report `atomic=False` because the current SDK cannot guarantee rollback of every
linked object created by third-party media/effect implementations.

0.9.5 adds a shared semantic Effect API for Live Bridge and `.aup2` models.
Twenty curated profiles use natural units and are checked against the running
host's exact catalog before editing:

```python
from aviutl2_api.editing import effect

title = project.add_text(
    "第一章",
    effects=[
        effect("glow", strength=50, color="#FFD966"),
        effect("outline", size_px=4, color="#202040"),
    ],
)
blur = project.apply_effect(title.primary, effect("blur", radius_px=8))
title_object = project.find(text="第一章").one()
blur = project.update_effect(
    title_object,
    blur,
    effect("blur", radius_px=12),
)
print(project.available_effect_profiles())
```

Native media may be returned as separate video/audio objects or as one combined
`映像再生` object. `effects=` routes both forms; a combined MP4 legitimately
reports the same object ID for video- and audio-domain Effects.

Unknown or third-party effects remain available explicitly through
`native_effect()` or `LiveProject.client`; their meaning is never guessed.
Create-time stacks preserve order, duplicates and enabled state and remain one
GUI Undo unit. A 0.9.4 plugin refuses a create-time stack instead of splitting
it into several Undo operations.

The same Effect specification can be applied to an in-memory `.aup2` model:

```python
from aviutl2_api import apply_effects, validate_standard_effects
from aviutl2_api.editing import effect

apply_effects(
    project_model,
    timeline_object,
    effect("glow", strength=50, color="#FFD966"),
    effect("outline", size_px=4),
)
assert validate_standard_effects(project_model).valid
```

`apply_effects()` does not save a file. It inserts complete versioned templates
after standard drawing/playback Effects, matching AviUtl2's Open/Save canonical
order, and renumbers Effect IDs. The manifest ID remains `2001901`; the
explicit compatibility allow-list accepts generated `2001901` projects and
AviUtl2 Open/Save output upgraded to `2010200`. Unknown future project versions
still fail closed. Disabled standard Effects use AviUtl2's canonical
`effect.disable=1` marker. The manual Open/Save gate is available in
`tests/manual/aup2_effect_roundtrip.py`.

The current official SDK cannot list/create/duplicate/switch scenes or execute
Undo/Redo. Those capabilities are reported as false, calls fail with
`SDK_METHOD_UNAVAILABLE`, and the 1.0 release gate remains closed. See
[`protocol/CAPABILITIES_0.9.5.json`](protocol/CAPABILITIES_0.9.5.json).

Render an exact composite frame with the running AviUtl2 process:

```python
with LiveClient.connect(pid=12345) as client:
    rendered = client.render_frame(
        60,
        output_path="frame-0060.png",
    )
    print(
        rendered.width,
        rendered.height,
        rendered.revision,
        rendered.sha256,
    )
```

This uses AviUtl2's native scene renderer. Pillow/OpenCV are not used to
reconstruct the scene; PNG compression and verified chunk transport happen
after AviUtl2 returns its RGBA output.

For the smallest runnable high-level example, enable exactly one Live Bridge
window and run:

```powershell
python examples/live_add_title.py
```

It adds one title at the GUI cursor, automatically selects an unlocked free
layer, and renders the title's midpoint with AviUtl2's native renderer. The PNG
stays in memory and only its dimensions and SHA-256 are printed. PID selection,
snapshot refresh, revision checks, operation IDs, and the one-command
`EditPlan` are handled by `LiveProject`.

The shooting-star example shows high-level animated shapes and text:

```powershell
python examples/live_shooting_star.py
```

It uses only `LiveProject`, `EditPlan`, `add_shape()`, `add_text()`, and the
localized-name-free `linear(start, end)` transform. Cursor placement, free-layer
selection, Alias generation, revision checks, and one grouped Undo operation are
automatic. The native three-frame contact sheet stays in memory unless
the caller explicitly invokes `sheet.save(...)`.

## CLI Commands

### Project Operations

| Command | Description |
|---------|-------------|
| `new` | Create new project |
| `info` | Show project information |
| `timeline` | Display ASCII timeline |
| `preview` | Render frame to PNG for Vision AI |
| `layers` | List layers |
| `objects` | List objects |
| `search` | Search objects at frame |
| `range` | List objects in frame range |
| `check` | Check if placement is possible |

### Object Operations

| Command | Description |
|---------|-------------|
| `add text` | Add text object |
| `add shape` | Add shape object |
| `add audio` | Add audio file |
| `add video` | Add video file |
| `add image` | Add image file |
| `move` | Move object position |
| `delete` | Delete object |
| `copy` | Duplicate object |
| `modify` | Change object properties |
| `batch` | Batch edit with filters (regex) |
| `fix` | Auto-fix layer collisions |

### Animation & Effects

| Command | Description |
|---------|-------------|
| `animate` | Set animation on property |
| `filter add` | Add filter effect |

### Preset System

| Command | Description |
|---------|-------------|
| `preset list` | List available presets |
| `preset show` | Show preset details |
| `preset apply` | Apply preset to object |
| `preset save` | Save object settings as preset |
| `preset delete` | Delete preset |
| `preset init` | Initialize with sample presets |

### JSON Conversion

| Command | Description |
|---------|-------------|
| `export-json` | Export project to JSON |
| `import-json` | Import project from JSON |

## Frame Preview (Vision AI Integration)

Render project frames to PNG images for verification by Vision-enabled LLMs.

```bash
# Render single frame
aviutl2 preview project.aup2 --frame 0 -o preview.png

# Resize for Vision AI (recommended to avoid API size limits)
aviutl2 preview project.aup2 --frame 0 -o small.png --max-width 800
aviutl2 preview project.aup2 --frame 0 -o small.png --max-height 600
aviutl2 preview project.aup2 --frame 0 -o half.png --scale 0.5

# Render filmstrip (multiple frames in one image)
aviutl2 preview project.aup2 --strip --interval 30 -o timeline.png
```

### Resize Options

| Option | Description |
|--------|-------------|
| `--max-width N` | Limit width to N pixels (maintains aspect ratio) |
| `--max-height N` | Limit height to N pixels (maintains aspect ratio) |
| `--scale X` | Scale factor (e.g., 0.5 for 50% size) |

**Warnings**: The tool automatically warns when:
- Aspect ratio would be changed
- Scale factor is below 50% (text/lines may become hard to read)
- Scale factor is below 25% (details may be lost)

## Sample Presets

17 sample presets are included:

**Animations:**
- `spin-fade-out` - Rotate 10 times while fading out
- `fade-in`, `fade-out` - Opacity transitions
- `slide-in-left`, `slide-in-right`, `slide-out-right` - Slide animations
- `bounce-vertical`, `bounce-horizontal` - Bounce effects
- `zoom-in`, `zoom-out` - Scale animations
- `spin-once` - Single rotation
- `orbit` - Circular motion

**Effects:**
- `shake` - Vibration effect
- `glow-pulse` - Glow effect
- `blur-soft` - Soft blur
- `text-shadow` - Drop shadow for text
- `border-white` - White border

## Development

### Setup

```bash
# Clone and setup
git clone https://github.com/Marble-GP/AviUtl2_API.git
cd AviUtl2_API
python -m venv .venv

# Activate virtual environment
# Linux/macOS/WSL:
source .venv/bin/activate

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# Windows Command Prompt:
.\.venv\Scripts\activate.bat

# Install in editable mode
pip install -e ".[dev]"
```

**Important**: Always activate the virtual environment before running `aviutl2` commands. Your prompt should show `(.venv)` when activated.

If you get a PowerShell execution policy error on Windows:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Testing and Linting

```bash
# Test
pytest

# Type check
mypy src/

# Lint
ruff check src/
```

### Publishing to PyPI

`.github/workflows/publish.yml` builds and validates the Python distributions
and the Windows `.aux2` plugin on GitHub-hosted runners for pull requests and
pushes to `main`. Pushing a version tag publishes the verified Python artifact
to PyPI using a project-scoped API token and creates a GitHub Release containing
`AviUtl2LiveBridge.aux2` and its SHA-256 checksum:

```bash
git tag v0.9.5
git push origin v0.9.5
```

The tag without its leading `v` must exactly match `project.version` in
`pyproject.toml`. Store a project-scoped PyPI API token as the
`PYPI_API_TOKEN` secret in the GitHub `pypi` environment. The publish action
uses PyPI project `aviutl2-api`; its API-token username is the standard
`__token__`, so no account username or alias is stored in the workflow.

An existing version can be recovered without moving its tag by running the
workflow manually and entering the exact `publish_version`. Leaving the input
empty performs validation only and never publishes.

The plugin is compiled and tested entirely on the Actions `windows-2022`
runner, which matches the Visual Studio 2022 CMake preset; a local Visual Studio
installation is not required for publishing. If
`docs/releases/<tag>.md` exists, the workflow uses it as the GitHub Release body;
otherwise it falls back to GitHub-generated notes.

## Documentation

Start here:

- [Live Bridge Agent Quick Start](docs/LIVE_BRIDGE_AGENT_QUICK_START.md) -
  Default `LiveProject`/`EditPlan` workflow for AI agents
- [Live Bridge Agent API Manual](docs/LIVE_BRIDGE_AGENT_API_MANUAL.md) -
  Complete Python API, safety rules, errors, and advanced operations
- [v0.9.5 Release Notes](docs/releases/v0.9.5.md) -
  Upgrade steps, high-level API examples, compatibility, and known constraints

Contributor references:

- [Live Bridge Protocol](docs/LIVE_BRIDGE_PROTOCOL.md) -
  Low-level wire contract for client/plugin implementers
- [Live Bridge Development](docs/LIVE_BRIDGE_DEVELOPMENT.md) -
  Native build, tests, security regression, and manual integration
- [.aup2 Format Notes](docs/aup2_format_specification.md) -
  Observed file format and parser/serializer compatibility boundary

CLI syntax is available from `aviutl2 --help` and each subcommand's `--help`.

## License

MIT
