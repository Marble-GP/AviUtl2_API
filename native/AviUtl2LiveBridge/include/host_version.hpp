#pragma once

#include <cstdint>

namespace aviutl2::live {

// Trailing EDIT_SECTION/EDIT_HANDLE members introduced by the 2026-09-05 SDK
// baseline exist only in hosts new enough to allocate them. A null check
// cannot prove that a trailing slot exists, so every access to those members
// is gated on the host version recorded by InitializePlugin.

// AviUtl2 2.1.3 added the EDIT_SECTION move_effect, get/set_effect_data_value,
// and set_edited_state members and the EDIT_HANDLE object rendering members.
inline constexpr std::uint32_t kHostVersionMoveEffect = 2010300U;

// AviUtl2 2.1.4 extended move_object_section to the start and end points and
// added the frame-mark members and set_palette_info to EDIT_SECTION.
inline constexpr std::uint32_t kHostVersionSectionEndpoints = 2010400U;

// Records the AviUtl2 host version passed to InitializePlugin. Call it before
// any trailing SDK member is read; an unrecorded version stays 0 and keeps
// every gated feature off.
void store_host_version(std::uint32_t version) noexcept;

// Returns the recorded host version, or 0 when InitializePlugin has not run.
[[nodiscard]] std::uint32_t host_version() noexcept;

}  // namespace aviutl2::live
