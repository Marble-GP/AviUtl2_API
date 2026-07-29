#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace aviutl2::live {

[[nodiscard]] std::string strip_object_alias_frame_range(
    std::string_view alias);
[[nodiscard]] std::string reorder_object_alias_effects(
    std::string_view alias,
    const std::vector<std::size_t>& order);
[[nodiscard]] std::string replace_object_alias_effect_item(
    std::string_view alias,
    std::size_t effect_index,
    std::string_view item,
    std::string_view value);

}  // namespace aviutl2::live
