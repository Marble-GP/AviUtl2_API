#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace aviutl2::live {

inline constexpr std::uint32_t kProtocolVersion = 1;
inline constexpr std::string_view kPluginVersion = "0.9.6";
inline constexpr std::string_view kSdkBaseline = "mirror-2026-07-25";
inline constexpr std::size_t kMaxPayloadBytes = 1024U * 1024U;
inline constexpr std::size_t kMaxJsonDepth = 64U;
inline constexpr std::size_t kMaxRequestIdBytes = 128U;
inline constexpr std::size_t kMaxMethodBytes = 128U;
inline constexpr std::size_t kMaxBatchCommands = 128U;
inline constexpr std::size_t kMaxTimelineCommands = 4096U;
inline constexpr std::size_t kMaxAliasBytes = 256U * 1024U;
inline constexpr std::size_t kMaxClientIdBytes = 128U;
inline constexpr std::size_t kMaxSnapshotAliasBytes = 384U * 1024U;
inline constexpr std::size_t kMaxSnapshotObjects = 4096U;
inline constexpr std::size_t kMaxItemUpdates = 128U;
inline constexpr std::size_t kMaxCreateEffects = 32U;
inline constexpr std::size_t kMaxCreateEffectItems = 128U;
inline constexpr std::size_t kMaxMediaPathCharacters = 32767U;
inline constexpr std::size_t kMaxInspectEffects = 256U;
inline constexpr std::size_t kMaxInspectItems = 4096U;
inline constexpr std::size_t kMaxInspectValueBytes = 512U * 1024U;
inline constexpr std::size_t kMaxCatalogEffects = 4096U;
inline constexpr std::size_t kMaxCatalogPageEffects = 128U;
inline constexpr std::size_t kMaxCatalogItemsPerEffect = 512U;
inline constexpr std::size_t kMaxLayerPageSize = 256U;
inline constexpr std::size_t kFrameChunkBytes = 512U * 1024U;
inline constexpr std::size_t kMaxFramePngBytes = 32U * 1024U * 1024U;
inline constexpr std::size_t kMaxRenderRgbaBytes = 256U * 1024U * 1024U;
inline constexpr std::size_t kMaxCaptureBytes = 64U * 1024U * 1024U;
inline constexpr std::size_t kMaxCaptures = 4U;
inline constexpr std::size_t kAudioChunkBytes = 512U * 1024U;
inline constexpr std::size_t kMaxAudioCaptureBytes =
    128U * 1024U * 1024U;
inline constexpr std::size_t kMaxAudioCaptures = 2U;
inline constexpr int kMaxAudioRenderFrames = 10000;
inline constexpr int kMaxRenderDimension = 16384;
inline constexpr int kRenderTimeoutSeconds = 20;
inline constexpr int kCaptureTtlSeconds = 60;
inline constexpr std::size_t kMaxPipeClients = 8U;
inline constexpr std::size_t kMaxSessionOperations = 256U;
inline constexpr std::size_t kMaxEventJournalEntries = 1024U;
inline constexpr int kMaxEventWatchMilliseconds = 30000;
inline constexpr unsigned long kRequiredHostVersion = 2003300UL;

}  // namespace aviutl2::live
