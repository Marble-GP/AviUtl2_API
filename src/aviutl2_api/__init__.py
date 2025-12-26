"""AviUtl2 Project API - Python API for AviUtl ver.2 project files.

This library provides:
- Parser: Read .aup2 files into Python objects
- Serializer: Write Python objects back to .aup2 format
- JSON Conversion: Export/import as JSON for LLM processing

Example usage:
    from aviutl2_api import parse_file, to_json

    # Load a project
    project = parse_file("my_project.aup2")

    # Access scene data
    scene = project.scenes[0]
    print(f"Resolution: {scene.width}x{scene.height} @ {scene.fps}fps")

    # Export as JSON
    json_str = to_json(project)
"""

__version__ = "0.1.0"

# Models
from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    Project,
    PropertyValue,
    Scene,
    StaticValue,
    TimelineObject,
)

# Parser
from aviutl2_api.parser import (
    Aup2ParseError,
    Aup2Parser,
    parse_file,
    parse_string,
)

# Serializer
from aviutl2_api.serializer import (
    Aup2Serializer,
    serialize,
    serialize_to_file,
)

# JSON Converter
from aviutl2_api.json_converter import (
    JsonConverter,
    from_dict,
    from_json,
    to_dict,
    to_json,
)

__all__ = [
    # Version
    "__version__",
    # Models
    "Project",
    "Scene",
    "TimelineObject",
    "Effect",
    "StaticValue",
    "AnimatedValue",
    "AnimationParams",
    "PropertyValue",
    # Parser
    "Aup2Parser",
    "Aup2ParseError",
    "parse_file",
    "parse_string",
    # Serializer
    "Aup2Serializer",
    "serialize",
    "serialize_to_file",
    # JSON
    "JsonConverter",
    "to_json",
    "from_json",
    "to_dict",
    "from_dict",
]
