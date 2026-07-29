#pragma once

#include "json.hpp"

#include <cstdint>
#include <string>
#include <string_view>

namespace aviutl2::live {

struct Request final {
    std::string id;
    std::uint32_t protocol_version = 0;
    std::string method;
    Json::Object params;
};

struct RequestParseResult final {
    bool ok = false;
    Request request;
    std::string error_code;
    std::string error_message;
};

[[nodiscard]] RequestParseResult parse_request(std::string_view payload);
[[nodiscard]] std::string make_success_response(
    std::string_view id,
    Json result);
[[nodiscard]] std::string make_error_response(
    std::string_view id,
    std::string_view code,
    std::string_view message,
    Json::Object details = {},
    bool retryable = false);
[[nodiscard]] std::string encode_frame(std::string_view payload);

}  // namespace aviutl2::live
