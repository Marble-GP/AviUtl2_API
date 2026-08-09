"""Safe high-level and lossless low-level APIs for AviUtl2 projects.

Use :class:`LocalProject` for guarded ``.aup2`` editing, :class:`LiveProject`
for one open AviUtl2 window, and :class:`SyncSession` for explicit dual apply.
The legacy parser, mutable model, serializer, JSON conversion, and CLI remain
available as advanced direct-file interfaces.
"""

__version__ = "0.9.6"

from aviutl2_api.aup2_effects import (
    Aup2RoundTripReport,
    StandardEffectValidation,
    apply_effects,
    compare_aup2_roundtrip,
    validate_standard_effects,
)
from aviutl2_api.editing import (
    EditPlan,
    InvalidMediaArgumentsError,
    PlanApplyError,
    PlanValidationError,
    ProjectChangedError,
    effect,
    linear,
    native_effect,
)
from aviutl2_api.effect_profiles import (
    AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS,
    AUP2_EFFECT_MANIFEST_VERSION,
    EffectProfileUnavailableError,
    available_effect_profiles,
    describe_effect_profile,
)
from aviutl2_api.json_converter import (
    JsonConverter,
    from_dict,
    from_json,
    to_dict,
    to_json,
)
from aviutl2_api.live.project import LiveProject
from aviutl2_api.local import (
    LocalCapabilityUnavailableError,
    LocalFileChangedError,
    LocalObject,
    LocalObjectSelection,
    LocalOverwriteRequiredError,
    LocalProject,
    LocalProjectFormatError,
    LocalSnapshot,
    SaveReceipt,
)

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
from aviutl2_api.sync import (
    SyncCapabilityUnavailableError,
    SyncConflictError,
    SyncedObjectSelection,
    SyncPartialApplyError,
    SyncSession,
    SyncStatus,
    SyncValidation,
    SyncValidationError,
)

__all__ = [
    # Version
    "__version__",
    "AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS",
    "AUP2_EFFECT_MANIFEST_VERSION",
    "Aup2RoundTripReport",
    "EffectProfileUnavailableError",
    "StandardEffectValidation",
    "apply_effects",
    "available_effect_profiles",
    "describe_effect_profile",
    "compare_aup2_roundtrip",
    "validate_standard_effects",
    "EditPlan",
    "InvalidMediaArgumentsError",
    "LiveProject",
    "PlanApplyError",
    "PlanValidationError",
    "ProjectChangedError",
    "SyncCapabilityUnavailableError",
    "SyncConflictError",
    "SyncPartialApplyError",
    "SyncSession",
    "SyncStatus",
    "SyncValidation",
    "SyncValidationError",
    "SyncedObjectSelection",
    "effect",
    "linear",
    "native_effect",
    "LocalCapabilityUnavailableError",
    "LocalFileChangedError",
    "LocalObject",
    "LocalObjectSelection",
    "LocalOverwriteRequiredError",
    "LocalProject",
    "LocalProjectFormatError",
    "LocalSnapshot",
    "SaveReceipt",
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
