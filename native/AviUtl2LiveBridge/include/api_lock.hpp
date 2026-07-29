#pragma once

#include <string_view>

namespace aviutl2::live {

inline constexpr std::wstring_view kApiLockMarker = L"\U0001F512";
inline constexpr std::wstring_view kApiLockCustomPrefix = L"\U0001F512 ";
inline constexpr std::wstring_view kApiLockDerivedPrefix =
    L"\U0001F512\u2009";

[[nodiscard]] inline bool is_api_locked_name(
    const std::wstring_view name) noexcept {
    return name == kApiLockMarker ||
           name.starts_with(kApiLockCustomPrefix) ||
           name.starts_with(kApiLockDerivedPrefix);
}

[[nodiscard]] inline bool is_derived_api_lock_name(
    const std::wstring_view name) noexcept {
    return name == kApiLockMarker ||
           name.starts_with(kApiLockDerivedPrefix);
}

}  // namespace aviutl2::live
