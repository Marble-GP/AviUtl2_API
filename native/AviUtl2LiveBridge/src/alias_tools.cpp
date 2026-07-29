#include "alias_tools.hpp"

#include <algorithm>
#include <charconv>
#include <numeric>
#include <stdexcept>

namespace aviutl2::live {

std::string strip_object_alias_frame_range(
    const std::string_view alias) {
    constexpr std::string_view object_header = "[Object]\r\n";
    constexpr std::string_view effect_header = "[Object.0]";
    constexpr std::string_view frame_prefix = "frame=";
    if (!alias.starts_with(object_header)) {
        throw std::runtime_error(
            "media Alias does not start with an Object section");
    }
    const std::size_t section_end = alias.find(effect_header);
    if (section_end == std::string_view::npos) {
        throw std::runtime_error(
            "media Alias does not contain its primary effect");
    }
    std::string normalized(alias);
    std::size_t line_start = object_header.size();
    while (line_start < section_end) {
        const std::size_t line_end =
            alias.find("\r\n", line_start);
        if (line_end == std::string_view::npos ||
            line_end > section_end) {
            break;
        }
        const std::string_view line =
            alias.substr(line_start, line_end - line_start);
        if (line.starts_with(frame_prefix)) {
            normalized.erase(
                line_start,
                line_end + 2U - line_start);
            return normalized;
        }
        line_start = line_end + 2U;
    }
    return normalized;
}

std::string reorder_object_alias_effects(
    const std::string_view alias,
    const std::vector<std::size_t>& order) {
    const std::string normalized =
        strip_object_alias_frame_range(alias);
    constexpr std::string_view first_header = "[Object.0]";
    const std::size_t first = normalized.find(first_header);
    if (first == std::string::npos) {
        throw std::runtime_error(
            "object Alias does not contain an effect section");
    }

    std::vector<std::string_view> bodies;
    std::size_t cursor = first;
    std::size_t expected_index = 0U;
    while (cursor < normalized.size()) {
        if (!std::string_view(normalized).substr(cursor).starts_with(
                "[Object.")) {
            throw std::runtime_error(
                "object Alias contains an invalid effect section");
        }
        const std::size_t number_start =
            cursor + std::string_view("[Object.").size();
        const std::size_t close = normalized.find(']', number_start);
        if (close == std::string::npos) {
            throw std::runtime_error(
                "object Alias contains an invalid effect header");
        }
        std::size_t section_index = 0U;
        const auto parsed = std::from_chars(
            normalized.data() + number_start,
            normalized.data() + close,
            section_index);
        if (parsed.ec != std::errc{} ||
            parsed.ptr != normalized.data() + close ||
            section_index != expected_index) {
            throw std::runtime_error(
                "object Alias effect indices are not contiguous");
        }
        const std::size_t header_end =
            normalized.find("\r\n", close + 1U);
        if (header_end == std::string::npos) {
            throw std::runtime_error(
                "object Alias effect header is not terminated");
        }
        const std::size_t body_start = header_end + 2U;
        const std::size_t next =
            normalized.find("\r\n[Object.", body_start);
        const std::size_t body_end =
            next == std::string::npos
                ? normalized.size()
                : next + 2U;
        bodies.emplace_back(
            normalized.data() + body_start,
            body_end - body_start);
        ++expected_index;
        if (next == std::string::npos) {
            break;
        }
        cursor = next + 2U;
    }

    if (order.size() != bodies.size()) {
        throw std::runtime_error(
            "effect reorder permutation has the wrong size");
    }
    std::vector<std::size_t> sorted = order;
    std::sort(sorted.begin(), sorted.end());
    for (std::size_t index = 0U;
         index < sorted.size();
         ++index) {
        if (sorted[index] != index) {
            throw std::runtime_error(
                "effect reorder indices are not a permutation");
        }
    }

    std::string output(normalized.substr(0U, first));
    for (std::size_t index = 0U; index < order.size(); ++index) {
        output.append("[Object.");
        output.append(std::to_string(index));
        output.append("]\r\n");
        output.append(bodies[order[index]]);
    }
    return output;
}

std::string replace_object_alias_effect_item(
    const std::string_view alias,
    const std::size_t effect_index,
    const std::string_view item,
    const std::string_view value) {
    if (item.empty() ||
        item.find_first_of("=\r\n") != std::string_view::npos ||
        item.find('\0') != std::string_view::npos ||
        value.find_first_of("\r\n") != std::string_view::npos ||
        value.find('\0') != std::string_view::npos) {
        throw std::runtime_error(
            "invalid Alias effect item replacement");
    }
    std::string normalized =
        strip_object_alias_frame_range(alias);
    const std::string header =
        "[Object." + std::to_string(effect_index) + "]\r\n";
    const std::size_t section = normalized.find(header);
    if (section == std::string::npos) {
        throw std::runtime_error(
            "Alias effect section was not found");
    }
    const std::size_t section_start = section + header.size();
    const std::size_t section_end =
        normalized.find("\r\n[Object.", section_start);
    const std::string prefix = std::string(item) + "=";
    std::size_t line_start = section_start;
    while (line_start <
           (section_end == std::string::npos
                ? normalized.size()
                : section_end)) {
        const std::size_t line_end =
            normalized.find("\r\n", line_start);
        const std::size_t effective_end =
            line_end == std::string::npos
                ? normalized.size()
                : line_end;
        if (std::string_view(normalized)
                .substr(
                    line_start,
                    effective_end - line_start)
                .starts_with(prefix)) {
            normalized.replace(
                line_start + prefix.size(),
                effective_end - line_start - prefix.size(),
                value);
            return normalized;
        }
        if (line_end == std::string::npos) {
            break;
        }
        line_start = line_end + 2U;
    }
    throw std::runtime_error(
        "Alias effect item was not found");
}

}  // namespace aviutl2::live
