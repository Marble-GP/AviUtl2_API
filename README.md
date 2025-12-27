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

## Installation

```bash
pip install aviutl2-api
```

## CLI Quick Start

```bash
# Create new project
aviutl2 new project.aup2 --width 1920 --height 1080 --fps 30

# Add objects
aviutl2 add text project.aup2 "Hello World" --from 0 --to 90
aviutl2 add shape project.aup2 circle --from 0 --to 90 --size 100

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

```bash
# Clone and setup
git clone https://github.com/Marble-GP/AviUtl2_API.git
cd AviUtl2_API
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

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

## License

MIT
