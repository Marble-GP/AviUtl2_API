#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace aviutl2::live {

[[nodiscard]] bool encode_png_rgba(
    int width,
    int height,
    std::span<const std::uint8_t> rgba,
    std::vector<std::uint8_t>& png,
    std::string& error_message) noexcept;

[[nodiscard]] std::string sha256_hex(
    std::span<const std::uint8_t> bytes);

[[nodiscard]] std::string base64_encode(
    std::span<const std::uint8_t> bytes);

}  // namespace aviutl2::live
