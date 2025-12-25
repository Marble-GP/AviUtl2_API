# AviUtl2 Project API

Python API for manipulating AviUtl ver.2 project files (.aup2).

## Overview

AviUtl ver.2 uses a text-based project format (.aup2) similar to INI files. This library provides:

- **Parser**: Read .aup2 files into Python objects
- **Serializer**: Write Python objects back to .aup2 format
- **JSON Conversion**: Export/import as JSON for LLM processing
- **Validation**: Timeline collision detection and frame calculations

## Installation

```bash
pip install aviutl2-api
```

## Quick Start

```python
from aviutl2_api import AviUtl2Project

# Load project
project = AviUtl2Project.from_file("my_project.aup2")

# Access scenes and objects
scene = project.scenes[0]
for obj in scene.objects:
    print(f"Layer {obj.layer}: frames {obj.frame_start}-{obj.frame_end}")

# Save project
project.to_file("output.aup2")

# Export as JSON
json_data = project.to_json()
```

## Development

```bash
# Setup
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

## License

MIT
