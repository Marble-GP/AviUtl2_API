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

### Live Bridge (0.9.2 beta)

The typed Python client and thin `.aux2` plugin add live access without changing
the existing `.aup2` workflow:

```python
from aviutl2_api.live import (
    CreateFromAliasCommand,
    LiveClient,
    make_text_object,
)

with LiveClient.connect() as client:
    print(client.hello())
    print(client.get_project_info())

    title = make_text_object(
        "AviUtl2本体へライブ追加",
        layer=0,
        frame=0,
        length=90,
        size=64,
    )
    command = CreateFromAliasCommand.from_object(
        title,
        client_id="title",
    )
    client.validate_batch([command])
    client.apply_batch([command])  # One AviUtl2 Undo unit
```

Phase 2 serializes the existing Python object models as Alias data, but delegates
the canonical parsing and actual edit to the AviUtl2 SDK. The bridge is local
Windows-only and does not interpret natural language. See
[`docs/LIVE_BRIDGE_DEVELOPMENT.md`](docs/LIVE_BRIDGE_DEVELOPMENT.md) for the native
build and AviUtl2 installation procedure, and
[`docs/LIVE_BRIDGE_PROTOCOL.md`](docs/LIVE_BRIDGE_PROTOCOL.md) for protocol v1.
AI agents should use
[`docs/LIVE_BRIDGE_AGENT_API_MANUAL.md`](docs/LIVE_BRIDGE_AGENT_API_MANUAL.md)
as the complete operational and API reference.
The API lock threat model and adversarial test procedure are documented in
[`docs/LIVE_BRIDGE_SECURITY.md`](docs/LIVE_BRIDGE_SECURITY.md).
The current end-to-end workflow coverage and the remaining requirements for
agent-driven production editing are tracked in
[`docs/LIVE_BRIDGE_AGENT_WORKFLOW_GAPS.md`](docs/LIVE_BRIDGE_AGENT_WORKFLOW_GAPS.md).

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
with LiveClient.connect(pid=12345) as client:
    print(client.get_project_info())
```

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

The current official SDK cannot list/create/duplicate/switch scenes or execute
Undo/Redo. Those capabilities are reported as false, calls fail with
`SDK_METHOD_UNAVAILABLE`, and the 1.0 release gate remains closed. See
[`protocol/CAPABILITIES_0.9.2.json`](protocol/CAPABILITIES_0.9.2.json).

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

For a reproducible end-to-end example, start with six free consecutive
layers and run:

```powershell
python examples/live_shooting_star.py --pid 12345 `
  --output-dir render-tests/shooting-star
```

The example validates placement, creates a 90-frame star and five-part tail
in one grouped Undo operation, and saves native PNG renders for the start,
middle, and end frames. It refuses to overwrite occupied target layers.

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

## Documentation

- [CLI Manual](docs/CLI_MANUAL.md) - Detailed CLI documentation
- [.aup2 Format Specification](docs/aup2_format_specification.md) - File format details
- [Live Bridge Agent API Manual](docs/LIVE_BRIDGE_AGENT_API_MANUAL.md) -
  Complete Python/Wire API and safe agent workflow
- [Live Bridge Protocol](docs/LIVE_BRIDGE_PROTOCOL.md) -
  Protocol framing and implementation contract
- [Live Bridge Security](docs/LIVE_BRIDGE_SECURITY.md) -
  API lock and trust-boundary documentation
- [Live Bridge agent workflow gaps](docs/LIVE_BRIDGE_AGENT_WORKFLOW_GAPS.md) -
  End-to-end production readiness and SDK requests

## License

MIT
