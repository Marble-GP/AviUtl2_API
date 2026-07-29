#include "protocol.hpp"

#include "bridge_constants.hpp"

#include <limits>
#include <stdexcept>

namespace aviutl2::live {
namespace {

[[nodiscard]] RequestParseResult invalid(
    std::string code,
    std::string message) noexcept {
    RequestParseResult result;
    result.error_code = std::move(code);
    result.error_message = std::move(message);
    return result;
}

}  // namespace

RequestParseResult parse_request(const std::string_view payload) {
    if (payload.empty()) {
        return invalid("INVALID_REQUEST", "Request payload must not be empty.");
    }
    if (payload.size() > kMaxPayloadBytes) {
        return invalid("INVALID_REQUEST", "Request payload exceeds the size limit.");
    }

    try {
        const Json document = parse_json(payload);
        if (!document.is_object()) {
            return invalid("INVALID_REQUEST", "Request root must be a JSON object.");
        }

        const Json* id = document.find("id");
        if (id == nullptr || !id->is_string() || id->as_string().empty() ||
            id->as_string().size() > kMaxRequestIdBytes) {
            return invalid(
                "INVALID_REQUEST",
                "Request id must be a non-empty string within the size limit.");
        }

        const Json* version = document.find("protocol_version");
        if (version == nullptr || !version->is_integer()) {
            return invalid(
                "INVALID_REQUEST",
                "protocol_version must be an integer.");
        }
        const std::int64_t version_number = version->as_integer();
        if (version_number < 0 ||
            version_number > std::numeric_limits<std::uint32_t>::max()) {
            return invalid(
                "INVALID_REQUEST",
                "protocol_version is outside the supported integer range.");
        }

        const Json* method = document.find("method");
        if (method == nullptr || !method->is_string() ||
            method->as_string().empty() ||
            method->as_string().size() > kMaxMethodBytes) {
            return invalid(
                "INVALID_REQUEST",
                "method must be a non-empty string within the size limit.");
        }

        Json::Object params;
        if (const Json* params_value = document.find("params");
            params_value != nullptr) {
            if (!params_value->is_object()) {
                return invalid("INVALID_REQUEST", "params must be a JSON object.");
            }
            params = params_value->as_object();
        }

        RequestParseResult result;
        result.ok = true;
        result.request = Request{
            id->as_string(),
            static_cast<std::uint32_t>(version_number),
            method->as_string(),
            std::move(params),
        };
        return result;
    } catch (const JsonParseError& error) {
        return invalid(
            "INVALID_REQUEST",
            std::string("Invalid JSON at byte ") + std::to_string(error.offset()) +
                ": " + error.what());
    } catch (const std::exception&) {
        return invalid(
            "INVALID_REQUEST",
            "Request could not be parsed safely.");
    }
}

std::string make_success_response(std::string_view id, Json result) {
    Json::Object response{
        {"id", Json(std::string(id))},
        {"ok", Json(true)},
        {"result", std::move(result)},
    };
    return serialize_json(Json(std::move(response)));
}

std::string make_error_response(
    const std::string_view id,
    const std::string_view code,
    const std::string_view message,
    Json::Object details,
    const bool retryable) {
    Json::Object error{
        {"code", Json(std::string(code))},
        {"details", Json(std::move(details))},
        {"message", Json(std::string(message))},
        {"retryable", Json(retryable)},
    };
    Json::Object response{
        {"error", Json(std::move(error))},
        {"id", Json(std::string(id))},
        {"ok", Json(false)},
    };
    return serialize_json(Json(std::move(response)));
}

std::string encode_frame(const std::string_view payload) {
    if (payload.empty() || payload.size() > kMaxPayloadBytes ||
        payload.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw std::length_error("payload length is outside the framing limit");
    }
    const auto length = static_cast<std::uint32_t>(payload.size());
    std::string frame;
    frame.reserve(4U + payload.size());
    frame.push_back(static_cast<char>(length & 0xFFU));
    frame.push_back(static_cast<char>((length >> 8U) & 0xFFU));
    frame.push_back(static_cast<char>((length >> 16U) & 0xFFU));
    frame.push_back(static_cast<char>((length >> 24U) & 0xFFU));
    frame.append(payload);
    return frame;
}

}  // namespace aviutl2::live
