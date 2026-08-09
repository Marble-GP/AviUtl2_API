"""Live connection support for the currently open AviUtl2 project."""

from aviutl2_api.editing import (
    AppliedEffect,
    PlacementConflictError,
    ValidationIssue,
)

from .alias import serialize_object_alias
from .audio import AudioAnalysis, RenderedAudio, analyze_pcm_f32le
from .catalog import (
    CatalogEffect,
    CatalogItem,
    EffectCatalogPage,
    EffectFlags,
)
from .client import LiveClient
from .commands import CreateFromAliasCommand, ItemUpdate, make_text_object
from .discovery import (
    AmbiguousInstanceError,
    InstanceInfo,
    discover_instances,
)
from .editing import (
    CapabilityUnavailableError,
    EditingSession,
    EditingTransactionResult,
    ReviewBundle,
    UndoReceipt,
)
from .effects import (
    EffectApplication,
    EffectSemantic,
    EffectValue,
    apply_common_effect,
)
from .events import BridgeEvent, EventWatchResult, SessionInfo
from .frame import (
    ContactSheet,
    RenderedFrame,
    RenderedPreview,
    make_contact_sheet,
    make_preview,
    review_sample_frames,
)
from .inspection import (
    EffectInspection,
    ItemInspection,
    ObjectInspection,
    TrackInspection,
)
from .layers import LayerInfo, LayerPage
from .media import (
    CreatedMediaObject,
    MediaInventory,
    MediaInventoryItem,
    MediaProbe,
    MediaRelinkReceipt,
    MediaSplit,
    MediaSplitRange,
)
from .project import LiveObject, LiveProject, ObjectSelection
from .protocol import (
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    BridgeRemoteError,
    ProtocolError,
)
from .qc import PreflightIssue, PreflightReport, run_preflight
from .scene import SceneInfo
from .sections import ObjectSection, ObjectSections
from .snapshot import ProjectSnapshot, SnapshotObject
from .subtitles import (
    SubtitleBatchResult,
    SubtitleCue,
    SubtitleLayerPolicy,
    SubtitlePlacement,
    SubtitleStyle,
    load_subtitles,
    parse_srt,
    parse_webvtt,
)
from .timeline import (
    ObjectGroup,
    TimelineTransactionCommand,
    TransactionReceipt,
)

__all__ = [
    "MAX_PAYLOAD_BYTES",
    "PROTOCOL_VERSION",
    "AmbiguousInstanceError",
    "AppliedEffect",
    "AudioAnalysis",
    "BridgeRemoteError",
    "BridgeEvent",
    "CatalogEffect",
    "CatalogItem",
    "CapabilityUnavailableError",
    "ContactSheet",
    "CreatedMediaObject",
    "CreateFromAliasCommand",
    "InstanceInfo",
    "ItemUpdate",
    "EffectInspection",
    "EffectApplication",
    "EffectCatalogPage",
    "EffectFlags",
    "EffectSemantic",
    "EffectValue",
    "EventWatchResult",
    "EditingSession",
    "EditingTransactionResult",
    "ItemInspection",
    "LiveClient",
    "LiveObject",
    "LiveProject",
    "LayerInfo",
    "LayerPage",
    "MediaProbe",
    "MediaInventory",
    "MediaInventoryItem",
    "MediaRelinkReceipt",
    "MediaSplit",
    "MediaSplitRange",
    "ObjectInspection",
    "ObjectSelection",
    "ObjectGroup",
    "ObjectSection",
    "ObjectSections",
    "PlacementConflictError",
    "ProjectSnapshot",
    "PreflightIssue",
    "PreflightReport",
    "ProtocolError",
    "RenderedFrame",
    "RenderedPreview",
    "RenderedAudio",
    "ReviewBundle",
    "SceneInfo",
    "SnapshotObject",
    "SessionInfo",
    "SubtitleBatchResult",
    "SubtitleCue",
    "SubtitleLayerPolicy",
    "SubtitlePlacement",
    "SubtitleStyle",
    "TrackInspection",
    "TimelineTransactionCommand",
    "TransactionReceipt",
    "UndoReceipt",
    "ValidationIssue",
    "discover_instances",
    "analyze_pcm_f32le",
    "apply_common_effect",
    "make_text_object",
    "make_contact_sheet",
    "make_preview",
    "load_subtitles",
    "parse_srt",
    "parse_webvtt",
    "review_sample_frames",
    "run_preflight",
    "serialize_object_alias",
]
