#include "command_dispatcher.hpp"

#include "bridge_constants.hpp"
#include "frame_codec.hpp"

#include <windows.h>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <cwctype>
#include <exception>
#include <filesystem>
#include <limits>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <utility>

namespace aviutl2::live {
namespace {

struct CommandParseResult final {
    bool ok = false;
    std::vector<CreateAliasCommand> commands;
    std::string message;
    std::size_t failed_index = 0U;
};

struct ObjectTarget final {
    std::int64_t revision = 0;
    std::size_t index = 0U;
};

struct TargetParseResult final {
    bool ok = false;
    ObjectTarget target;
    std::string message;
};

struct MediaPathParseResult final {
    bool ok = false;
    std::wstring path;
    bool exists = false;
    bool regular_file = false;
    std::string message;
};

[[nodiscard]] CommandParseResult command_error(
    const std::size_t index,
    std::string message) {
    CommandParseResult result;
    result.failed_index = index;
    result.message = std::move(message);
    return result;
}

[[nodiscard]] const Json* find_field(
    const Json::Object& object,
    const std::string_view name) noexcept {
    const auto found = object.find(name);
    return found == object.end() ? nullptr : &found->second;
}

[[nodiscard]] std::wstring utf8_to_wide(
    const std::string_view input) {
    if (input.empty()) {
        return {};
    }
    const int required = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        input.data(),
        static_cast<int>(input.size()),
        nullptr,
        0);
    if (required <= 0) {
        throw std::runtime_error("UTF-8 conversion failed");
    }
    std::wstring output(static_cast<std::size_t>(required), L'\0');
    const int written = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        input.data(),
        static_cast<int>(input.size()),
        output.data(),
        required);
    if (written != required) {
        throw std::runtime_error("UTF-8 conversion failed");
    }
    return output;
}

[[nodiscard]] MediaPathParseResult parse_media_path(
    const Json::Object& params) {
    MediaPathParseResult result;
    const Json* file = find_field(params, "file");
    if (file == nullptr || !file->is_string() ||
        file->as_string().empty() ||
        file->as_string().find('\0') != std::string::npos) {
        result.message = "file must be a non-empty UTF-8 path.";
        return result;
    }
    result.path = utf8_to_wide(file->as_string());
    if (result.path.size() > kMaxMediaPathCharacters) {
        result.message = "file exceeds the Windows path length limit.";
        return result;
    }
    const std::filesystem::path filesystem_path(result.path);
    if (!filesystem_path.is_absolute()) {
        result.message =
            "file must be an absolute path so it is independent of process working directories.";
        return result;
    }
    const DWORD attributes = GetFileAttributesW(result.path.c_str());
    result.exists = attributes != INVALID_FILE_ATTRIBUTES;
    result.regular_file =
        result.exists && (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0U;
    result.ok = true;
    return result;
}

[[nodiscard]] std::wstring normalized_path_key(
    const std::wstring& path) {
    std::wstring key =
        std::filesystem::path(path)
            .lexically_normal()
            .wstring();
    std::transform(
        key.begin(),
        key.end(),
        key.begin(),
        [](const wchar_t value) {
            return static_cast<wchar_t>(
                std::towlower(value));
        });
    return key;
}

[[nodiscard]] TargetParseResult parse_object_target(
    const Json::Object& params) {
    TargetParseResult result;
    const Json* expected = find_field(params, "expected_revision");
    const Json* target = find_field(params, "target");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0) {
        result.message =
            "expected_revision must be a positive integer.";
        return result;
    }
    if (target == nullptr || !target->is_object()) {
        result.message = "target must be a JSON object.";
        return result;
    }
    const Json* object_id =
        find_field(target->as_object(), "object_id");
    if (object_id == nullptr || !object_id->is_string()) {
        result.message = "target.object_id must be a string.";
        return result;
    }

    const std::int64_t revision = expected->as_integer();
    const std::string prefix =
        "obj-" + std::to_string(revision) + "-";
    const std::string& id = object_id->as_string();
    if (!id.starts_with(prefix) || id.size() == prefix.size()) {
        result.message =
            "target.object_id does not belong to expected_revision.";
        return result;
    }
    const std::string_view suffix(id.data() + prefix.size(),
                                  id.size() - prefix.size());
    std::uint64_t index = 0U;
    const auto parsed = std::from_chars(
        suffix.data(),
        suffix.data() + suffix.size(),
        index);
    if (parsed.ec != std::errc{} ||
        parsed.ptr != suffix.data() + suffix.size() ||
        index > std::numeric_limits<std::size_t>::max()) {
        result.message = "target.object_id has an invalid object index.";
        return result;
    }
    result.ok = true;
    result.target = ObjectTarget{
        revision,
        static_cast<std::size_t>(index),
    };
    return result;
}

[[nodiscard]] bool valid_item_name(
    const std::string& value) noexcept {
    return !value.empty() && value.size() <= 256U &&
           value.find('\0') == std::string::npos &&
           value.find('\r') == std::string::npos &&
           value.find('\n') == std::string::npos;
}

[[nodiscard]] Json::Object mutation_error_details(
    const ObjectMutationResult& result) {
    Json::Object details{
        {"applied_count",
         Json(static_cast<std::int64_t>(result.applied_count))},
    };
    if (result.current_revision >= 0) {
        details.emplace(
            "current_revision",
            Json(result.current_revision));
    }
    if (result.applied_count > 0U) {
        details.emplace("undo_grouped", Json(true));
    }
    return details;
}

[[nodiscard]] std::string mutation_response(
    const std::string_view request_id,
    const ObjectMutationResult& result,
    const std::optional<std::size_t>
        updated_object_index = std::nullopt) {
    if (!result.ok) {
        return make_error_response(
            request_id,
            result.error_code,
            result.error_message,
            mutation_error_details(result),
            result.retryable);
    }
    Json::Object response{
        {"applied_count",
         Json(static_cast<std::int64_t>(result.applied_count))},
        {"snapshot_required", Json(result.current_revision < 0)},
        {"updated_object", Json(nullptr)},
        {"undo_unit", Json("single_edit_section")},
        {"undo_grouped", Json(true)},
        {"warnings", Json(Json::Array{})},
    };
    if (result.current_revision >= 0) {
        response.emplace(
            "revision",
            Json(result.current_revision));
        if (updated_object_index.has_value()) {
            response["updated_object"] =
                Json(Json::Object{
                    {"object_id",
                     Json("obj-" +
                          std::to_string(
                              result.current_revision) +
                          "-" +
                          std::to_string(
                              *updated_object_index))},
                    {"revision",
                     Json(result.current_revision)},
                });
        }
    } else {
        response.emplace("revision", Json(nullptr));
    }
    return make_success_response(
        request_id,
        Json(std::move(response)));
}

[[nodiscard]] std::string timeline_transaction_response(
    const std::string_view request_id,
    const TimelineTransactionResult& result,
    const bool apply) {
    if (!result.ok) {
        Json::Object details;
        if (result.current_revision >= 0) {
            details.emplace(
                "current_revision",
                Json(result.current_revision));
        }
        if (result.failed_command_index !=
            std::numeric_limits<std::size_t>::max()) {
            details.emplace(
                "failed_command_index",
                Json(static_cast<std::int64_t>(
                    result.failed_command_index)));
        }
        if (result.has_collision) {
            details.emplace(
                "collision",
                Json(Json::Object{
                    {"end", Json(result.collision_end)},
                    {"layer", Json(result.collision_layer)},
                    {"start", Json(result.collision_start)},
                }));
        }
        return make_error_response(
            request_id,
            result.error_code,
            result.error_message,
            std::move(details),
            result.retryable);
    }
    return make_success_response(
        request_id,
        Json(Json::Object{
            {"applied_count",
             Json(static_cast<std::int64_t>(
                 result.applied_count))},
            {"revision",
             result.current_revision >= 0
                 ? Json(result.current_revision)
                 : Json(nullptr)},
            {"snapshot_required",
             Json(apply && result.current_revision < 0)},
            {"undo_unit",
             apply
                 ? Json("single_edit_section")
                 : Json(nullptr)},
            {"undo_grouped", Json(apply)},
            {"valid", Json(result.valid)},
            {"warnings", Json(Json::Array{})},
        }));
}

[[nodiscard]] std::string_view effect_item_type_name(
    const int type) noexcept {
    switch (type) {
        case 1:
            return "integer";
        case 2:
            return "number";
        case 3:
            return "check";
        case 4:
            return "text";
        case 5:
            return "string";
        case 6:
            return "file";
        case 7:
            return "color";
        case 8:
            return "select";
        case 9:
            return "scene";
        case 10:
            return "range";
        case 11:
            return "combo";
        case 12:
            return "mask";
        case 13:
            return "font";
        case 14:
            return "figure";
        case 15:
            return "data";
        case 16:
            return "folder";
        default:
            return "unknown";
    }
}

[[nodiscard]] std::string_view effect_type_name(
    const int type) noexcept {
    switch (type) {
        case 1:
            return "filter";
        case 2:
            return "input";
        case 3:
            return "transition";
        case 4:
            return "control";
        case 5:
            return "output";
        default:
            return "unknown";
    }
}

[[nodiscard]] CommandParseResult parse_create_commands(
    const Json::Array& values,
    const bool require_op) {
    if (values.empty()) {
        return command_error(0U, "commands must contain at least one command.");
    }
    if (values.size() > kMaxBatchCommands) {
        return command_error(
            kMaxBatchCommands,
            "commands exceeds the Phase 2 batch size limit.");
    }

    CommandParseResult result;
    result.commands.reserve(values.size());
    std::set<std::string, std::less<>> client_ids;
    for (std::size_t index = 0U; index < values.size(); ++index) {
        const Json& value = values[index];
        if (!value.is_object()) {
            return command_error(index, "Each command must be a JSON object.");
        }
        const Json::Object& object = value.as_object();
        if (require_op) {
            const Json* operation = find_field(object, "op");
            if (operation == nullptr || !operation->is_string() ||
                operation->as_string() != "object.create_from_alias") {
                return command_error(
                    index,
                    "op must be object.create_from_alias.");
            }
        }

        std::string client_id;
        if (const Json* id = find_field(object, "client_id");
            id != nullptr) {
            if (!id->is_string() || id->as_string().empty() ||
                id->as_string().size() > kMaxClientIdBytes) {
                return command_error(
                    index,
                    "client_id must be a non-empty string within the size limit.");
            }
            client_id = id->as_string();
            if (!client_ids.insert(client_id).second) {
                return command_error(
                    index,
                    "client_id must be unique within a batch.");
            }
        }

        const Json* alias = find_field(object, "alias");
        if (alias == nullptr || !alias->is_string() ||
            alias->as_string().empty() ||
            alias->as_string().size() > kMaxAliasBytes) {
            return command_error(
                index,
                "alias must be a non-empty UTF-8 string within the size limit.");
        }
        if (alias->as_string().find('\0') != std::string::npos) {
            return command_error(index, "alias must not contain NUL bytes.");
        }
        if (alias->as_string().find("[Object]") == std::string::npos ||
            alias->as_string().find("effect.name=") == std::string::npos) {
            return command_error(
                index,
                "alias must contain [Object] and at least one effect.name entry.");
        }

        const Json* layer = find_field(object, "layer");
        const Json* frame = find_field(object, "frame");
        const Json* length = find_field(object, "length");
        if (layer == nullptr || !layer->is_integer() ||
            frame == nullptr || !frame->is_integer() ||
            length == nullptr || !length->is_integer()) {
            return command_error(
                index,
                "layer, frame, and length must be integers.");
        }
        const std::int64_t layer_value = layer->as_integer();
        const std::int64_t frame_value = frame->as_integer();
        const std::int64_t length_value = length->as_integer();
        constexpr std::int64_t max_int =
            std::numeric_limits<int>::max();
        if (layer_value < 0 || layer_value > max_int) {
            return command_error(index, "layer is outside the supported range.");
        }
        if (frame_value < 0 || frame_value > max_int) {
            return command_error(index, "frame is outside the supported range.");
        }
        if (length_value < 1 || length_value > max_int ||
            frame_value > max_int - length_value + 1) {
            return command_error(
                index,
                "length is outside the supported range or overflows the frame range.");
        }

        result.commands.push_back(CreateAliasCommand{
            std::move(client_id),
            alias->as_string(),
            static_cast<int>(layer_value),
            static_cast<int>(frame_value),
            static_cast<int>(length_value),
        });
    }
    result.ok = true;
    return result;
}

[[nodiscard]] Json::Object batch_error_details(
    const BatchEditResult& result) {
    Json::Object details{
        {"applied_count",
         Json(static_cast<std::int64_t>(result.applied_count))},
    };
    if (result.failed_command_index !=
        std::numeric_limits<std::size_t>::max()) {
        details.emplace(
            "failed_command_index",
            Json(static_cast<std::int64_t>(result.failed_command_index)));
    }
    if (result.has_collision) {
        details.emplace(
            "collision",
            Json(Json::Object{
                {"end", Json(result.collision_end)},
                {"layer", Json(result.collision_layer)},
                {"start", Json(result.collision_start)},
            }));
    }
    if (result.applied_count > 0U) {
        details.emplace("undo_grouped", Json(true));
    }
    return details;
}

}  // namespace

CommandDispatcher::CommandDispatcher(
    SdkAdapter& sdk,
    const std::uint32_t pid) noexcept
    : sdk_(sdk), pid_(pid) {}

std::string CommandDispatcher::handle_payload(const std::string_view payload) {
    return handle_payload(0U, payload);
}

std::string CommandDispatcher::handle_payload(
    const ConnectionId connection_id,
    const std::string_view payload) {
    const RequestParseResult parsed = parse_request(payload);
    if (!parsed.ok) {
        return make_error_response(
            "",
            parsed.error_code,
            parsed.error_message);
    }
    return dispatch_for_connection(connection_id, parsed.request);
}

void CommandDispatcher::start() noexcept {
    sdk_.set_stopping(false);
    captures_.clear();
    capture_bytes_ = 0U;
    audio_captures_.clear();
    audio_capture_bytes_ = 0U;
    {
        std::scoped_lock lock(scheduler_mutex_);
        scheduler_stopping_ = false;
        next_ticket_ = 0U;
        serving_ticket_ = 0U;
    }
    {
        std::scoped_lock lock(events_mutex_);
        events_stopping_ = false;
        events_.clear();
        next_event_sequence_ = 1U;
    }
}

void CommandDispatcher::stop() noexcept {
    sdk_.set_stopping(true);
    {
        std::scoped_lock lock(scheduler_mutex_);
        scheduler_stopping_ = true;
    }
    scheduler_cv_.notify_all();
    {
        std::scoped_lock lock(events_mutex_);
        events_stopping_ = true;
    }
    events_cv_.notify_all();
}

void CommandDispatcher::finish_stop() noexcept {
    captures_.clear();
    capture_bytes_ = 0U;
    audio_captures_.clear();
    audio_capture_bytes_ = 0U;
    std::scoped_lock lock(sessions_mutex_);
    sessions_.clear();
}

void CommandDispatcher::close_connection(
    const ConnectionId connection_id) noexcept {
    std::scoped_lock lock(sessions_mutex_);
    sessions_.erase(connection_id);
}

void CommandDispatcher::record_event(
    const std::string_view event_type) noexcept {
    try {
        const auto now = std::chrono::system_clock::now();
        const auto timestamp_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                now.time_since_epoch())
                .count();
        {
            std::scoped_lock lock(events_mutex_);
            if (events_stopping_) {
                return;
            }
            events_.push_back(EventEntry{
                next_event_sequence_++,
                timestamp_ms,
                std::string(event_type),
            });
            while (events_.size() > kMaxEventJournalEntries) {
                events_.pop_front();
            }
        }
        events_cv_.notify_all();
    } catch (...) {
        // Host event callbacks must never allow an allocation failure to
        // escape into AviUtl2.
    }
}

bool CommandDispatcher::is_mutation_method(
    const std::string_view method) noexcept {
    static constexpr std::string_view methods[]{
        "batch.apply",
        "edit.plan.apply",
        "layer.update",
        "media.relink",
        "media.trim",
        "object.create_from_alias",
        "object.create_from_media_file",
        "object.delete",
        "object.effect.add",
        "object.effect.delete",
        "object.effect.reorder",
        "object.effect.set_enabled",
        "object.move",
        "object.section.create",
        "object.section.delete",
        "object.section.move",
        "object.section.set_check",
        "object.section.set_track",
        "object.set_duration",
        "object.set_item",
        "object.set_items",
        "object.set_name",
        "object.split_media",
        "scene.create",
        "scene.duplicate",
        "scene.switch",
        "scene.update_current",
        "timeline.close_gap",
        "timeline.ripple_delete",
        "timeline.ripple_insert",
        "timeline.shift_after",
        "timeline.transaction.apply",
    };
    return std::find(
               std::begin(methods),
               std::end(methods),
               method) != std::end(methods);
}

std::string CommandDispatcher::operation_fingerprint(
    const Request& request) {
    return serialize_json(Json(Json::Object{
        {"method", Json(request.method)},
        {"params", Json(request.params)},
        {"protocol_version",
         Json(static_cast<std::int64_t>(request.protocol_version))},
    }));
}

std::string CommandDispatcher::response_with_request_id(
    const std::string_view response,
    const std::string_view request_id) {
    Json document = parse_json(response);
    if (!document.is_object()) {
        throw std::runtime_error("cached response is not an object");
    }
    document.as_object()["id"] = Json(std::string(request_id));
    return serialize_json(document);
}

CommandDispatcher::SessionState&
CommandDispatcher::ensure_session_locked(
    const ConnectionId connection_id) {
    auto [iterator, inserted] =
        sessions_.try_emplace(connection_id);
    if (inserted) {
        iterator->second.session_id =
            "session-" + std::to_string(pid_) + "-" +
            std::to_string(connection_id);
        iterator->second.client_name = "implicit-v1-client";
    }
    return iterator->second;
}

std::optional<std::string>
CommandDispatcher::cached_operation_response(
    const ConnectionId connection_id,
    const Request& request,
    const std::string_view operation_id,
    const std::string_view fingerprint) {
    std::scoped_lock lock(sessions_mutex_);
    SessionState& session = ensure_session_locked(connection_id);
    const auto found =
        session.operations.find(std::string(operation_id));
    if (found == session.operations.end()) {
        return std::nullopt;
    }
    if (found->second.fingerprint != fingerprint) {
        return make_error_response(
            request.id,
            "OPERATION_ID_REUSED",
            "operation_id was already used with a different mutation payload.",
            Json::Object{
                {"operation_id", Json(std::string(operation_id))},
                {"session_id", Json(session.session_id)},
            });
    }
    return response_with_request_id(
        found->second.response,
        request.id);
}

void CommandDispatcher::cache_operation_response(
    const ConnectionId connection_id,
    std::string operation_id,
    std::string fingerprint,
    std::string response) {
    std::scoped_lock lock(sessions_mutex_);
    SessionState& session = ensure_session_locked(connection_id);
    if (session.operations.contains(operation_id)) {
        return;
    }
    session.operation_order.push_back(operation_id);
    session.operations.emplace(
        std::move(operation_id),
        CachedOperation{
            std::move(fingerprint),
            std::move(response),
        });
    while (session.operation_order.size() >
           kMaxSessionOperations) {
        session.operations.erase(session.operation_order.front());
        session.operation_order.pop_front();
    }
}

std::string CommandDispatcher::handle_session_open(
    const ConnectionId connection_id,
    const Request& request) {
    std::string client_name = "anonymous";
    if (const Json* value =
            find_field(request.params, "client_name");
        value != nullptr) {
        if (!value->is_string() || value->as_string().empty() ||
            value->as_string().size() > kMaxClientIdBytes ||
            value->as_string().find('\0') != std::string::npos) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "client_name must be a non-empty string within the size limit.");
        }
        client_name = value->as_string();
    }
    std::scoped_lock lock(sessions_mutex_);
    SessionState& session = ensure_session_locked(connection_id);
    session.client_name = std::move(client_name);
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"client_name", Json(session.client_name)},
            {"connection_id",
             Json(static_cast<std::int64_t>(connection_id))},
            {"max_cached_operations",
             Json(static_cast<std::int64_t>(
                 kMaxSessionOperations))},
            {"session_id", Json(session.session_id)},
        }));
}

std::string CommandDispatcher::handle_event_watch(
    const Request& request) {
    std::int64_t after_sequence = 0;
    std::int64_t timeout_ms = kMaxEventWatchMilliseconds;
    if (const Json* value =
            find_field(request.params, "after_sequence");
        value != nullptr) {
        if (!value->is_integer() || value->as_integer() < 0) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "after_sequence must be a non-negative integer.");
        }
        after_sequence = value->as_integer();
    }
    if (const Json* value =
            find_field(request.params, "timeout_ms");
        value != nullptr) {
        if (!value->is_integer() || value->as_integer() < 0 ||
            value->as_integer() > kMaxEventWatchMilliseconds) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "timeout_ms must be between 0 and the advertised maximum.");
        }
        timeout_ms = value->as_integer();
    }

    std::set<std::string, std::less<>> requested_types;
    if (const Json* value = find_field(request.params, "types");
        value != nullptr) {
        if (!value->is_array() || value->as_array().size() > 16U) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "types must be an array containing at most 16 event names.");
        }
        for (const Json& item : value->as_array()) {
            if (!item.is_string() || item.as_string().empty() ||
                item.as_string().size() > 64U) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "Every event type must be a non-empty short string.");
            }
            requested_types.insert(item.as_string());
        }
    }

    const auto is_requested =
        [&requested_types](const EventEntry& event) {
            return requested_types.empty() ||
                   requested_types.contains(event.type);
        };
    const auto has_match =
        [&](const std::deque<EventEntry>& journal) {
            return std::any_of(
                journal.begin(),
                journal.end(),
                [&](const EventEntry& event) {
                    return event.sequence >
                               static_cast<std::uint64_t>(
                                   after_sequence) &&
                           is_requested(event);
                });
        };

    std::unique_lock lock(events_mutex_);
    auto resync_required = [&]() {
        return !events_.empty() && after_sequence > 0 &&
               static_cast<std::uint64_t>(after_sequence) + 1U <
                   events_.front().sequence;
    };
    bool matched = has_match(events_);
    if (!events_stopping_ && !matched &&
        !resync_required() && timeout_ms > 0) {
        events_cv_.wait_for(
            lock,
            std::chrono::milliseconds(timeout_ms),
            [&] {
                return events_stopping_ ||
                       resync_required() ||
                       has_match(events_);
            });
        matched = has_match(events_);
    }
    if (events_stopping_) {
        return make_error_response(
            request.id,
            "BRIDGE_STOPPING",
            "The bridge is stopping.",
            {},
            true);
    }

    const bool overflow = resync_required();
    Json::Array result_events;
    result_events.reserve(
        (std::min)(events_.size(), std::size_t{256U}));
    for (const EventEntry& event : events_) {
        if (event.sequence <=
                static_cast<std::uint64_t>(after_sequence) ||
            !is_requested(event)) {
            continue;
        }
        result_events.emplace_back(Json(Json::Object{
            {"sequence",
             Json(static_cast<std::int64_t>(event.sequence))},
            {"timestamp_ms", Json(event.timestamp_ms)},
            {"type", Json(event.type)},
        }));
        if (result_events.size() == 256U) {
            break;
        }
    }
    const std::uint64_t latest_sequence =
        next_event_sequence_ > 1U
            ? next_event_sequence_ - 1U
            : 0U;
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"events", Json(std::move(result_events))},
            {"latest_sequence",
             Json(static_cast<std::int64_t>(
                 latest_sequence))},
            {"resync_required", Json(overflow)},
            {"timed_out", Json(!matched && !overflow)},
        }));
}

std::string CommandDispatcher::dispatch_serialized(
    const Request& request) {
    std::uint64_t ticket = 0U;
    {
        std::unique_lock lock(scheduler_mutex_);
        if (scheduler_stopping_) {
            return make_error_response(
                request.id,
                "BRIDGE_STOPPING",
                "The bridge is stopping.",
                {},
                true);
        }
        ticket = next_ticket_++;
        scheduler_cv_.wait(
            lock,
            [&] {
                return scheduler_stopping_ ||
                       serving_ticket_ == ticket;
            });
        if (scheduler_stopping_) {
            return make_error_response(
                request.id,
                "BRIDGE_STOPPING",
                "The bridge is stopping.",
                {},
                true);
        }
    }

    std::string response;
    try {
        response = dispatch(request);
    } catch (...) {
        response = make_error_response(
            request.id,
            "INTERNAL_PLUGIN_ERROR",
            "The request failed inside the bridge.");
    }
    {
        std::scoped_lock lock(scheduler_mutex_);
        ++serving_ticket_;
    }
    scheduler_cv_.notify_all();
    return response;
}

std::string CommandDispatcher::dispatch_for_connection(
    const ConnectionId connection_id,
    const Request& request) {
    if (request.protocol_version != kProtocolVersion) {
        return dispatch(request);
    }
    if (request.method == "session.open") {
        return handle_session_open(connection_id, request);
    }
    if (request.method == "event.watch") {
        return handle_event_watch(request);
    }

    std::string operation_id;
    std::string fingerprint;
    if (is_mutation_method(request.method)) {
        if (const Json* value =
                find_field(request.params, "operation_id");
            value != nullptr) {
            if (!value->is_string() || value->as_string().empty() ||
                value->as_string().size() > kMaxRequestIdBytes) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "operation_id must be a non-empty string within the size limit.");
            }
            operation_id = value->as_string();
            fingerprint = operation_fingerprint(request);
            if (const auto cached = cached_operation_response(
                    connection_id,
                    request,
                    operation_id,
                    fingerprint);
                cached.has_value()) {
                return *cached;
            }
        }
    }

    std::string response = dispatch_serialized(request);
    if (!operation_id.empty()) {
        cache_operation_response(
            connection_id,
            std::move(operation_id),
            std::move(fingerprint),
            response);
    }
    return response;
}

std::string CommandDispatcher::dispatch(const Request& request) {
    try {
        if (request.protocol_version != kProtocolVersion) {
            return make_error_response(
                request.id,
                "PROTOCOL_VERSION_UNSUPPORTED",
                "The requested protocol version is not supported.",
                Json::Object{
                    {"requested", Json(static_cast<std::int64_t>(
                                      request.protocol_version))},
                    {"supported", Json(static_cast<std::int64_t>(kProtocolVersion))},
                });
        }
        if (request.method == "system.hello") {
            return make_success_response(request.id, hello_result());
        }
        if (request.method == "system.ping") {
            return make_success_response(
                request.id,
                Json(Json::Object{{"pong", Json(true)}}));
        }
        if (request.method == "system.get_capabilities") {
            return make_success_response(request.id, capabilities_result());
        }
        if (request.method == "scene.get_current") {
            return handle_scene(request, false);
        }
        if (request.method == "scene.update_current") {
            return handle_scene(request, true);
        }
        if (request.method == "scene.list" ||
            request.method == "scene.create" ||
            request.method == "scene.duplicate" ||
            request.method == "scene.switch" ||
            request.method == "history.undo" ||
            request.method == "history.redo") {
            return make_error_response(
                request.id,
                "SDK_METHOD_UNAVAILABLE",
                "The official AviUtl2 SDK does not expose this operation in the current baseline.",
                Json::Object{
                    {"release_gate", Json(true)},
                });
        }
        if (request.method == "project.get_info") {
            ProjectInfoResult project = sdk_.get_project_info();
            if (!project.ok) {
                return make_error_response(
                    request.id,
                    project.error_code,
                    project.error_message,
                    {},
                    project.retryable);
            }
            const ProjectInfo& info = project.info;
            Json::Object result{
                {"cursor",
                 Json(Json::Object{
                     {"frame", Json(info.cursor_frame)},
                     {"layer", Json(info.cursor_layer)},
                 })},
                {"edit_state", Json(edit_state_name(sdk_.get_edit_state()))},
                {"frame_max", Json(info.frame_max)},
                {"frame_rate",
                 Json(Json::Object{
                     {"rate", Json(info.rate)},
                     {"scale", Json(info.scale)},
                 })},
                {"height", Json(info.height)},
                {"layer_max", Json(info.layer_max)},
                {"project_file_path",
                 info.project_file_path.empty()
                     ? Json(nullptr)
                     : Json(info.project_file_path)},
                {"sample_rate", Json(info.sample_rate)},
                {"scene_id", Json(info.scene_id)},
                {"width", Json(info.width)},
            };
            return make_success_response(request.id, Json(std::move(result)));
        }
        if (request.method == "effect.catalog") {
            return handle_effect_catalog(request);
        }
        if (request.method == "font.catalog") {
            return handle_runtime_catalog(request, "font");
        }
        if (request.method == "palette.catalog") {
            return handle_runtime_catalog(request, "palette");
        }
        if (request.method == "module.catalog") {
            return handle_runtime_catalog(request, "module");
        }
        if (request.method == "project.get_layers") {
            return handle_layers(request);
        }
        if (request.method == "layer.update") {
            return handle_layer_update(request);
        }
        if (request.method == "project.get_snapshot") {
            return handle_snapshot(request);
        }
        if (request.method == "media.probe") {
            return handle_media_probe(request);
        }
        if (request.method == "media.inventory") {
            return handle_media_inventory(request);
        }
        if (request.method == "media.relink") {
            return handle_media_relink(request);
        }
        if (request.method == "object.create_from_alias") {
            return handle_create_from_alias(request);
        }
        if (request.method == "object.create_from_media_file") {
            return handle_create_from_media(request);
        }
        if (request.method == "object.inspect") {
            return handle_inspect(request);
        }
        if (request.method == "object.effect.add") {
            return handle_effect_mutation(request, true);
        }
        if (request.method == "object.effect.delete") {
            return handle_effect_mutation(request, false);
        }
        if (request.method == "object.effect.set_enabled") {
            return handle_effect_enabled(request);
        }
        if (request.method == "object.effect.reorder") {
            return handle_structural_edit(request, "reorder");
        }
        if (request.method == "object.section.list") {
            return handle_sections(request, "list");
        }
        if (request.method == "object.section.create") {
            return handle_sections(request, "create");
        }
        if (request.method == "object.section.delete") {
            return handle_sections(request, "delete");
        }
        if (request.method == "object.section.move") {
            return handle_sections(request, "move");
        }
        if (request.method == "object.split_media") {
            return handle_split_media(request);
        }
        if (request.method == "object.set_duration") {
            return handle_structural_edit(request, "duration");
        }
        if (request.method == "media.trim") {
            return handle_structural_edit(request, "trim");
        }
        if (request.method == "timeline.transaction.validate") {
            return handle_timeline_transaction(request, false);
        }
        if (request.method == "timeline.transaction.apply") {
            return handle_timeline_transaction(request, true);
        }
        if (request.method == "edit.plan.validate") {
            return handle_edit_plan(request, false);
        }
        if (request.method == "edit.plan.apply") {
            return handle_edit_plan(request, true);
        }
        if (request.method == "timeline.shift_after") {
            return handle_timeline_shift(request, "shift_after");
        }
        if (request.method == "timeline.ripple_insert") {
            return handle_timeline_shift(request, "ripple_insert");
        }
        if (request.method == "timeline.ripple_delete") {
            return handle_timeline_shift(request, "ripple_delete");
        }
        if (request.method == "timeline.close_gap") {
            return handle_timeline_shift(request, "close_gap");
        }
        if (request.method == "frame.render") {
            return handle_frame_render(request);
        }
        if (request.method == "frame.read_chunk") {
            return handle_frame_read_chunk(request);
        }
        if (request.method == "frame.release") {
            return handle_frame_release(request);
        }
        if (request.method == "audio.render") {
            return handle_audio_render(request);
        }
        if (request.method == "audio.read_chunk") {
            return handle_audio_read_chunk(request);
        }
        if (request.method == "audio.release") {
            return handle_audio_release(request);
        }
        if (request.method == "object.set_item") {
            return handle_set_items(request, true);
        }
        if (request.method == "object.set_items") {
            return handle_set_items(request, false);
        }
        if (request.method == "object.set_name") {
            return handle_set_name(request);
        }
        if (request.method == "object.move") {
            return handle_move(request);
        }
        if (request.method == "object.delete") {
            return handle_delete(request);
        }
        if (request.method == "batch.validate") {
            return handle_batch(request, false);
        }
        if (request.method == "batch.apply") {
            return handle_batch(request, true);
        }
        return make_error_response(
            request.id,
            "METHOD_NOT_FOUND",
            "The requested method is not available.");
    } catch (const std::exception&) {
        return make_error_response(
            request.id,
            "INTERNAL_PLUGIN_ERROR",
            "The request failed inside the bridge.");
    } catch (...) {
        return make_error_response(
            request.id,
            "INTERNAL_PLUGIN_ERROR",
            "The request failed inside the bridge.");
    }
}

Json CommandDispatcher::hello_result() const {
    Json::Object result{
        {"edit_state", Json(edit_state_name(sdk_.get_edit_state()))},
        {"pid", Json(static_cast<std::int64_t>(pid_))},
        {"plugin_version", Json(std::string(kPluginVersion))},
        {"protocol_version", Json(static_cast<std::int64_t>(kProtocolVersion))},
        {"sdk_baseline", Json(std::string(kSdkBaseline))},
    };
    return Json(std::move(result));
}

Json CommandDispatcher::capabilities_result() const {
    Json::Array methods{
        Json("system.hello"),
        Json("system.ping"),
        Json("system.get_capabilities"),
        Json("session.open"),
        Json("event.watch"),
        Json("scene.get_current"),
        Json("scene.update_current"),
        Json("effect.catalog"),
        Json("edit.plan.apply"),
        Json("edit.plan.validate"),
        Json("font.catalog"),
        Json("palette.catalog"),
        Json("module.catalog"),
        Json("project.get_info"),
        Json("project.get_layers"),
        Json("project.get_snapshot"),
        Json("layer.update"),
        Json("media.probe"),
        Json("media.inventory"),
        Json("media.relink"),
        Json("object.create_from_alias"),
        Json("object.create_from_media_file"),
        Json("object.effect.add"),
        Json("object.effect.delete"),
        Json("object.effect.set_enabled"),
        Json("object.effect.reorder"),
        Json("object.inspect"),
        Json("object.section.list"),
        Json("object.section.create"),
        Json("object.section.delete"),
        Json("object.section.move"),
        Json("frame.render"),
        Json("frame.read_chunk"),
        Json("frame.release"),
        Json("audio.render"),
        Json("audio.read_chunk"),
        Json("audio.release"),
        Json("object.set_item"),
        Json("object.set_items"),
        Json("object.set_name"),
        Json("object.split_media"),
        Json("object.set_duration"),
        Json("media.trim"),
        Json("timeline.transaction.validate"),
        Json("timeline.transaction.apply"),
        Json("timeline.shift_after"),
        Json("timeline.ripple_insert"),
        Json("timeline.ripple_delete"),
        Json("timeline.close_gap"),
        Json("object.move"),
        Json("object.delete"),
        Json("batch.validate"),
        Json("batch.apply"),
    };
    Json::Object result{
        {"explicit_plan_sync", Json(true)},
        {"guarded_checkpoint_save", Json(true)},
        {"local_project", Json(true)},
        {"lossless_aup2_document", Json(true)},
        {"project_lifecycle_notifications", Json(true)},
        {"project_path_observation", Json(true)},
        {"semantic_effect_profiles", Json(true)},
        {"native_effect_fallback", Json(true)},
        {"edit_plan_create_effect_stack", Json(true)},
        {"media_group_effect_routing", Json(true)},
        {"linear_effect_values", Json(true)},
        {"aup2_effect_manifest_version", Json(2001901)},
        {"sessions",
         Json(Json::Object{
             {"idempotent_mutations", Json(true)},
             {"max_cached_operations",
              Json(static_cast<std::int64_t>(
                  kMaxSessionOperations))},
             {"max_clients",
              Json(static_cast<std::int64_t>(
                  kMaxPipeClients))},
             {"sdk_queue", Json("fifo")},
         })},
        {"events",
         Json(Json::Object{
             {"long_poll", Json(true)},
             {"max_entries",
              Json(static_cast<std::int64_t>(
                  kMaxEventJournalEntries))},
             {"max_watch_ms",
              Json(kMaxEventWatchMilliseconds)},
             {"sequence", Json(true)},
         })},
        {"scene",
         Json(Json::Object{
             {"create", Json(false)},
             {"current_get", Json(true)},
             {"current_update", Json(true)},
             {"delete", Json(false)},
             {"duplicate", Json(false)},
             {"list", Json(false)},
             {"switch", Json(false)},
             {"update_non_undoable_confirmation",
              Json(true)},
         })},
        {"history",
         Json(Json::Object{
             {"bridge_owned_redo", Json(false)},
             {"bridge_owned_undo", Json(false)},
             {"global_history_exposed", Json(false)},
             {"sdk_execute_api", Json(false)},
         })},
        {"release_gate",
         Json(Json::Object{
             {"ready_for_1_0", Json(false)},
             {"blocked_by",
              Json(Json::Array{
                  Json("sdk_scene_crud"),
                  Json("sdk_undo_redo"),
              })},
         })},
        {"batch",
         Json(Json::Object{
             {"atomic", Json(false)},
             {"max_commands",
              Json(static_cast<std::int64_t>(kMaxBatchCommands))},
             {"single_undo_unit", Json(true)},
         })},
        {"edit_plan",
         Json(Json::Object{
             {"atomic", Json(false)},
             {"automatic_placement", Json(false)},
             {"max_commands",
              Json(static_cast<std::int64_t>(kMaxBatchCommands))},
             {"max_create_effects",
              Json(static_cast<std::int64_t>(kMaxCreateEffects))},
             {"max_create_effect_items",
              Json(static_cast<std::int64_t>(kMaxCreateEffectItems))},
             {"operations",
              Json(Json::Array{
                  Json("object.create_from_alias"),
                  Json("object.create_from_media_file"),
                  Json("object.update"),
                  Json("object.move"),
                  Json("object.delete"),
                  Json("object.effect.add"),
                  Json("object.effect.set_enabled"),
              })},
             {"rollback", Json("best_effort_with_receipt")},
             {"single_revision", Json(true)},
             {"single_undo_unit", Json(true)},
         })},
        {"max_alias_bytes",
         Json(static_cast<std::int64_t>(kMaxAliasBytes))},
        {"max_payload_bytes", Json(static_cast<std::int64_t>(kMaxPayloadBytes))},
        {"effect_catalog",
         Json(Json::Object{
             {"max_effects",
              Json(static_cast<std::int64_t>(kMaxCatalogEffects))},
             {"max_items_per_effect",
              Json(static_cast<std::int64_t>(
                  kMaxCatalogItemsPerEffect))},
             {"max_page_size",
              Json(static_cast<std::int64_t>(
                  kMaxCatalogPageEffects))},
             {"paged", Json(true)},
         })},
        {"media",
         Json(Json::Object{
             {"absolute_paths_required", Json(true)},
             {"auto_length", Json(true)},
             {"native_probe", Json(true)},
             {"inventory", Json(true)},
             {"relink_atomic", Json(true)},
             {"split_basic_clips", Json(true)},
             {"trim_fixed_speed",
              Json("verified_alias_replacement")},
         })},
        {"structural_editing",
         Json(Json::Object{
             {"duration",
              Json("verified_alias_replacement")},
             {"native_duration_setter", Json(false)},
             {"native_effect_reorder", Json(false)},
             {"native_split", Json(false)},
             {"unsafe_objects_fail_closed", Json(true)},
         })},
        {"timeline_transactions",
         Json(Json::Object{
             {"atomic_preflight", Json(true)},
             {"max_commands",
              Json(static_cast<std::int64_t>(
                  kMaxTimelineCommands))},
             {"operations",
              Json(Json::Array{
                  Json("move"),
                  Json("delete"),
                  Json("set_items"),
                  Json("set_name"),
                  Json("effect.set_enabled"),
              })},
             {"single_revision", Json(true)},
             {"single_undo_unit", Json(true)},
             {"structural_operations", Json(false)},
         })},
        {"inspection",
         Json(Json::Object{
             {"max_effects",
              Json(static_cast<std::int64_t>(kMaxInspectEffects))},
             {"max_items",
              Json(static_cast<std::int64_t>(kMaxInspectItems))},
             {"sampled_track_values", Json(true)},
             {"temporary_object_ids", Json(true)},
         })},
        {"effect_editing",
         Json(Json::Object{
             {"add", Json(true)},
             {"delete", Json(true)},
             {"initial_items_atomic", Json(true)},
             {"reorder", Json(true)},
             {"reorder_backend",
              Json("verified_alias_replacement")},
             {"selector_from_inspection", Json(true)},
             {"set_enabled", Json(true)},
         })},
        {"sections",
         Json(Json::Object{
             {"create", Json(true)},
             {"delete", Json(true)},
             {"list", Json(true)},
             {"move", Json(true)},
             {"sample_values_via_inspection", Json(true)},
             {"typed_setters", Json(false)},
             {"raw_values_via_object_set_item", Json(true)},
         })},
        {"catalogs",
         Json(Json::Object{
             {"fonts", Json(true)},
             {"modules", Json(true)},
             {"palettes", Json(true)},
             {"track_groups_in_inspection", Json(true)},
         })},
        {"frame_render",
         Json(Json::Object{
             {"capture_ttl_seconds",
              Json(kCaptureTtlSeconds)},
             {"chunk_bytes",
              Json(static_cast<std::int64_t>(
                  kFrameChunkBytes))},
             {"format", Json("png")},
             {"max_captures",
              Json(static_cast<std::int64_t>(
                  kMaxCaptures))},
             {"max_png_bytes",
              Json(static_cast<std::int64_t>(
                  kMaxFramePngBytes))},
             {"native_renderer", Json(true)},
         })},
        {"audio_render",
         Json(Json::Object{
             {"capture_ttl_seconds",
              Json(kCaptureTtlSeconds)},
             {"channels", Json(2)},
             {"chunk_bytes",
              Json(static_cast<std::int64_t>(
                  kAudioChunkBytes))},
             {"format", Json("f32le")},
             {"max_bytes",
              Json(static_cast<std::int64_t>(
                  kMaxAudioCaptureBytes))},
             {"max_captures",
              Json(static_cast<std::int64_t>(
                  kMaxAudioCaptures))},
             {"max_frames", Json(kMaxAudioRenderFrames)},
             {"native_renderer", Json(true)},
         })},
        {"snapshot",
         Json(Json::Object{
             {"max_alias_bytes",
              Json(static_cast<std::int64_t>(kMaxSnapshotAliasBytes))},
             {"max_objects",
              Json(static_cast<std::int64_t>(kMaxSnapshotObjects))},
             {"filtered", Json(true)},
             {"paged", Json(true)},
             {"optional_alias", Json(true)},
             {"temporary_object_ids", Json(true)},
         })},
        {"layers",
         Json(Json::Object{
             {"includes_object_count", Json(true)},
             {"includes_visibility", Json(true)},
             {"max_page_size",
              Json(static_cast<std::int64_t>(
                  kMaxLayerPageSize))},
             {"paged", Json(true)},
         })},
        {"methods", Json(std::move(methods))},
        {"notifications",
         Json(Json::Array{
             Json("object_updated"),
             Json("edit_frame_changed"),
             Json("edit_scene_changed"),
             Json("focus_object_changed"),
             Json("project_loaded"),
             Json("project_saving"),
         })},
        {"protocol_version", Json(static_cast<std::int64_t>(kProtocolVersion))},
    };
    return Json(std::move(result));
}

std::string CommandDispatcher::handle_effect_catalog(
    const Request& request) {
    std::int64_t start = 0;
    std::int64_t count = 64;
    if (const Json* value = find_field(request.params, "start");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "start must be a non-negative integer.");
        }
        start = value->as_integer();
    }
    if (const Json* value = find_field(request.params, "count");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "count must be a positive integer within the page limit.");
        }
        count = value->as_integer();
    }
    if (start < 0 ||
        static_cast<std::uint64_t>(start) >
            std::numeric_limits<std::size_t>::max() ||
        count <= 0 ||
        count > static_cast<std::int64_t>(kMaxCatalogPageEffects)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "The requested effect catalog page is outside the limit.");
    }

    EffectCatalogResult catalog = sdk_.get_effect_catalog(
        static_cast<std::size_t>(start),
        static_cast<std::size_t>(count));
    if (!catalog.ok) {
        return make_error_response(
            request.id,
            catalog.error_code,
            catalog.error_message,
            {},
            catalog.retryable);
    }
    Json::Array effects;
    effects.reserve(catalog.effects.size());
    for (CatalogEffect& effect : catalog.effects) {
        Json::Array items;
        items.reserve(effect.items.size());
        for (CatalogItem& item : effect.items) {
            items.emplace_back(Json(Json::Object{
                {"name", Json(std::move(item.name))},
                {"type", Json(std::string(
                     effect_item_type_name(item.type)))},
                {"type_code", Json(item.type)},
            }));
        }
        effects.emplace_back(Json(Json::Object{
            {"flags",
             Json(Json::Object{
                 {"audio", Json((effect.flags & 2) != 0)},
                 {"camera", Json((effect.flags & 8) != 0)},
                 {"filter_object", Json((effect.flags & 4) != 0)},
                 {"video", Json((effect.flags & 1) != 0)},
             })},
            {"items", Json(std::move(items))},
            {"name", Json(std::move(effect.name))},
            {"type", Json(std::string(effect_type_name(effect.type)))},
            {"type_code", Json(effect.type)},
        }));
    }
    const std::size_t returned = effects.size();
    const std::size_t next =
        catalog.start > std::numeric_limits<std::size_t>::max() - returned
            ? catalog.total
            : catalog.start + returned;
    Json::Object result{
        {"count", Json(static_cast<std::int64_t>(returned))},
        {"effects", Json(std::move(effects))},
        {"start", Json(static_cast<std::int64_t>(catalog.start))},
        {"total", Json(static_cast<std::int64_t>(catalog.total))},
    };
    result.emplace(
        "next_start",
        next < catalog.total
            ? Json(static_cast<std::int64_t>(next))
            : Json(nullptr));
    return make_success_response(request.id, Json(std::move(result)));
}

std::string CommandDispatcher::handle_layers(
    const Request& request) {
    std::int64_t start = 0;
    std::int64_t count = 128;
    if (const Json* value = find_field(request.params, "start");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "start must be a non-negative integer.");
        }
        start = value->as_integer();
    }
    if (const Json* value = find_field(request.params, "count");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "count must be a positive integer within the page limit.");
        }
        count = value->as_integer();
    }
    if (start < 0 ||
        start > std::numeric_limits<int>::max() ||
        count <= 0 ||
        count > static_cast<std::int64_t>(kMaxLayerPageSize)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "The requested layer page is outside the limit.");
    }

    LayerSnapshotResult page = sdk_.get_layers(
        static_cast<int>(start),
        static_cast<int>(count));
    if (!page.ok) {
        return make_error_response(
            request.id,
            page.error_code,
            page.error_message,
            {},
            page.retryable);
    }
    const int visible_end =
        page.display_layer_start + page.display_layer_count;
    Json::Array layers;
    layers.reserve(page.layers.size());
    for (LayerInfo& layer : page.layers) {
        const bool visible =
            page.display_layer_count > 0 &&
            layer.layer >= page.display_layer_start &&
            layer.layer < visible_end;
        layers.emplace_back(Json(Json::Object{
            {"enabled", Json(layer.enabled)},
            {"layer", Json(layer.layer)},
            {"locked", Json(layer.locked)},
            {"name",
             layer.has_name
                 ? Json(std::move(layer.name))
                 : Json(nullptr)},
            {"object_count",
             Json(static_cast<std::int64_t>(layer.object_count))},
            {"visible", Json(visible)},
        }));
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"count",
             Json(static_cast<std::int64_t>(layers.size()))},
            {"display",
             Json(Json::Object{
                 {"count", Json(page.display_layer_count)},
                 {"start", Json(page.display_layer_start)},
             })},
            {"layer_max", Json(page.layer_max)},
            {"layers", Json(std::move(layers))},
            {"revision", Json(page.revision)},
            {"scene_id", Json(page.scene_id)},
            {"start", Json(page.start)},
        }));
}

std::string CommandDispatcher::handle_layer_update(
    const Request& request) {
    const Json* expected =
        find_field(request.params, "expected_revision");
    const Json* layer = find_field(request.params, "layer");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0 ||
        layer == nullptr || !layer->is_integer() ||
        layer->as_integer() < 0 ||
        layer->as_integer() >
            std::numeric_limits<int>::max()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "expected_revision must be positive and layer must be a supported non-negative integer.");
    }

    std::optional<std::wstring> name;
    std::optional<bool> enabled;
    if (const Json* value = find_field(request.params, "name");
        value != nullptr) {
        if (value->is_null()) {
            name = std::wstring();
        } else if (
            value->is_string() &&
            value->as_string().size() <= 4096U &&
            value->as_string().find('\0') ==
                std::string::npos) {
            try {
                name = utf8_to_wide(value->as_string());
            } catch (const std::exception&) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "name must contain valid UTF-8.");
            }
        } else {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "name must be null or a UTF-8 string within the size limit.");
        }
    }
    if (const Json* value =
            find_field(request.params, "enabled");
        value != nullptr) {
        if (!value->is_bool()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "enabled must be a boolean.");
        }
        enabled = value->as_bool();
    }
    if (!name.has_value() && !enabled.has_value()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "At least one of name or enabled must be supplied.");
    }
    return mutation_response(
        request.id,
        sdk_.update_layer(
            expected->as_integer(),
            static_cast<int>(layer->as_integer()),
            name,
            enabled));
}

std::string CommandDispatcher::handle_runtime_catalog(
    const Request& request,
    const std::string_view kind) {
    std::int64_t start = 0;
    std::int64_t count = 128;
    if (const Json* value = find_field(request.params, "start");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "start must be a non-negative integer.");
        }
        start = value->as_integer();
    }
    if (const Json* value = find_field(request.params, "count");
        value != nullptr) {
        if (!value->is_integer()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "count must be a positive integer.");
        }
        count = value->as_integer();
    }
    if (start < 0 || count <= 0 || count > 256 ||
        static_cast<std::uint64_t>(start) >
            std::numeric_limits<std::size_t>::max()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "The requested catalog page is outside the limit.");
    }
    const std::size_t page_start =
        static_cast<std::size_t>(start);
    const std::size_t page_count =
        static_cast<std::size_t>(count);
    Json::Array entries;
    std::size_t total = 0U;

    if (kind == "font") {
        StringCatalogResult catalog =
            sdk_.get_font_catalog();
        if (!catalog.ok) {
            return make_error_response(
                request.id,
                catalog.error_code,
                catalog.error_message,
                {},
                catalog.retryable);
        }
        total = catalog.values.size();
        const std::size_t end =
            page_start >= total
                ? total
                : (std::min)(
                      total,
                      page_start + page_count);
        if (page_start < total) {
            entries.reserve(end - page_start);
            for (std::size_t index = page_start;
                 index < end;
                 ++index) {
                entries.emplace_back(
                    Json(std::move(catalog.values[index])));
            }
        }
    } else if (kind == "palette") {
        PaletteCatalogResult catalog =
            sdk_.get_palette_catalog();
        if (!catalog.ok) {
            return make_error_response(
                request.id,
                catalog.error_code,
                catalog.error_message,
                {},
                catalog.retryable);
        }
        total = catalog.palettes.size();
        const std::size_t end =
            page_start >= total
                ? total
                : (std::min)(total, page_start + page_count);
        entries.reserve(end - (std::min)(page_start, end));
        for (std::size_t index = page_start;
             index < end;
             ++index) {
            Json::Array colors;
            colors.reserve(
                catalog.palettes[index].colors_rgba.size());
            for (const std::uint32_t color :
                 catalog.palettes[index].colors_rgba) {
                colors.emplace_back(
                    Json(static_cast<std::int64_t>(color)));
            }
            entries.emplace_back(Json(Json::Object{
                {"colors_rgba", Json(std::move(colors))},
                {"name",
                 Json(std::move(
                     catalog.palettes[index].name))},
            }));
        }
    } else {
        ModuleCatalogResult catalog =
            sdk_.get_module_catalog();
        if (!catalog.ok) {
            return make_error_response(
                request.id,
                catalog.error_code,
                catalog.error_message,
                {},
                catalog.retryable);
        }
        total = catalog.modules.size();
        const std::size_t end =
            page_start >= total
                ? total
                : (std::min)(total, page_start + page_count);
        entries.reserve(end - (std::min)(page_start, end));
        for (std::size_t index = page_start;
             index < end;
             ++index) {
            ModuleCatalogEntry& module =
                catalog.modules[index];
            entries.emplace_back(Json(Json::Object{
                {"information",
                 Json(std::move(module.information))},
                {"name", Json(std::move(module.name))},
                {"type", Json(std::move(module.type_name))},
                {"type_code", Json(module.type)},
            }));
        }
    }
    const std::size_t returned = entries.size();
    const std::size_t next = page_start + returned;
    std::string response = make_success_response(
        request.id,
        Json(Json::Object{
            {"count",
             Json(static_cast<std::int64_t>(returned))},
            {"entries", Json(std::move(entries))},
            {"next_start",
             next < total
                 ? Json(static_cast<std::int64_t>(next))
                 : Json(nullptr)},
            {"start", Json(start)},
            {"total", Json(static_cast<std::int64_t>(total))},
        }));
    if (response.size() > kMaxPayloadBytes) {
        return make_error_response(
            request.id,
            "CATALOG_TOO_LARGE",
            "The encoded catalog page exceeds the IPC payload limit.");
    }
    return response;
}

std::string CommandDispatcher::handle_scene(
    const Request& request,
    const bool update) {
    SceneInfoResult result;
    if (!update) {
        result = sdk_.get_current_scene();
    } else {
        const Json* expected =
            find_field(request.params, "expected_revision");
        const Json* confirmation =
            find_field(
                request.params,
                "confirm_non_undoable");
        if (expected == nullptr || !expected->is_integer() ||
            expected->as_integer() <= 0 ||
            confirmation == nullptr ||
            !confirmation->is_bool() ||
            !confirmation->as_bool()) {
            return make_error_response(
                request.id,
                "NON_UNDOABLE_CONFIRMATION_REQUIRED",
                "Scene updates require expected_revision and confirm_non_undoable=true.");
        }
        SceneUpdate changes;
        if (const Json* value =
                find_field(request.params, "name");
            value != nullptr) {
            if (!value->is_string() ||
                value->as_string().empty() ||
                value->as_string().size() > 4096U ||
                value->as_string().find('\0') !=
                    std::string::npos) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "name must be a non-empty UTF-8 string within the size limit.");
            }
            changes.name =
                utf8_to_wide(value->as_string());
        }
        const auto parse_positive =
            [&](const std::string_view name,
                std::optional<int>& destination,
                const int maximum) -> std::string {
            if (const Json* value =
                    find_field(request.params, name);
                value != nullptr) {
                if (!value->is_integer() ||
                    value->as_integer() <= 0 ||
                    value->as_integer() > maximum) {
                    return std::string(name) +
                           " is outside the supported positive range.";
                }
                destination =
                    static_cast<int>(value->as_integer());
            }
            return {};
        };
        for (const auto [name, destination, maximum] :
             std::initializer_list<std::tuple<
                 std::string_view,
                 std::optional<int>*,
                 int>>{
                 {"width", &changes.width,
                  kMaxRenderDimension},
                 {"height", &changes.height,
                  kMaxRenderDimension},
                 {"rate", &changes.rate,
                  std::numeric_limits<int>::max()},
                 {"scale", &changes.scale,
                  std::numeric_limits<int>::max()},
                 {"sample_rate", &changes.sample_rate,
                  1000000},
             }) {
            if (const std::string error =
                    parse_positive(
                        name,
                        *destination,
                        maximum);
                !error.empty()) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    error);
            }
        }
        if (changes.width.has_value() !=
                changes.height.has_value() ||
            changes.rate.has_value() !=
                changes.scale.has_value()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "width/height and rate/scale must be supplied as complete pairs.");
        }
        result = sdk_.update_current_scene(
            expected->as_integer(),
            changes);
    }
    if (!result.ok) {
        Json::Object details;
        if (result.revision > 0) {
            details.emplace(
                "current_revision",
                Json(result.revision));
        }
        return make_error_response(
            request.id,
            result.error_code,
            result.error_message,
            std::move(details),
            result.retryable);
    }
    const SceneInfo& info = result.info;
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"frame_rate",
             Json(Json::Object{
                 {"rate", Json(info.rate)},
                 {"scale", Json(info.scale)},
             })},
            {"height", Json(info.height)},
            {"name", Json(info.name)},
            {"non_undoable", Json(update)},
            {"revision", Json(result.revision)},
            {"sample_rate", Json(info.sample_rate)},
            {"scene_id", Json(info.scene_id)},
            {"width", Json(info.width)},
        }));
}

std::string CommandDispatcher::handle_snapshot(
    const Request& request) {
    std::int64_t offset = 0;
    std::int64_t count =
        static_cast<std::int64_t>(kMaxSnapshotObjects);
    std::optional<std::int64_t> layer_start;
    std::optional<std::int64_t> layer_end;
    std::optional<std::int64_t> frame_start;
    std::optional<std::int64_t> frame_end;
    std::optional<bool> has_alias;
    bool include_alias = true;
    std::set<std::string, std::less<>> object_ids;

    const auto parse_non_negative =
        [&](const std::string_view name,
            std::int64_t& destination) -> std::string {
        if (const Json* value = find_field(request.params, name);
            value != nullptr) {
            if (!value->is_integer() || value->as_integer() < 0) {
                return std::string(name) +
                       " must be a non-negative integer.";
            }
            destination = value->as_integer();
        }
        return {};
    };
    if (const std::string error =
            parse_non_negative("offset", offset);
        !error.empty()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            error);
    }
    if (const std::string error =
            parse_non_negative("count", count);
        !error.empty()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            error);
    }
    if (count <= 0 ||
        count > static_cast<std::int64_t>(
                    kMaxSnapshotObjects)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "count must be positive and within the snapshot page limit.");
    }

    const auto parse_optional_integer =
        [&](const std::string_view name,
            std::optional<std::int64_t>& destination)
        -> std::string {
        if (const Json* value = find_field(request.params, name);
            value != nullptr) {
            if (!value->is_integer() || value->as_integer() < 0 ||
                value->as_integer() >
                    std::numeric_limits<int>::max()) {
                return std::string(name) +
                       " must be a non-negative supported integer.";
            }
            destination = value->as_integer();
        }
        return {};
    };
    for (const auto [name, destination] :
         std::initializer_list<std::pair<
             std::string_view,
             std::optional<std::int64_t>*>>{
             {"layer_start", &layer_start},
             {"layer_end", &layer_end},
             {"frame_start", &frame_start},
             {"frame_end", &frame_end},
         }) {
        if (const std::string error =
                parse_optional_integer(name, *destination);
            !error.empty()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                error);
        }
    }
    if (layer_start.has_value() && layer_end.has_value() &&
        *layer_start > *layer_end) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "layer_start must not be greater than layer_end.");
    }
    if (frame_start.has_value() && frame_end.has_value() &&
        *frame_start > *frame_end) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "frame_start must not be greater than frame_end.");
    }
    if (const Json* value =
            find_field(request.params, "include_alias");
        value != nullptr) {
        if (!value->is_bool()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "include_alias must be a boolean.");
        }
        include_alias = value->as_bool();
    }
    if (const Json* value =
            find_field(request.params, "has_alias");
        value != nullptr) {
        if (!value->is_bool()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "has_alias must be a boolean.");
        }
        has_alias = value->as_bool();
    }
    if (const Json* value =
            find_field(request.params, "object_ids");
        value != nullptr) {
        if (!value->is_array() ||
            value->as_array().size() >
                kMaxSnapshotObjects) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "object_ids must be an array within the snapshot limit.");
        }
        for (const Json& item : value->as_array()) {
            if (!item.is_string() || item.as_string().empty() ||
                item.as_string().size() > 128U) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "Every object_id must be a non-empty short string.");
            }
            object_ids.insert(item.as_string());
        }
    }

    SnapshotResult snapshot = sdk_.get_snapshot();
    if (!snapshot.ok) {
        return make_error_response(
            request.id,
            snapshot.error_code,
            snapshot.error_message,
            {},
            snapshot.retryable);
    }

    Json::Array objects;
    objects.reserve(
        (std::min)(
            snapshot.objects.size(),
            static_cast<std::size_t>(count)));
    std::size_t matched_count = 0U;
    for (SnapshotObject& object : snapshot.objects) {
        const bool matches =
            (!layer_start.has_value() ||
             object.layer >= *layer_start) &&
            (!layer_end.has_value() ||
             object.layer <= *layer_end) &&
            (!frame_start.has_value() ||
             object.frame_end >= *frame_start) &&
            (!frame_end.has_value() ||
             object.frame_start <= *frame_end) &&
            (!has_alias.has_value() ||
             !object.alias.empty() == *has_alias) &&
            (object_ids.empty() ||
             object_ids.contains(object.object_id));
        if (!matches) {
            continue;
        }
        const std::size_t match_index = matched_count++;
        if (match_index < static_cast<std::size_t>(offset) ||
            objects.size() >= static_cast<std::size_t>(count)) {
            continue;
        }
        Json::Object item{
            {"api_locked", Json(object.api_locked)},
            {"alias",
             include_alias
                 ? Json(std::move(object.alias))
                 : Json(nullptr)},
            {"frame_end", Json(object.frame_end)},
            {"frame_start", Json(object.frame_start)},
            {"layer", Json(object.layer)},
            {"name",
             object.has_name
                 ? Json(std::move(object.name))
                 : Json(nullptr)},
            {"object_id", Json(std::move(object.object_id))},
        };
        objects.emplace_back(Json(std::move(item)));
    }
    std::string response = make_success_response(
        request.id,
        Json(Json::Object{
            {"count",
             Json(static_cast<std::int64_t>(objects.size()))},
            {"next_offset",
             static_cast<std::size_t>(offset) +
                         objects.size() <
                     matched_count
                 ? Json(static_cast<std::int64_t>(
                       static_cast<std::size_t>(offset) +
                       objects.size()))
                 : Json(nullptr)},
            {"object_count",
             Json(static_cast<std::int64_t>(objects.size()))},
            {"objects", Json(std::move(objects))},
            {"offset", Json(offset)},
            {"revision", Json(snapshot.revision)},
            {"scene_id", Json(snapshot.scene_id)},
            {"total", Json(static_cast<std::int64_t>(
                          matched_count))},
        }));
    if (response.size() > kMaxPayloadBytes) {
        return make_error_response(
            request.id,
            "SNAPSHOT_TOO_LARGE",
            "The encoded snapshot exceeds the IPC payload limit.");
    }
    return response;
}

std::string CommandDispatcher::handle_media_probe(
    const Request& request) {
    const MediaPathParseResult parsed =
        parse_media_path(request.params);
    if (!parsed.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            parsed.message);
    }
    const MediaProbeResult probe = sdk_.probe_media(parsed.path);
    if (!probe.ok) {
        return make_error_response(
            request.id,
            probe.error_code,
            probe.error_message,
            {},
            probe.retryable);
    }
    const MediaInfo& info = probe.info;
    std::string kind = "unsupported";
    if (info.has_info) {
        if (info.video_track_count > 0) {
            kind =
                info.duration_seconds > 0.0 ? "video" : "image";
        } else if (info.audio_track_count > 0) {
            kind = "audio";
        } else {
            kind = "unknown";
        }
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"audio_track_count", Json(info.audio_track_count)},
            {"duration_seconds", Json(info.duration_seconds)},
            {"exists", Json(parsed.exists)},
            {"extension_supported",
             Json(info.extension_supported)},
            {"has_media_info", Json(info.has_info)},
            {"height", Json(info.height)},
            {"kind", Json(std::move(kind))},
            {"readable", Json(info.readable)},
            {"regular_file", Json(parsed.regular_file)},
            {"video_track_count", Json(info.video_track_count)},
            {"width", Json(info.width)},
        }));
}

std::string CommandDispatcher::handle_media_inventory(
    const Request& request) {
    SnapshotResult snapshot = sdk_.get_snapshot();
    if (!snapshot.ok) {
        return make_error_response(
            request.id,
            snapshot.error_code,
            snapshot.error_message,
            {},
            snapshot.retryable);
    }

    struct ProbeCache final {
        bool exists = false;
        bool regular_file = false;
        bool readable = false;
        std::string probe_error;
    };
    std::map<std::wstring, ProbeCache, std::less<>>
        probes;
    std::map<std::wstring, std::size_t, std::less<>>
        duplicate_counts;
    struct InventoryItem final {
        std::string object_id;
        std::string effect;
        std::string item;
        std::string file;
        std::wstring key;
        bool api_locked = false;
    };
    std::vector<InventoryItem> inventory;

    for (std::size_t index = 0U;
         index < snapshot.objects.size();
         ++index) {
        ObjectInspectionResult inspection =
            sdk_.inspect_object(
                snapshot.revision,
                index,
                -1);
        if (!inspection.ok) {
            return make_error_response(
                request.id,
                inspection.error_code,
                inspection.error_message,
                Json::Object{
                    {"object_id",
                     Json(snapshot.objects[index].object_id)},
                },
                inspection.retryable);
        }
        for (const InspectedEffect& effect :
             inspection.effects) {
            for (const InspectedItem& item : effect.items) {
                if (item.type != 6 || !item.has_value ||
                    item.value.empty()) {
                    continue;
                }
                std::wstring path;
                try {
                    path = utf8_to_wide(item.value);
                } catch (const std::exception&) {
                    return make_error_response(
                        request.id,
                        "HOST_INSPECTION_FAILED",
                        "AviUtl2 returned an invalid UTF-8 media path.");
                }
                const std::wstring key =
                    normalized_path_key(path);
                inventory.push_back(InventoryItem{
                    snapshot.objects[index].object_id,
                    effect.selector,
                    item.name,
                    item.value,
                    key,
                    snapshot.objects[index].api_locked,
                });
                ++duplicate_counts[key];
                if (!probes.contains(key)) {
                    ProbeCache cache;
                    const DWORD attributes =
                        GetFileAttributesW(path.c_str());
                    cache.exists =
                        attributes != INVALID_FILE_ATTRIBUTES;
                    cache.regular_file =
                        cache.exists &&
                        (attributes &
                         FILE_ATTRIBUTE_DIRECTORY) == 0U;
                    if (cache.regular_file) {
                        const MediaProbeResult probe =
                            sdk_.probe_media(path);
                        if (probe.ok) {
                            cache.readable =
                                probe.info.readable;
                        } else {
                            cache.probe_error =
                                probe.error_code;
                        }
                    }
                    probes.emplace(key, std::move(cache));
                }
            }
        }
    }

    Json::Array files;
    files.reserve(inventory.size());
    std::size_t missing_count = 0U;
    std::size_t unreadable_count = 0U;
    for (InventoryItem& item : inventory) {
        const ProbeCache& probe = probes.at(item.key);
        if (!probe.exists) {
            ++missing_count;
        } else if (!probe.readable) {
            ++unreadable_count;
        }
        files.emplace_back(Json(Json::Object{
            {"api_locked", Json(item.api_locked)},
            {"duplicate_count",
             Json(static_cast<std::int64_t>(
                 duplicate_counts.at(item.key)))},
            {"effect", Json(std::move(item.effect))},
            {"exists", Json(probe.exists)},
            {"file", Json(std::move(item.file))},
            {"item", Json(std::move(item.item))},
            {"object_id", Json(std::move(item.object_id))},
            {"probe_error",
             probe.probe_error.empty()
                 ? Json(nullptr)
                 : Json(probe.probe_error)},
            {"readable", Json(probe.readable)},
            {"regular_file", Json(probe.regular_file)},
        }));
    }
    std::string response = make_success_response(
        request.id,
        Json(Json::Object{
            {"file_item_count",
             Json(static_cast<std::int64_t>(files.size()))},
            {"files", Json(std::move(files))},
            {"missing_count",
             Json(static_cast<std::int64_t>(
                 missing_count))},
            {"revision", Json(snapshot.revision)},
            {"scene_id", Json(snapshot.scene_id)},
            {"unique_file_count",
             Json(static_cast<std::int64_t>(
                 probes.size()))},
            {"unreadable_count",
             Json(static_cast<std::int64_t>(
                 unreadable_count))},
        }));
    if (response.size() > kMaxPayloadBytes) {
        return make_error_response(
            request.id,
            "INVENTORY_TOO_LARGE",
            "The encoded media inventory exceeds the IPC payload limit.");
    }
    return response;
}

std::string CommandDispatcher::handle_media_relink(
    const Request& request) {
    const Json* expected =
        find_field(request.params, "expected_revision");
    const Json* replacements =
        find_field(request.params, "replacements");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0 ||
        replacements == nullptr ||
        !replacements->is_array() ||
        replacements->as_array().empty() ||
        replacements->as_array().size() > 256U) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "expected_revision and 1..256 replacements are required.");
    }
    struct Replacement final {
        std::wstring from_key;
        std::string to_utf8;
    };
    std::map<std::wstring, Replacement, std::less<>>
        parsed_replacements;
    for (std::size_t index = 0U;
         index < replacements->as_array().size();
         ++index) {
        const Json& value =
            replacements->as_array()[index];
        if (!value.is_object()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "Every replacement must be an object.");
        }
        const Json* from =
            find_field(value.as_object(), "from");
        const Json* to =
            find_field(value.as_object(), "to");
        if (from == nullptr || !from->is_string() ||
            to == nullptr || !to->is_string()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "Every replacement requires from/to paths.");
        }
        const MediaPathParseResult from_path =
            parse_media_path(Json::Object{
                {"file", *from},
            });
        const MediaPathParseResult to_path =
            parse_media_path(Json::Object{
                {"file", *to},
            });
        if (!from_path.ok || !to_path.ok ||
            !to_path.regular_file) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                !from_path.ok
                    ? from_path.message
                    : (!to_path.ok
                           ? to_path.message
                           : "Every destination must be an existing regular file."),
                Json::Object{
                    {"failed_replacement_index",
                     Json(static_cast<std::int64_t>(index))},
                });
        }
        const MediaProbeResult probe =
            sdk_.probe_media(to_path.path);
        if (!probe.ok || !probe.info.readable) {
            return make_error_response(
                request.id,
                probe.ok
                    ? "UNSUPPORTED_MEDIA"
                    : probe.error_code,
                probe.ok
                    ? "AviUtl2 cannot read a replacement file."
                    : probe.error_message,
                Json::Object{
                    {"failed_replacement_index",
                     Json(static_cast<std::int64_t>(index))},
                },
                !probe.ok && probe.retryable);
        }
        const std::wstring key =
            normalized_path_key(from_path.path);
        if (parsed_replacements.contains(key)) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "A source path must not appear twice.");
        }
        parsed_replacements.emplace(
            key,
            Replacement{key, to->as_string()});
    }

    SnapshotResult snapshot = sdk_.get_snapshot();
    if (!snapshot.ok) {
        return make_error_response(
            request.id,
            snapshot.error_code,
            snapshot.error_message,
            {},
            snapshot.retryable);
    }
    if (snapshot.revision != expected->as_integer()) {
        return make_error_response(
            request.id,
            "STALE_PROJECT_STATE",
            "The project changed before media relinking was prepared.",
            Json::Object{
                {"current_revision", Json(snapshot.revision)},
            },
            true);
    }

    std::vector<TimelineCommand> commands;
    std::size_t matched_items = 0U;
    std::set<std::wstring, std::less<>> matched_sources;
    for (std::size_t object_index = 0U;
         object_index < snapshot.objects.size();
         ++object_index) {
        ObjectInspectionResult inspection =
            sdk_.inspect_object(
                snapshot.revision,
                object_index,
                -1);
        if (!inspection.ok) {
            return make_error_response(
                request.id,
                inspection.error_code,
                inspection.error_message,
                {},
                inspection.retryable);
        }
        TimelineCommand command;
        command.type = TimelineCommandType::set_items;
        command.object_index = object_index;
        for (const InspectedEffect& effect :
             inspection.effects) {
            for (const InspectedItem& item : effect.items) {
                if (item.type != 6 || !item.has_value) {
                    continue;
                }
                const std::wstring key = normalized_path_key(
                    utf8_to_wide(item.value));
                const auto replacement =
                    parsed_replacements.find(key);
                if (replacement ==
                    parsed_replacements.end()) {
                    continue;
                }
                command.updates.push_back(ObjectItemUpdate{
                    utf8_to_wide(effect.selector),
                    utf8_to_wide(item.name),
                    replacement->second.to_utf8,
                });
                ++matched_items;
                matched_sources.insert(key);
            }
        }
        if (!command.updates.empty()) {
            commands.push_back(std::move(command));
        }
    }
    if (matched_sources.size() !=
        parsed_replacements.size()) {
        return make_error_response(
            request.id,
            "MEDIA_SOURCE_NOT_FOUND",
            "One or more source paths are not referenced by the current scene.",
            Json::Object{
                {"matched_sources",
                 Json(static_cast<std::int64_t>(
                     matched_sources.size()))},
                {"requested_sources",
                 Json(static_cast<std::int64_t>(
                     parsed_replacements.size()))},
            });
    }
    if (commands.empty()) {
        return make_success_response(
            request.id,
            Json(Json::Object{
                {"affected_objects", Json(0)},
                {"matched_items", Json(0)},
                {"revision", Json(snapshot.revision)},
                {"snapshot_required", Json(false)},
                {"undo_unit", Json(nullptr)},
                {"undo_grouped", Json(false)},
                {"warnings", Json(Json::Array{})},
            }));
    }
    const TimelineTransactionResult transaction =
        sdk_.run_timeline_transaction(
            snapshot.revision,
            commands,
            true);
    if (!transaction.ok) {
        return timeline_transaction_response(
            request.id,
            transaction,
            true);
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"affected_objects",
             Json(static_cast<std::int64_t>(
                 commands.size()))},
            {"matched_items",
             Json(static_cast<std::int64_t>(
                 matched_items))},
            {"revision",
             transaction.current_revision >= 0
                 ? Json(transaction.current_revision)
                 : Json(nullptr)},
            {"snapshot_required",
             Json(transaction.current_revision < 0)},
            {"undo_unit", Json("single_edit_section")},
            {"undo_grouped", Json(true)},
            {"warnings", Json(Json::Array{})},
        }));
}

std::string CommandDispatcher::handle_create_from_media(
    const Request& request) {
    const MediaPathParseResult parsed =
        parse_media_path(request.params);
    if (!parsed.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            parsed.message);
    }
    if (!parsed.regular_file) {
        return make_error_response(
            request.id,
            "MEDIA_FILE_NOT_FOUND",
            "file must refer to an existing non-directory file.");
    }
    const Json* layer = find_field(request.params, "layer");
    const Json* frame = find_field(request.params, "frame");
    const Json* length = find_field(request.params, "length");
    constexpr std::int64_t max_int =
        std::numeric_limits<int>::max();
    if (layer == nullptr || !layer->is_integer() ||
        frame == nullptr || !frame->is_integer() ||
        length == nullptr || !length->is_integer() ||
        layer->as_integer() < 0 ||
        layer->as_integer() > max_int ||
        frame->as_integer() < 0 ||
        frame->as_integer() > max_int ||
        length->as_integer() < 0 ||
        length->as_integer() > max_int ||
        (length->as_integer() > 0 &&
         frame->as_integer() >
             max_int - length->as_integer() + 1)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "layer and frame must be non-negative integers; length must be 0 (auto) or a positive supported integer.");
    }
    const SnapshotResult before = sdk_.get_snapshot();
    if (!before.ok) {
        return make_error_response(
            request.id,
            before.error_code,
            before.error_message,
            {},
            before.retryable);
    }
    const CreateMediaResult created =
        sdk_.create_object_from_media_file(
            parsed.path,
            static_cast<int>(layer->as_integer()),
            static_cast<int>(frame->as_integer()),
            static_cast<int>(length->as_integer()));
    if (!created.ok) {
        return make_error_response(
            request.id,
            created.error_code,
            created.error_message,
            {},
            created.retryable);
    }
    SnapshotResult after = sdk_.get_snapshot();
    if (!after.ok) {
        return make_error_response(
            request.id,
            after.error_code,
            after.error_message,
            Json::Object{
                {"mutation_may_have_succeeded", Json(true)},
            },
            after.retryable);
    }
    using ObjectSignature =
        std::tuple<int, int, int, std::string, std::string>;
    std::map<ObjectSignature, std::size_t> previous_counts;
    for (const SnapshotObject& object : before.objects) {
        ++previous_counts[ObjectSignature{
            object.layer,
            object.frame_start,
            object.frame_end,
            object.has_name ? object.name : std::string(),
            object.alias,
        }];
    }
    Json::Array created_objects;
    for (SnapshotObject& object : after.objects) {
        const ObjectSignature signature{
            object.layer,
            object.frame_start,
            object.frame_end,
            object.has_name ? object.name : std::string(),
            object.alias,
        };
        auto previous = previous_counts.find(signature);
        if (previous != previous_counts.end() &&
            previous->second > 0U) {
            --previous->second;
            continue;
        }
        created_objects.emplace_back(Json(Json::Object{
            {"api_locked", Json(object.api_locked)},
            {"frame_end", Json(object.frame_end)},
            {"frame_start", Json(object.frame_start)},
            {"layer", Json(object.layer)},
            {"name",
             object.has_name
                 ? Json(std::move(object.name))
                 : Json(nullptr)},
            {"object_id", Json(std::move(object.object_id))},
        }));
    }
    if (created_objects.empty()) {
        return make_error_response(
            request.id,
            "MEDIA_CREATE_VERIFICATION_FAILED",
            "AviUtl2 accepted the media but no created object was found in the fresh snapshot.",
            Json::Object{
                {"current_revision", Json(after.revision)},
                {"mutation_may_have_succeeded", Json(true)},
            });
    }
    const std::size_t created_count = created_objects.size();
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"applied_count",
             Json(static_cast<std::int64_t>(created_count))},
            {"created",
             Json(Json::Object{
                 {"frame_end", Json(created.frame_end)},
                 {"frame_start", Json(created.frame_start)},
                 {"layer", Json(created.layer)},
             })},
            {"created_objects", Json(std::move(created_objects))},
            {"revision", Json(after.revision)},
            {"snapshot_required", Json(false)},
            {"undo_grouped", Json(true)},
            {"undo_unit", Json("single_edit_section")},
            {"warnings",
             created_count > 1U
                 ? Json(Json::Array{
                       Json("sdk_generated_object_group"),
                   })
                 : Json(Json::Array{})},
        }));
}

std::string CommandDispatcher::handle_inspect(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    int sample_frame = -1;
    if (const Json* value =
            find_field(request.params, "sample_frame");
        value != nullptr) {
        if (!value->is_integer() || value->as_integer() < 0 ||
            value->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "sample_frame must be a non-negative supported integer.");
        }
        sample_frame = static_cast<int>(value->as_integer());
    }
    ObjectInspectionResult inspection = sdk_.inspect_object(
        target.target.revision,
        target.target.index,
        sample_frame);
    if (!inspection.ok) {
        Json::Object details;
        if (inspection.revision > 0) {
            details.emplace(
                "current_revision",
                Json(inspection.revision));
        }
        return make_error_response(
            request.id,
            inspection.error_code,
            inspection.error_message,
            std::move(details),
            inspection.retryable);
    }

    Json::Array effects;
    effects.reserve(inspection.effects.size());
    for (InspectedEffect& effect : inspection.effects) {
        Json::Array items;
        items.reserve(effect.items.size());
        for (InspectedItem& item : effect.items) {
            Json::Object item_object{
                {"name", Json(std::move(item.name))},
                {"raw_value",
                 item.has_value
                     ? Json(std::move(item.value))
                     : Json(nullptr)},
                {"type",
                 Json(std::string(
                     effect_item_type_name(item.type)))},
                {"type_code", Json(item.type)},
            };
            item_object.emplace(
                "sampled_check_value",
                item.has_sampled_check_value
                    ? Json(item.sampled_check_value)
                    : Json(nullptr));
            if (item.track.available) {
                Json::Array parameters;
                parameters.reserve(item.track.parameters.size());
                for (const double parameter :
                     item.track.parameters) {
                    parameters.emplace_back(Json(parameter));
                }
                Json::Array group_items;
                group_items.reserve(
                    item.track.group_items.size());
                for (std::string& group_item :
                     item.track.group_items) {
                    group_items.emplace_back(
                        Json(std::move(group_item)));
                }
                item_object.emplace(
                    "track",
                    Json(Json::Object{
                        {"accelerate",
                         Json(item.track.accelerate)},
                        {"decelerate",
                         Json(item.track.decelerate)},
                        {"group_count",
                         Json(item.track.group_count)},
                        {"group_index",
                         Json(item.track.group_index)},
                        {"group_items",
                         Json(std::move(group_items))},
                        {"group_name",
                         item.track.has_group_name
                             ? Json(std::move(
                                   item.track.group_name))
                             : Json(nullptr)},
                        {"ignore_midpoints",
                         Json(item.track.ignore_midpoints)},
                        {"mode",
                         item.track.has_mode
                             ? Json(std::move(item.track.mode))
                             : Json(nullptr)},
                        {"parameters",
                         Json(std::move(parameters))},
                        {"sampled_value",
                         item.track.has_sampled_value
                             ? Json(item.track.sampled_value)
                             : Json(nullptr)},
                        {"time_control",
                         Json(item.track.time_control)},
                    }));
            } else {
                item_object.emplace("track", Json(nullptr));
            }
            items.emplace_back(Json(std::move(item_object)));
        }
        effects.emplace_back(
            Json(Json::Object{
                {"enabled", Json(effect.enabled)},
                {"index", Json(effect.index)},
                {"items", Json(std::move(items))},
                {"locked", Json(effect.locked)},
                {"name", Json(std::move(effect.name))},
                {"occurrence", Json(effect.occurrence)},
                {"selector", Json(std::move(effect.selector))},
            }));
    }
    std::string response = make_success_response(
        request.id,
        Json(Json::Object{
            {"effect_count",
             Json(static_cast<std::int64_t>(effects.size()))},
            {"effects", Json(std::move(effects))},
            {"object_id",
             Json("obj-" +
                  std::to_string(inspection.revision) + "-" +
                  std::to_string(target.target.index))},
            {"revision", Json(inspection.revision)},
            {"sample_frame", Json(inspection.sampled_frame)},
        }));
    if (response.size() > kMaxPayloadBytes) {
        return make_error_response(
            request.id,
            "INSPECTION_TOO_LARGE",
            "The encoded object inspection exceeds the IPC payload limit.");
    }
    return response;
}

void CommandDispatcher::cleanup_captures() {
    const auto now = std::chrono::steady_clock::now();
    for (auto iterator = captures_.begin();
         iterator != captures_.end();) {
        if (iterator->second.expires_at <= now) {
            capture_bytes_ -= iterator->second.png.size();
            iterator = captures_.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

std::string CommandDispatcher::handle_frame_render(
    const Request& request) {
    const Json* frame_value =
        find_field(request.params, "frame");
    if (frame_value == nullptr ||
        !frame_value->is_integer() ||
        frame_value->as_integer() < 0 ||
        frame_value->as_integer() >
            std::numeric_limits<int>::max()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "frame must be a non-negative supported integer.");
    }
    const int frame =
        static_cast<int>(frame_value->as_integer());

    SnapshotResult before = sdk_.get_snapshot();
    if (!before.ok) {
        return make_error_response(
            request.id,
            before.error_code,
            before.error_message,
            {},
            before.retryable);
    }
    RenderedFrameResult rendered = sdk_.render_frame(frame);
    if (!rendered.ok) {
        return make_error_response(
            request.id,
            rendered.error_code,
            rendered.error_message,
            {},
            rendered.retryable);
    }
    if (rendered.frame != frame) {
        return make_error_response(
            request.id,
            "RENDER_FRAME_MISMATCH",
            "AviUtl2 returned a different frame than requested.");
    }
    SnapshotResult after = sdk_.get_snapshot();
    if (!after.ok) {
        return make_error_response(
            request.id,
            after.error_code,
            after.error_message,
            {},
            after.retryable);
    }
    if (after.revision != before.revision ||
        after.scene_id != before.scene_id) {
        return make_error_response(
            request.id,
            "STALE_PROJECT_STATE",
            "The project changed while the frame was being rendered.",
            Json::Object{
                {"before_revision", Json(before.revision)},
                {"current_revision", Json(after.revision)},
            },
            true);
    }

    std::vector<std::uint8_t> png;
    std::string encoding_error;
    if (!encode_png_rgba(
            rendered.width,
            rendered.height,
            rendered.rgba,
            png,
            encoding_error)) {
        return make_error_response(
            request.id,
            "PNG_ENCODING_FAILED",
            encoding_error);
    }
    if (png.empty() || png.size() > kMaxFramePngBytes) {
        return make_error_response(
            request.id,
            "RENDER_CAPTURE_TOO_LARGE",
            "The encoded PNG exceeds the capture size limit.");
    }

    cleanup_captures();
    if (captures_.size() >= kMaxCaptures ||
        png.size() > kMaxCaptureBytes - capture_bytes_) {
        return make_error_response(
            request.id,
            "CAPTURE_LIMIT_REACHED",
            "Release an existing frame capture before rendering another.",
            Json::Object{
                {"active_captures",
                 Json(static_cast<std::int64_t>(
                     captures_.size()))},
                {"active_bytes",
                 Json(static_cast<std::int64_t>(
                     capture_bytes_))},
            },
            true);
    }

    std::string digest;
    try {
        digest = sha256_hex(png);
    } catch (const std::exception&) {
        return make_error_response(
            request.id,
            "CAPTURE_HASH_FAILED",
            "The rendered PNG could not be hashed.");
    }
    const std::string capture_id =
        "cap-" + std::to_string(pid_) + "-" +
        std::to_string(next_capture_id_++);
    const std::size_t byte_size = png.size();
    const std::size_t chunk_count =
        (byte_size + kFrameChunkBytes - 1U) /
        kFrameChunkBytes;
    FrameCapture capture{
        frame,
        rendered.width,
        rendered.height,
        before.scene_id,
        before.revision,
        digest,
        std::move(png),
        std::chrono::steady_clock::now() +
            std::chrono::seconds(kCaptureTtlSeconds),
    };
    capture_bytes_ += capture.png.size();
    captures_.emplace(capture_id, std::move(capture));

    return make_success_response(
        request.id,
        Json(Json::Object{
            {"byte_size",
             Json(static_cast<std::int64_t>(byte_size))},
            {"capture_id", Json(capture_id)},
            {"chunk_bytes",
             Json(static_cast<std::int64_t>(
                 kFrameChunkBytes))},
            {"chunk_count",
             Json(static_cast<std::int64_t>(
                 chunk_count))},
            {"format", Json("png")},
            {"frame", Json(frame)},
            {"height", Json(rendered.height)},
            {"native_renderer", Json(true)},
            {"revision", Json(before.revision)},
            {"scene_id", Json(before.scene_id)},
            {"sha256", Json(std::move(digest))},
            {"ttl_seconds", Json(kCaptureTtlSeconds)},
            {"width", Json(rendered.width)},
        }));
}

std::string CommandDispatcher::handle_frame_read_chunk(
    const Request& request) {
    cleanup_captures();
    const Json* capture_id =
        find_field(request.params, "capture_id");
    const Json* index_value =
        find_field(request.params, "index");
    if (capture_id == nullptr ||
        !capture_id->is_string() ||
        capture_id->as_string().empty() ||
        capture_id->as_string().size() > 128U ||
        index_value == nullptr ||
        !index_value->is_integer() ||
        index_value->as_integer() < 0) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "capture_id and a non-negative chunk index are required.");
    }
    const auto found =
        captures_.find(capture_id->as_string());
    if (found == captures_.end()) {
        return make_error_response(
            request.id,
            "CAPTURE_NOT_FOUND",
            "The frame capture does not exist or has expired.");
    }
    FrameCapture& capture = found->second;
    const std::size_t chunk_count =
        (capture.png.size() + kFrameChunkBytes - 1U) /
        kFrameChunkBytes;
    const std::int64_t requested_index =
        index_value->as_integer();
    if (requested_index >=
        static_cast<std::int64_t>(chunk_count)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "The chunk index is outside the capture range.");
    }
    const std::size_t index =
        static_cast<std::size_t>(requested_index);
    const std::size_t offset = index * kFrameChunkBytes;
    const std::size_t count = std::min(
        kFrameChunkBytes,
        capture.png.size() - offset);
    const std::span<const std::uint8_t> bytes(
        capture.png.data() + offset,
        count);
    capture.expires_at =
        std::chrono::steady_clock::now() +
        std::chrono::seconds(kCaptureTtlSeconds);
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"byte_offset",
             Json(static_cast<std::int64_t>(offset))},
            {"capture_id", Json(capture_id->as_string())},
            {"data_base64", Json(base64_encode(bytes))},
            {"data_size",
             Json(static_cast<std::int64_t>(count))},
            {"eof", Json(index + 1U == chunk_count)},
            {"index", Json(requested_index)},
        }));
}

std::string CommandDispatcher::handle_frame_release(
    const Request& request) {
    cleanup_captures();
    const Json* capture_id =
        find_field(request.params, "capture_id");
    if (capture_id == nullptr ||
        !capture_id->is_string() ||
        capture_id->as_string().empty() ||
        capture_id->as_string().size() > 128U) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "capture_id must be a non-empty string.");
    }
    const auto found =
        captures_.find(capture_id->as_string());
    if (found == captures_.end()) {
        return make_success_response(
            request.id,
            Json(Json::Object{{"released", Json(false)}}));
    }
    capture_bytes_ -= found->second.png.size();
    captures_.erase(found);
    return make_success_response(
        request.id,
        Json(Json::Object{{"released", Json(true)}}));
}

void CommandDispatcher::cleanup_audio_captures() {
    const auto now = std::chrono::steady_clock::now();
    for (auto iterator = audio_captures_.begin();
         iterator != audio_captures_.end();) {
        if (iterator->second.expires_at <= now) {
            audio_capture_bytes_ -=
                iterator->second.pcm.size();
            iterator = audio_captures_.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

std::string CommandDispatcher::handle_audio_render(
    const Request& request) {
    const Json* frame_start_value =
        find_field(request.params, "frame_start");
    const Json* frame_end_value =
        find_field(request.params, "frame_end");
    if (frame_start_value == nullptr ||
        !frame_start_value->is_integer() ||
        frame_end_value == nullptr ||
        !frame_end_value->is_integer() ||
        frame_start_value->as_integer() < 0 ||
        frame_end_value->as_integer() <
            frame_start_value->as_integer() ||
        frame_end_value->as_integer() >
            std::numeric_limits<int>::max() ||
        frame_end_value->as_integer() -
                frame_start_value->as_integer() + 1 >
            kMaxAudioRenderFrames) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "frame_start/frame_end must describe a supported non-negative range.");
    }
    const int frame_start =
        static_cast<int>(frame_start_value->as_integer());
    const int frame_end =
        static_cast<int>(frame_end_value->as_integer());

    SnapshotResult before = sdk_.get_snapshot();
    if (!before.ok) {
        return make_error_response(
            request.id,
            before.error_code,
            before.error_message,
            {},
            before.retryable);
    }
    if (const Json* expected =
            find_field(request.params, "expected_revision");
        expected != nullptr) {
        if (!expected->is_integer() ||
            expected->as_integer() <= 0) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "expected_revision must be a positive integer.");
        }
        if (expected->as_integer() != before.revision) {
            return make_error_response(
                request.id,
                "STALE_PROJECT_STATE",
                "The project changed before audio rendering began.",
                Json::Object{
                    {"current_revision",
                     Json(before.revision)},
                },
                true);
        }
    }

    RenderedAudioResult rendered =
        sdk_.render_audio(frame_start, frame_end);
    if (!rendered.ok) {
        return make_error_response(
            request.id,
            rendered.error_code,
            rendered.error_message,
            {},
            rendered.retryable);
    }
    if (rendered.frame_start != frame_start ||
        rendered.frame_end != frame_end ||
        rendered.sample_rate <= 0 ||
        rendered.interleaved_stereo.size() % 2U != 0U) {
        return make_error_response(
            request.id,
            "AUDIO_RENDER_METADATA_MISMATCH",
            "AviUtl2 returned inconsistent audio render metadata.");
    }
    SnapshotResult after = sdk_.get_snapshot();
    if (!after.ok) {
        return make_error_response(
            request.id,
            after.error_code,
            after.error_message,
            {},
            after.retryable);
    }
    if (after.revision != before.revision ||
        after.scene_id != before.scene_id) {
        return make_error_response(
            request.id,
            "STALE_PROJECT_STATE",
            "The project changed while audio was being rendered.",
            Json::Object{
                {"before_revision", Json(before.revision)},
                {"current_revision", Json(after.revision)},
            },
            true);
    }

    const std::size_t byte_size =
        rendered.interleaved_stereo.size() *
        sizeof(float);
    if (byte_size == 0U ||
        byte_size > kMaxAudioCaptureBytes) {
        return make_error_response(
            request.id,
            "AUDIO_CAPTURE_SIZE_INVALID",
            "The rendered PCM is empty or exceeds the capture limit.");
    }
    std::vector<std::uint8_t> pcm(byte_size);
    std::memcpy(
        pcm.data(),
        rendered.interleaved_stereo.data(),
        byte_size);

    cleanup_audio_captures();
    if (audio_captures_.size() >= kMaxAudioCaptures ||
        pcm.size() >
            kMaxAudioCaptureBytes - audio_capture_bytes_) {
        return make_error_response(
            request.id,
            "AUDIO_CAPTURE_LIMIT_REACHED",
            "Release an existing audio capture before rendering another.",
            Json::Object{
                {"active_bytes",
                 Json(static_cast<std::int64_t>(
                     audio_capture_bytes_))},
                {"active_captures",
                 Json(static_cast<std::int64_t>(
                     audio_captures_.size()))},
            },
            true);
    }
    std::string digest;
    try {
        digest = sha256_hex(pcm);
    } catch (const std::exception&) {
        return make_error_response(
            request.id,
            "CAPTURE_HASH_FAILED",
            "The rendered PCM could not be hashed.");
    }
    const std::string capture_id =
        "aud-" + std::to_string(pid_) + "-" +
        std::to_string(next_audio_capture_id_++);
    const std::size_t chunk_count =
        (byte_size + kAudioChunkBytes - 1U) /
        kAudioChunkBytes;
    const std::size_t sample_count =
        rendered.interleaved_stereo.size() / 2U;
    AudioCapture capture{
        frame_start,
        frame_end,
        rendered.sample_rate,
        before.scene_id,
        before.revision,
        digest,
        std::move(pcm),
        std::chrono::steady_clock::now() +
            std::chrono::seconds(kCaptureTtlSeconds),
    };
    audio_capture_bytes_ += capture.pcm.size();
    audio_captures_.emplace(
        capture_id,
        std::move(capture));

    return make_success_response(
        request.id,
        Json(Json::Object{
            {"byte_size",
             Json(static_cast<std::int64_t>(byte_size))},
            {"capture_id", Json(capture_id)},
            {"channels", Json(2)},
            {"chunk_bytes",
             Json(static_cast<std::int64_t>(
                 kAudioChunkBytes))},
            {"chunk_count",
             Json(static_cast<std::int64_t>(
                 chunk_count))},
            {"format", Json("f32le")},
            {"frame_end", Json(frame_end)},
            {"frame_start", Json(frame_start)},
            {"native_renderer", Json(true)},
            {"revision", Json(before.revision)},
            {"sample_count",
             Json(static_cast<std::int64_t>(
                 sample_count))},
            {"sample_rate", Json(rendered.sample_rate)},
            {"scene_id", Json(before.scene_id)},
            {"sha256", Json(std::move(digest))},
            {"ttl_seconds", Json(kCaptureTtlSeconds)},
        }));
}

std::string CommandDispatcher::handle_audio_read_chunk(
    const Request& request) {
    cleanup_audio_captures();
    const Json* capture_id =
        find_field(request.params, "capture_id");
    const Json* index_value =
        find_field(request.params, "index");
    if (capture_id == nullptr ||
        !capture_id->is_string() ||
        capture_id->as_string().empty() ||
        capture_id->as_string().size() > 128U ||
        index_value == nullptr ||
        !index_value->is_integer() ||
        index_value->as_integer() < 0) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "capture_id and a non-negative chunk index are required.");
    }
    const auto found =
        audio_captures_.find(capture_id->as_string());
    if (found == audio_captures_.end()) {
        return make_error_response(
            request.id,
            "CAPTURE_NOT_FOUND",
            "The audio capture does not exist or has expired.");
    }
    AudioCapture& capture = found->second;
    const std::size_t chunk_count =
        (capture.pcm.size() + kAudioChunkBytes - 1U) /
        kAudioChunkBytes;
    const std::int64_t requested_index =
        index_value->as_integer();
    if (requested_index >=
        static_cast<std::int64_t>(chunk_count)) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "The chunk index is outside the capture range.");
    }
    const std::size_t index =
        static_cast<std::size_t>(requested_index);
    const std::size_t offset =
        index * kAudioChunkBytes;
    const std::size_t count = std::min(
        kAudioChunkBytes,
        capture.pcm.size() - offset);
    const std::span<const std::uint8_t> bytes(
        capture.pcm.data() + offset,
        count);
    capture.expires_at =
        std::chrono::steady_clock::now() +
        std::chrono::seconds(kCaptureTtlSeconds);
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"byte_offset",
             Json(static_cast<std::int64_t>(offset))},
            {"capture_id", Json(capture_id->as_string())},
            {"data_base64", Json(base64_encode(bytes))},
            {"data_size",
             Json(static_cast<std::int64_t>(count))},
            {"eof", Json(index + 1U == chunk_count)},
            {"index", Json(requested_index)},
        }));
}

std::string CommandDispatcher::handle_audio_release(
    const Request& request) {
    cleanup_audio_captures();
    const Json* capture_id =
        find_field(request.params, "capture_id");
    if (capture_id == nullptr ||
        !capture_id->is_string() ||
        capture_id->as_string().empty() ||
        capture_id->as_string().size() > 128U) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "capture_id must be a non-empty string.");
    }
    const auto found =
        audio_captures_.find(capture_id->as_string());
    if (found == audio_captures_.end()) {
        return make_success_response(
            request.id,
            Json(Json::Object{{"released", Json(false)}}));
    }
    audio_capture_bytes_ -= found->second.pcm.size();
    audio_captures_.erase(found);
    return make_success_response(
        request.id,
        Json(Json::Object{{"released", Json(true)}}));
}

std::string CommandDispatcher::handle_set_items(
    const Request& request,
    const bool single_item) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }

    Json::Array item_values;
    if (single_item) {
        Json::Object item;
        for (const std::string_view field :
             {"effect", "item", "value"}) {
            const Json* value = find_field(request.params, field);
            if (value == nullptr) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    std::string(field) + " is required.");
            }
            item.emplace(std::string(field), *value);
        }
        item_values.emplace_back(Json(std::move(item)));
    } else {
        const Json* items = find_field(request.params, "items");
        if (items == nullptr || !items->is_array()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "items must be a JSON array.");
        }
        item_values = items->as_array();
    }
    if (item_values.empty() || item_values.size() > kMaxItemUpdates) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "items must contain between 1 and 128 updates.");
    }

    std::vector<ObjectItemUpdate> updates;
    updates.reserve(item_values.size());
    std::set<std::string, std::less<>> unique_items;
    for (std::size_t index = 0U;
         index < item_values.size();
         ++index) {
        if (!item_values[index].is_object()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "Each item update must be a JSON object.",
                Json::Object{
                    {"failed_item_index",
                     Json(static_cast<std::int64_t>(index))},
                });
        }
        const Json::Object& item = item_values[index].as_object();
        const Json* effect = find_field(item, "effect");
        const Json* name = find_field(item, "item");
        const Json* value = find_field(item, "value");
        if (effect == nullptr || !effect->is_string() ||
            name == nullptr || !name->is_string() ||
            value == nullptr || !value->is_string() ||
            !valid_item_name(effect->as_string()) ||
            !valid_item_name(name->as_string()) ||
            value->as_string().size() > kMaxAliasBytes ||
            value->as_string().find('\0') != std::string::npos) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "effect, item, and value must be valid strings within their limits.",
                Json::Object{
                    {"failed_item_index",
                     Json(static_cast<std::int64_t>(index))},
                });
        }
        const std::string key =
            effect->as_string() + '\0' + name->as_string();
        if (!unique_items.insert(key).second) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "The same effect item must not be updated twice.",
                Json::Object{
                    {"failed_item_index",
                     Json(static_cast<std::int64_t>(index))},
                });
        }
        updates.push_back(ObjectItemUpdate{
            utf8_to_wide(effect->as_string()),
            utf8_to_wide(name->as_string()),
            value->as_string(),
        });
    }

    const ObjectMutationResult result = sdk_.set_object_items(
        target.target.revision,
        target.target.index,
        updates);
    return mutation_response(
        request.id,
        result,
        target.target.index);
}

std::string CommandDispatcher::handle_set_name(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const Json* value = find_field(request.params, "name");
    if (value == nullptr ||
        (!value->is_null() && !value->is_string())) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "name must be null or a UTF-8 string.");
    }
    std::optional<std::wstring> name;
    try {
        if (value->is_null()) {
            name = std::wstring();
        } else {
            if (value->as_string().size() > 4096U ||
                value->as_string().find('\0') !=
                    std::string::npos) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "name exceeds the supported size limit.");
            }
            name = utf8_to_wide(value->as_string());
        }
    } catch (const std::exception&) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "name must contain valid UTF-8.");
    }
    return mutation_response(
        request.id,
        sdk_.set_object_name(
            target.target.revision,
            target.target.index,
            name),
        target.target.index);
}

std::string CommandDispatcher::handle_move(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const Json* layer = find_field(request.params, "layer");
    const Json* frame = find_field(request.params, "frame");
    constexpr std::int64_t max_int =
        std::numeric_limits<int>::max();
    if (layer == nullptr || !layer->is_integer() ||
        frame == nullptr || !frame->is_integer() ||
        layer->as_integer() < 0 ||
        layer->as_integer() > max_int ||
        frame->as_integer() < 0 ||
        frame->as_integer() > max_int) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "layer and frame must be non-negative supported integers.");
    }
    const ObjectMutationResult result = sdk_.move_object(
        target.target.revision,
        target.target.index,
        static_cast<int>(layer->as_integer()),
        static_cast<int>(frame->as_integer()));
    std::optional<std::size_t> updated_index;
    if (result.ok && result.current_revision >= 0) {
        const SnapshotResult snapshot = sdk_.get_snapshot();
        if (snapshot.ok &&
            snapshot.revision == result.current_revision) {
            for (std::size_t index = 0U;
                 index < snapshot.objects.size();
                 ++index) {
                if (snapshot.objects[index].layer ==
                        layer->as_integer() &&
                    snapshot.objects[index].frame_start ==
                        frame->as_integer()) {
                    if (updated_index.has_value()) {
                        updated_index.reset();
                        break;
                    }
                    updated_index = index;
                }
            }
        }
    }
    return mutation_response(
        request.id,
        result,
        updated_index);
}

std::string CommandDispatcher::handle_delete(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const ObjectMutationResult result = sdk_.delete_object(
        target.target.revision,
        target.target.index);
    return mutation_response(request.id, result);
}

std::string CommandDispatcher::handle_effect_mutation(
    const Request& request,
    const bool add) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const std::string_view field = add ? "effect" : "selector";
    const Json* value = find_field(request.params, field);
    if (value == nullptr || !value->is_string() ||
        !valid_item_name(value->as_string())) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            std::string(field) +
                " must be a non-empty effect name without line breaks.");
    }
    const std::wstring wide_value =
        utf8_to_wide(value->as_string());
    ObjectMutationResult result;
    if (add) {
        std::vector<EffectInitialItem> initial_items;
        if (const Json* items =
                find_field(request.params, "items");
            items != nullptr) {
            if (!items->is_object() ||
                items->as_object().empty() ||
                items->as_object().size() >
                    kMaxItemUpdates) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "items must be a non-empty object within the item update limit.");
            }
            initial_items.reserve(items->as_object().size());
            for (const auto& [item_name, item_value] :
                 items->as_object()) {
                if (!valid_item_name(item_name) ||
                    !item_value.is_string() ||
                    item_value.as_string().size() >
                        kMaxAliasBytes ||
                    item_value.as_string().find('\0') !=
                        std::string::npos) {
                    return make_error_response(
                        request.id,
                        "INVALID_ARGUMENT",
                        "Every initial effect item must map a valid item name to a string raw value.");
                }
                initial_items.push_back(EffectInitialItem{
                    utf8_to_wide(item_name),
                    item_value.as_string(),
                });
            }
        }
        result = initial_items.empty()
                     ? sdk_.add_object_effect(
                           target.target.revision,
                           target.target.index,
                           wide_value)
                     : sdk_.add_object_effect_with_items(
                           target.target.revision,
                           target.target.index,
                           wide_value,
                           initial_items);
    } else {
        result = sdk_.delete_object_effect(
            target.target.revision,
            target.target.index,
            wide_value);
    }
    return mutation_response(
        request.id,
        result,
        target.target.index);
}

std::string CommandDispatcher::handle_effect_enabled(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const Json* selector =
        find_field(request.params, "selector");
    const Json* enabled =
        find_field(request.params, "enabled");
    if (selector == nullptr || !selector->is_string() ||
        !valid_item_name(selector->as_string()) ||
        enabled == nullptr || !enabled->is_bool()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "selector must be a valid effect selector and enabled must be a boolean.");
    }
    return mutation_response(
        request.id,
        sdk_.set_object_effect_enabled(
            target.target.revision,
            target.target.index,
            utf8_to_wide(selector->as_string()),
            enabled->as_bool()),
        target.target.index);
}

std::string CommandDispatcher::handle_sections(
    const Request& request,
    const std::string_view action) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    if (action == "list") {
        ObjectSectionsResult result =
            sdk_.get_object_sections(
                target.target.revision,
                target.target.index);
        if (!result.ok) {
            Json::Object details;
            if (result.revision > 0) {
                details.emplace(
                    "current_revision",
                    Json(result.revision));
            }
            return make_error_response(
                request.id,
                result.error_code,
                result.error_message,
                std::move(details),
                result.retryable);
        }
        Json::Array sections;
        sections.reserve(result.sections.size());
        for (const ObjectSectionInfo& section :
             result.sections) {
            sections.emplace_back(Json(Json::Object{
                {"frame", Json(section.frame)},
                {"index", Json(section.index)},
            }));
        }
        return make_success_response(
            request.id,
            Json(Json::Object{
                {"count",
                 Json(static_cast<std::int64_t>(
                     sections.size()))},
                {"revision", Json(result.revision)},
                {"sections", Json(std::move(sections))},
            }));
    }

    const Json* frame =
        find_field(request.params, "frame");
    const Json* section =
        find_field(request.params, "section");
    if ((action == "create" || action == "move") &&
        (frame == nullptr || !frame->is_integer() ||
         frame->as_integer() < 0 ||
         frame->as_integer() >
             std::numeric_limits<int>::max())) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "frame must be a non-negative supported integer.");
    }
    if ((action == "delete" || action == "move") &&
        (section == nullptr || !section->is_integer() ||
         section->as_integer() <= 0 ||
         section->as_integer() >
             std::numeric_limits<int>::max())) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "section must identify a positive middle boundary index.");
    }
    ObjectMutationResult result;
    if (action == "create") {
        result = sdk_.create_object_section(
            target.target.revision,
            target.target.index,
            static_cast<int>(frame->as_integer()));
    } else if (action == "delete") {
        result = sdk_.delete_object_section(
            target.target.revision,
            target.target.index,
            static_cast<int>(section->as_integer()));
    } else {
        result = sdk_.move_object_section(
            target.target.revision,
            target.target.index,
            static_cast<int>(section->as_integer()),
            static_cast<int>(frame->as_integer()));
    }
    return mutation_response(
        request.id,
        result,
        target.target.index);
}

std::string CommandDispatcher::handle_structural_edit(
    const Request& request,
    const std::string_view action) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }

    StructuralEditResult result;
    if (action == "duration") {
        const Json* duration =
            find_field(request.params, "duration");
        if (duration == nullptr || !duration->is_integer() ||
            duration->as_integer() <= 0 ||
            duration->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "duration must be a positive supported frame count.");
        }
        result = sdk_.set_object_duration(
            target.target.revision,
            target.target.index,
            static_cast<int>(duration->as_integer()));
    } else if (action == "trim") {
        const Json* frame_start =
            find_field(request.params, "frame_start");
        const Json* frame_end =
            find_field(request.params, "frame_end");
        if (frame_start == nullptr ||
            !frame_start->is_integer() ||
            frame_end == nullptr ||
            !frame_end->is_integer() ||
            frame_start->as_integer() < 0 ||
            frame_end->as_integer() <
                frame_start->as_integer() ||
            frame_end->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "frame_start/frame_end must form a supported inclusive range.");
        }
        std::optional<double> source_position;
        if (const Json* value =
                find_field(request.params, "source_position");
            value != nullptr) {
            if (!value->is_number() ||
                !std::isfinite(value->as_number()) ||
                value->as_number() < 0.0) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "source_position must be a finite non-negative number.");
            }
            source_position = value->as_number();
        }
        result = sdk_.trim_media_object(
            target.target.revision,
            target.target.index,
            static_cast<int>(frame_start->as_integer()),
            static_cast<int>(frame_end->as_integer()),
            source_position);
    } else {
        const Json* selectors =
            find_field(request.params, "selectors");
        if (selectors == nullptr || !selectors->is_array() ||
            selectors->as_array().empty() ||
            selectors->as_array().size() >
                kMaxInspectEffects) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "selectors must be a non-empty complete effect order.");
        }
        std::vector<std::wstring> order;
        order.reserve(selectors->as_array().size());
        std::set<std::string, std::less<>> unique;
        for (const Json& selector :
             selectors->as_array()) {
            if (!selector.is_string() ||
                !valid_item_name(selector.as_string()) ||
                !unique.insert(selector.as_string()).second) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "selectors must contain unique valid effect selectors.");
            }
            order.push_back(
                utf8_to_wide(selector.as_string()));
        }
        result = sdk_.reorder_object_effects(
            target.target.revision,
            target.target.index,
            order);
    }

    if (!result.ok) {
        Json::Object details;
        if (result.current_revision >= 0) {
            details.emplace(
                "current_revision",
                Json(result.current_revision));
        }
        return make_error_response(
            request.id,
            result.error_code,
            result.error_message,
            std::move(details),
            result.retryable);
    }
    Json::Array effect_order;
    effect_order.reserve(result.effect_order.size());
    for (std::string& selector : result.effect_order) {
        effect_order.emplace_back(Json(std::move(selector)));
    }
    Json updated_object(nullptr);
    if (result.current_revision >= 0) {
        const SnapshotResult snapshot = sdk_.get_snapshot();
        if (snapshot.ok &&
            snapshot.revision == result.current_revision) {
            std::optional<std::size_t> match;
            for (std::size_t index = 0U;
                 index < snapshot.objects.size();
                 ++index) {
                const SnapshotObject& object =
                    snapshot.objects[index];
                if (object.layer == result.layer &&
                    object.frame_start == result.frame_start &&
                    object.frame_end == result.frame_end) {
                    if (match.has_value()) {
                        match.reset();
                        break;
                    }
                    match = index;
                }
            }
            if (match.has_value()) {
                updated_object = Json(Json::Object{
                    {"object_id",
                     Json("obj-" +
                          std::to_string(
                              result.current_revision) +
                          "-" +
                          std::to_string(*match))},
                    {"revision",
                     Json(result.current_revision)},
                });
            }
        }
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"backend", Json("verified_alias_replacement")},
            {"effect_order", Json(std::move(effect_order))},
            {"frame_end", Json(result.frame_end)},
            {"frame_start", Json(result.frame_start)},
            {"layer", Json(result.layer)},
            {"revision",
             result.current_revision >= 0
                 ? Json(result.current_revision)
                 : Json(nullptr)},
            {"snapshot_required",
             Json(result.current_revision < 0)},
            {"source_position",
             result.has_source_position
                 ? Json(result.source_position)
                 : Json(nullptr)},
            {"updated_object", std::move(updated_object)},
            {"undo_unit", Json("single_edit_section")},
            {"undo_grouped", Json(true)},
            {"warnings",
             Json(Json::Array{
                 Json("SDK_NATIVE_SETTER_UNAVAILABLE"),
             })},
        }));
}

std::string CommandDispatcher::handle_timeline_transaction(
    const Request& request,
    const bool apply) {
    const Json* expected =
        find_field(request.params, "expected_revision");
    const Json* commands =
        find_field(request.params, "commands");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0 ||
        commands == nullptr || !commands->is_array() ||
        commands->as_array().empty() ||
        commands->as_array().size() >
            kMaxTimelineCommands) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "expected_revision and 1..4096 transaction commands are required.");
    }

    std::vector<TimelineCommand> parsed_commands;
    parsed_commands.reserve(commands->as_array().size());
    for (std::size_t command_index = 0U;
         command_index < commands->as_array().size();
         ++command_index) {
        const Json& command_value =
            commands->as_array()[command_index];
        if (!command_value.is_object()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "Every transaction command must be an object.",
                Json::Object{
                    {"failed_command_index",
                     Json(static_cast<std::int64_t>(
                         command_index))},
                });
        }
        const Json::Object& command =
            command_value.as_object();
        const Json* op = find_field(command, "op");
        const Json* command_target =
            find_field(command, "target");
        if (op == nullptr || !op->is_string() ||
            command_target == nullptr ||
            !command_target->is_object()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "Every transaction command requires op and target.",
                Json::Object{
                    {"failed_command_index",
                     Json(static_cast<std::int64_t>(
                         command_index))},
                });
        }
        Json::Object target_params{
            {"expected_revision", *expected},
            {"target", *command_target},
        };
        const TargetParseResult target =
            parse_object_target(target_params);
        if (!target.ok) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                target.message,
                Json::Object{
                    {"failed_command_index",
                     Json(static_cast<std::int64_t>(
                         command_index))},
                });
        }

        TimelineCommand parsed;
        parsed.object_index = target.target.index;
        const std::string& operation = op->as_string();
        if (operation == "move" ||
            operation == "object.move") {
            const Json* layer = find_field(command, "layer");
            const Json* frame = find_field(command, "frame");
            if (layer == nullptr || !layer->is_integer() ||
                frame == nullptr || !frame->is_integer() ||
                layer->as_integer() < 0 ||
                layer->as_integer() >
                    std::numeric_limits<int>::max() ||
                frame->as_integer() < 0 ||
                frame->as_integer() >
                    std::numeric_limits<int>::max()) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "A move command requires supported layer/frame integers.",
                    Json::Object{
                        {"failed_command_index",
                         Json(static_cast<std::int64_t>(
                             command_index))},
                    });
            }
            parsed.type = TimelineCommandType::move;
            parsed.layer =
                static_cast<int>(layer->as_integer());
            parsed.frame =
                static_cast<int>(frame->as_integer());
        } else if (
            operation == "delete" ||
            operation == "object.delete") {
            parsed.type = TimelineCommandType::remove;
        } else if (
            operation == "set_items" ||
            operation == "object.set_items") {
            const Json* items = find_field(command, "items");
            if (items == nullptr || !items->is_array() ||
                items->as_array().empty() ||
                items->as_array().size() >
                    kMaxItemUpdates) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "A set_items command requires 1..128 item updates.",
                    Json::Object{
                        {"failed_command_index",
                         Json(static_cast<std::int64_t>(
                             command_index))},
                    });
            }
            parsed.type = TimelineCommandType::set_items;
            std::set<std::string, std::less<>> unique_items;
            for (const Json& item_value :
                 items->as_array()) {
                if (!item_value.is_object()) {
                    return make_error_response(
                        request.id,
                        "INVALID_ARGUMENT",
                        "Transaction item updates must be objects.");
                }
                const Json* effect =
                    find_field(
                        item_value.as_object(),
                        "effect");
                const Json* item =
                    find_field(
                        item_value.as_object(),
                        "item");
                const Json* value =
                    find_field(
                        item_value.as_object(),
                        "value");
                if (effect == nullptr ||
                    !effect->is_string() ||
                    item == nullptr || !item->is_string() ||
                    value == nullptr || !value->is_string() ||
                    !valid_item_name(effect->as_string()) ||
                    !valid_item_name(item->as_string()) ||
                    value->as_string().size() >
                        kMaxAliasBytes ||
                    value->as_string().find('\0') !=
                        std::string::npos) {
                    return make_error_response(
                        request.id,
                        "INVALID_ARGUMENT",
                        "A transaction item update contains invalid values.",
                        Json::Object{
                            {"failed_command_index",
                             Json(static_cast<std::int64_t>(
                                 command_index))},
                        });
                }
                const std::string key =
                    effect->as_string() + '\0' +
                    item->as_string();
                if (!unique_items.insert(key).second) {
                    return make_error_response(
                        request.id,
                        "INVALID_ARGUMENT",
                        "A transaction item is duplicated.");
                }
                parsed.updates.push_back(ObjectItemUpdate{
                    utf8_to_wide(effect->as_string()),
                    utf8_to_wide(item->as_string()),
                    value->as_string(),
                });
            }
        } else if (
            operation == "set_name" ||
            operation == "object.set_name") {
            const Json* name = find_field(command, "name");
            if (name == nullptr ||
                (!name->is_null() && !name->is_string())) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "A set_name command requires a string or null name.");
            }
            parsed.type = TimelineCommandType::set_name;
            if (name->is_null()) {
                parsed.name = std::wstring();
            } else {
                if (name->as_string().size() > 4096U ||
                    name->as_string().find('\0') !=
                        std::string::npos) {
                    return make_error_response(
                        request.id,
                        "INVALID_ARGUMENT",
                        "A transaction object name exceeds the limit.");
                }
                parsed.name =
                    utf8_to_wide(name->as_string());
            }
        } else if (
            operation == "effect.set_enabled" ||
            operation == "object.effect.set_enabled") {
            const Json* selector =
                find_field(command, "selector");
            const Json* enabled =
                find_field(command, "enabled");
            if (selector == nullptr ||
                !selector->is_string() ||
                !valid_item_name(selector->as_string()) ||
                enabled == nullptr ||
                !enabled->is_bool()) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "An effect state command requires selector/enabled.");
            }
            parsed.type =
                TimelineCommandType::set_effect_enabled;
            parsed.effect_selector =
                utf8_to_wide(selector->as_string());
            parsed.enabled = enabled->as_bool();
        } else {
            return make_error_response(
                request.id,
                operation == "split" ||
                        operation == "trim" ||
                        operation == "object.split_media" ||
                        operation == "media.trim"
                    ? "STRUCTURAL_EDIT_UNSAFE"
                    : "INVALID_ARGUMENT",
                "This operation is not available inside a transaction.",
                Json::Object{
                    {"failed_command_index",
                     Json(static_cast<std::int64_t>(
                         command_index))},
                    {"operation", Json(operation)},
                });
        }
        parsed_commands.push_back(std::move(parsed));
    }
    return timeline_transaction_response(
        request.id,
        sdk_.run_timeline_transaction(
            expected->as_integer(),
            parsed_commands,
            apply),
        apply);
}

std::string CommandDispatcher::handle_edit_plan(
    const Request& request,
    const bool apply) {
    const Json* expected =
        find_field(request.params, "expected_revision");
    const Json* commands = find_field(request.params, "commands");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0 || commands == nullptr ||
        !commands->is_array() || commands->as_array().empty() ||
        commands->as_array().size() > kMaxBatchCommands) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "expected_revision and 1..128 edit-plan commands are required.");
    }

    const auto parse_updates = [&](
        const Json::Object& source,
        std::vector<ObjectItemUpdate>& output,
        const bool allow_empty) -> std::string {
        const Json* items = find_field(source, "items");
        if (items == nullptr) {
            return allow_empty ? std::string() :
                std::string("items is required.");
        }
        if (!items->is_array() ||
            items->as_array().size() > kMaxItemUpdates ||
            (!allow_empty && items->as_array().empty())) {
            return "items must contain at most 128 valid item updates.";
        }
        std::set<std::string, std::less<>> unique;
        for (const Json& item_value : items->as_array()) {
            if (!item_value.is_object()) {
                return "Every item update must be an object.";
            }
            const Json* effect =
                find_field(item_value.as_object(), "effect");
            const Json* item =
                find_field(item_value.as_object(), "item");
            const Json* value =
                find_field(item_value.as_object(), "value");
            if (effect == nullptr || !effect->is_string() ||
                item == nullptr || !item->is_string() ||
                value == nullptr || !value->is_string() ||
                !valid_item_name(effect->as_string()) ||
                !valid_item_name(item->as_string()) ||
                value->as_string().size() > kMaxAliasBytes ||
                value->as_string().find('\0') != std::string::npos) {
                return "An edit-plan item update is invalid.";
            }
            const std::string key =
                effect->as_string() + '\0' + item->as_string();
            if (!unique.insert(key).second) {
                return "An edit-plan item update is duplicated.";
            }
            output.push_back(ObjectItemUpdate{
                utf8_to_wide(effect->as_string()),
                utf8_to_wide(item->as_string()),
                value->as_string(),
            });
        }
        return {};
    };

    const auto parse_effects = [&parse_updates](
        const Json::Object& source,
        std::vector<EditPlanEffect>& output) -> std::string {
        const Json* effects = find_field(source, "effects");
        if (effects == nullptr) {
            return {};
        }
        if (!effects->is_array() ||
            effects->as_array().size() > kMaxCreateEffects) {
            return "effects must contain at most 32 effect definitions.";
        }
        for (const Json& effect_value : effects->as_array()) {
            if (!effect_value.is_object()) {
                return "Every create-time effect must be an object.";
            }
            const Json::Object& source_effect = effect_value.as_object();
            const Json* effect = find_field(source_effect, "effect");
            const Json* enabled = find_field(source_effect, "enabled");
            const Json* scope = find_field(source_effect, "scope");
            const Json* profile = find_field(source_effect, "profile");
            if (effect == nullptr || !effect->is_string() ||
                !valid_item_name(effect->as_string()) ||
                enabled == nullptr || !enabled->is_bool() ||
                scope == nullptr || !scope->is_string() ||
                (profile != nullptr && !profile->is_null() &&
                 (!profile->is_string() ||
                  profile->as_string().size() > kMaxClientIdBytes))) {
                return "A create-time effect definition is invalid.";
            }
            EditPlanEffect parsed_effect;
            parsed_effect.effect = utf8_to_wide(effect->as_string());
            parsed_effect.enabled = enabled->as_bool();
            if (profile != nullptr && profile->is_string()) {
                parsed_effect.profile = profile->as_string();
            }
            if (scope->as_string() == "primary") {
                parsed_effect.scope = EditPlanEffectScope::primary;
            } else if (scope->as_string() == "video") {
                parsed_effect.scope = EditPlanEffectScope::video;
            } else if (scope->as_string() == "audio") {
                parsed_effect.scope = EditPlanEffectScope::audio;
            } else {
                return "A create-time effect scope is invalid.";
            }
            std::vector<ObjectItemUpdate> updates;
            const std::string update_error =
                parse_updates(source_effect, updates, true);
            if (!update_error.empty()) {
                return update_error;
            }
            if (updates.size() > kMaxCreateEffectItems) {
                return "A create-time effect has too many items.";
            }
            for (ObjectItemUpdate& update : updates) {
                if (update.effect != parsed_effect.effect) {
                    return "Create-time items must use their effect name.";
                }
                parsed_effect.items.push_back(EffectInitialItem{
                    std::move(update.item),
                    std::move(update.value),
                });
            }
            output.push_back(std::move(parsed_effect));
        }
        return {};
    };

    std::vector<EditPlanCommand> parsed;
    parsed.reserve(commands->as_array().size());
    std::set<std::string, std::less<>> keys;
    for (std::size_t index = 0U;
         index < commands->as_array().size();
         ++index) {
        const Json& command_value = commands->as_array()[index];
        const auto invalid = [&](const std::string_view message) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                message,
                Json::Object{
                    {"failed_command_index",
                     Json(static_cast<std::int64_t>(index))},
                });
        };
        if (!command_value.is_object()) {
            return invalid("Every edit-plan command must be an object.");
        }
        const Json::Object& command = command_value.as_object();
        const Json* op = find_field(command, "op");
        const Json* key = find_field(command, "key");
        if (op == nullptr || !op->is_string() || key == nullptr ||
            !key->is_string() || key->as_string().empty() ||
            key->as_string().size() > kMaxClientIdBytes ||
            !keys.insert(key->as_string()).second) {
            return invalid(
                "Each edit-plan command requires a unique short key and op.");
        }
        EditPlanCommand item;
        item.key = key->as_string();
        const std::string& operation = op->as_string();
        if (operation == "object.create_from_alias" ||
            operation == "object.create_from_media_file") {
            const Json* layer = find_field(command, "layer");
            const Json* frame = find_field(command, "frame");
            const Json* length = find_field(command, "length");
            if (layer == nullptr || !layer->is_integer() ||
                frame == nullptr || !frame->is_integer() ||
                length == nullptr || !length->is_integer() ||
                layer->as_integer() < 0 || frame->as_integer() < 0 ||
                length->as_integer() <= 0 ||
                layer->as_integer() > std::numeric_limits<int>::max() ||
                frame->as_integer() > std::numeric_limits<int>::max() ||
                length->as_integer() > std::numeric_limits<int>::max()) {
                return invalid("A creation command has invalid placement.");
            }
            item.layer = static_cast<int>(layer->as_integer());
            item.frame = static_cast<int>(frame->as_integer());
            item.length = static_cast<int>(length->as_integer());
            if (operation == "object.create_from_alias") {
                const Json* alias = find_field(command, "alias");
                if (alias == nullptr || !alias->is_string() ||
                    alias->as_string().empty() ||
                    alias->as_string().size() > kMaxAliasBytes ||
                    alias->as_string().find('\0') != std::string::npos ||
                    alias->as_string().find("[Object]") ==
                        std::string::npos ||
                    alias->as_string().find("effect.name=") ==
                        std::string::npos) {
                    return invalid("A planned Alias is invalid.");
                }
                item.type = EditPlanCommandType::create_alias;
                item.alias = alias->as_string();
            } else {
                const MediaPathParseResult media = parse_media_path(command);
                if (!media.ok || !media.regular_file) {
                    return invalid(
                        media.message.empty()
                            ? "A planned media path does not exist."
                            : media.message);
                }
                item.type = EditPlanCommandType::create_media;
                item.file = media.path;
                const std::string update_error =
                    parse_updates(command, item.updates, true);
                if (!update_error.empty()) {
                    return invalid(update_error);
                }
            }
            const std::string effect_error =
                parse_effects(command, item.effects);
            if (!effect_error.empty()) {
                return invalid(effect_error);
            }
            parsed.push_back(std::move(item));
            continue;
        }

        const Json* target_value = find_field(command, "target");
        if (target_value == nullptr || !target_value->is_object()) {
            return invalid("An edit-plan mutation requires target.");
        }
        const TargetParseResult target = parse_object_target(
            Json::Object{
                {"expected_revision", *expected},
                {"target", *target_value},
            });
        if (!target.ok) {
            return invalid(target.message);
        }
        item.object_index = target.target.index;
        if (operation == "object.update") {
            item.type = EditPlanCommandType::update;
            const std::string update_error =
                parse_updates(command, item.updates, true);
            if (!update_error.empty()) {
                return invalid(update_error);
            }
            if (const Json* name = find_field(command, "name");
                name != nullptr) {
                if (!name->is_string() ||
                    name->as_string().size() > 4096U ||
                    name->as_string().find('\0') != std::string::npos) {
                    return invalid("A planned object name is invalid.");
                }
                item.name = utf8_to_wide(name->as_string());
            }
            if (item.updates.empty() && !item.name.has_value()) {
                return invalid("An object update has no changed values.");
            }
        } else if (operation == "object.move") {
            const Json* layer = find_field(command, "layer");
            const Json* frame = find_field(command, "frame");
            if (layer == nullptr || !layer->is_integer() ||
                frame == nullptr || !frame->is_integer() ||
                layer->as_integer() < 0 || frame->as_integer() < 0 ||
                layer->as_integer() > std::numeric_limits<int>::max() ||
                frame->as_integer() > std::numeric_limits<int>::max()) {
                return invalid("A planned move has invalid layer/frame.");
            }
            item.type = EditPlanCommandType::move;
            item.layer = static_cast<int>(layer->as_integer());
            item.frame = static_cast<int>(frame->as_integer());
        } else if (operation == "object.delete") {
            item.type = EditPlanCommandType::remove;
        } else if (operation == "object.effect.set_enabled") {
            const Json* selector = find_field(command, "selector");
            const Json* enabled = find_field(command, "enabled");
            if (selector == nullptr || !selector->is_string() ||
                !valid_item_name(selector->as_string()) ||
                enabled == nullptr || !enabled->is_bool()) {
                return invalid("A planned effect state is invalid.");
            }
            item.type = EditPlanCommandType::set_effect_enabled;
            item.effect = utf8_to_wide(selector->as_string());
            item.enabled = enabled->as_bool();
        } else if (operation == "object.effect.add") {
            const Json* effect = find_field(command, "effect");
            if (effect == nullptr || !effect->is_string() ||
                !valid_item_name(effect->as_string())) {
                return invalid("A planned effect name is invalid.");
            }
            item.type = EditPlanCommandType::add_effect;
            item.effect = utf8_to_wide(effect->as_string());
            if (const Json* enabled = find_field(command, "enabled");
                enabled != nullptr) {
                if (!enabled->is_bool()) {
                    return invalid("A planned effect enabled value is invalid.");
                }
                item.enabled = enabled->as_bool();
            }
            std::vector<ObjectItemUpdate> updates;
            const std::string update_error =
                parse_updates(command, updates, true);
            if (!update_error.empty()) {
                return invalid(update_error);
            }
            for (ObjectItemUpdate& update : updates) {
                if (update.effect != item.effect) {
                    return invalid(
                        "Initial effect items must use the created effect name.");
                }
                item.effect_items.push_back(EffectInitialItem{
                    std::move(update.item),
                    std::move(update.value),
                });
            }
        } else {
            return invalid("The edit plan contains an unsupported operation.");
        }
        parsed.push_back(std::move(item));
    }

    const EditPlanResult result = sdk_.run_edit_plan(
        expected->as_integer(),
        parsed,
        apply);
    Json::Object rollback{
        {"attempted", Json(result.rollback_attempted)},
        {"complete", Json(result.rollback_complete)},
        {"restored_count",
         Json(static_cast<std::int64_t>(result.restored_count))},
        {"gui_undo_required", Json(result.gui_undo_required)},
    };
    if (!result.ok) {
        Json::Object details{
            {"rollback", Json(rollback)},
        };
        if (result.failed_command_index !=
            std::numeric_limits<std::size_t>::max()) {
            details.emplace(
                "failed_command_index",
                Json(static_cast<std::int64_t>(
                    result.failed_command_index)));
        }
        if (result.current_revision >= 0) {
            details.emplace(
                "current_revision",
                Json(result.current_revision));
        }
        return make_error_response(
            request.id,
            result.error_code,
            result.error_message,
            std::move(details),
            result.retryable);
    }
    if (!apply) {
        return make_success_response(
            request.id,
            Json(Json::Object{
                {"valid", Json(result.valid)},
                {"command_count",
                 Json(static_cast<std::int64_t>(parsed.size()))},
                {"warnings", Json(Json::Array{})},
            }));
    }
    Json::Array command_results;
    for (std::size_t index = 0U; index < parsed.size(); ++index) {
        command_results.emplace_back(Json(Json::Object{
            {"command_index", Json(static_cast<std::int64_t>(index))},
            {"key", Json(parsed[index].key)},
            {"status", Json("applied")},
        }));
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"applied_count",
             Json(static_cast<std::int64_t>(result.applied_count))},
            {"atomic", Json(false)},
            {"commands", Json(std::move(command_results))},
            {"revision",
             result.current_revision >= 0
                 ? Json(result.current_revision)
                 : Json(nullptr)},
            {"rollback", Json(std::move(rollback))},
            {"snapshot_required", Json(result.current_revision < 0)},
            {"undo_grouped", Json(true)},
            {"undo_unit", Json("single_edit_section")},
            {"warnings", Json(Json::Array{})},
        }));
}

std::string CommandDispatcher::handle_timeline_shift(
    const Request& request,
    const std::string_view action) {
    const Json* expected =
        find_field(request.params, "expected_revision");
    if (expected == nullptr || !expected->is_integer() ||
        expected->as_integer() <= 0) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "expected_revision must be a positive integer.");
    }
    SnapshotResult snapshot = sdk_.get_snapshot();
    if (!snapshot.ok) {
        return make_error_response(
            request.id,
            snapshot.error_code,
            snapshot.error_message,
            {},
            snapshot.retryable);
    }
    if (snapshot.revision != expected->as_integer()) {
        return make_error_response(
            request.id,
            "STALE_PROJECT_STATE",
            "The project changed before the timeline operation was prepared.",
            Json::Object{
                {"current_revision", Json(snapshot.revision)},
            },
            true);
    }

    std::set<std::string, std::less<>> selected_ids;
    if (const Json* value =
            find_field(request.params, "object_ids");
        value != nullptr) {
        if (!value->is_array() ||
            value->as_array().size() >
                kMaxSnapshotObjects) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "object_ids exceeds the supported group size.");
        }
        for (const Json& item : value->as_array()) {
            if (!item.is_string() ||
                item.as_string().empty()) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    "Every grouped object_id must be a string.");
            }
            selected_ids.insert(item.as_string());
        }
    }
    std::optional<int> layer_start;
    std::optional<int> layer_end;
    for (const auto [name, destination] :
         std::initializer_list<
             std::pair<std::string_view, std::optional<int>*>>{
             {"layer_start", &layer_start},
             {"layer_end", &layer_end},
         }) {
        if (const Json* value = find_field(request.params, name);
            value != nullptr) {
            if (!value->is_integer() ||
                value->as_integer() < 0 ||
                value->as_integer() >
                    std::numeric_limits<int>::max()) {
                return make_error_response(
                    request.id,
                    "INVALID_ARGUMENT",
                    std::string(name) +
                        " must be a supported non-negative integer.");
            }
            *destination =
                static_cast<int>(value->as_integer());
        }
    }
    if (layer_start.has_value() && layer_end.has_value() &&
        *layer_start > *layer_end) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "layer_start must not exceed layer_end.");
    }
    const auto in_scope =
        [&](const SnapshotObject& object) {
            return (!layer_start.has_value() ||
                    object.layer >= *layer_start) &&
                   (!layer_end.has_value() ||
                    object.layer <= *layer_end) &&
                   (selected_ids.empty() ||
                    selected_ids.contains(object.object_id));
        };

    std::int64_t range_start = 0;
    std::int64_t range_end = -1;
    std::int64_t delta = 0;
    if (action == "shift_after") {
        const Json* frame =
            find_field(request.params, "frame");
        const Json* delta_value =
            find_field(request.params, "delta");
        if (frame == nullptr || !frame->is_integer() ||
            frame->as_integer() < 0 ||
            delta_value == nullptr ||
            !delta_value->is_integer() ||
            delta_value->as_integer() == 0 ||
            delta_value->as_integer() <
                -std::numeric_limits<int>::max() ||
            delta_value->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "shift_after requires a frame and a non-zero supported delta.");
        }
        range_start = frame->as_integer();
        delta = delta_value->as_integer();
    } else if (action == "ripple_insert") {
        const Json* frame =
            find_field(request.params, "frame");
        const Json* length =
            find_field(request.params, "length");
        if (frame == nullptr || !frame->is_integer() ||
            frame->as_integer() < 0 ||
            length == nullptr || !length->is_integer() ||
            length->as_integer() <= 0 ||
            length->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "ripple_insert requires a frame and positive length.");
        }
        range_start = frame->as_integer();
        delta = length->as_integer();
    } else {
        const Json* start =
            find_field(request.params, "frame_start");
        const Json* end =
            find_field(request.params, "frame_end");
        if (start == nullptr || !start->is_integer() ||
            end == nullptr || !end->is_integer() ||
            start->as_integer() < 0 ||
            end->as_integer() < start->as_integer() ||
            end->as_integer() >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "The operation requires an inclusive frame_start/frame_end range.");
        }
        range_start = start->as_integer();
        range_end = end->as_integer();
        delta = -(range_end - range_start + 1);
    }

    std::vector<TimelineCommand> commands;
    commands.reserve(snapshot.objects.size());
    std::set<std::string, std::less<>> found_ids;
    for (std::size_t index = 0U;
         index < snapshot.objects.size();
         ++index) {
        const SnapshotObject& object =
            snapshot.objects[index];
        if (!in_scope(object)) {
            continue;
        }
        found_ids.insert(object.object_id);
        if (action == "ripple_delete") {
            if (object.frame_start >= range_start &&
                object.frame_end <= range_end) {
                TimelineCommand command;
                command.type = TimelineCommandType::remove;
                command.object_index = index;
                commands.push_back(std::move(command));
                continue;
            }
            if (object.frame_start <= range_end &&
                object.frame_end >= range_start) {
                return make_error_response(
                    request.id,
                    "STRUCTURAL_EDIT_UNSAFE",
                    "ripple_delete would cut through an object; split or trim it explicitly first.",
                    Json::Object{
                        {"object_id", Json(object.object_id)},
                    });
            }
        } else if (
            action == "close_gap" &&
            object.frame_start <= range_end &&
            object.frame_end >= range_start) {
            return make_error_response(
                request.id,
                "GAP_NOT_EMPTY",
                "close_gap requires an empty range.",
                Json::Object{
                    {"object_id", Json(object.object_id)},
                });
        }
        const std::int64_t threshold =
            range_end >= 0 ? range_end + 1 : range_start;
        if (object.frame_start < threshold) {
            continue;
        }
        const std::int64_t destination =
            static_cast<std::int64_t>(
                object.frame_start) +
            delta;
        if (destination < 0 ||
            destination >
                std::numeric_limits<int>::max()) {
            return make_error_response(
                request.id,
                "INVALID_ARGUMENT",
                "The timeline shift would move an object outside the supported range.",
                Json::Object{
                    {"object_id", Json(object.object_id)},
                });
        }
        TimelineCommand command;
        command.type = TimelineCommandType::move;
        command.object_index = index;
        command.layer = object.layer;
        command.frame = static_cast<int>(destination);
        commands.push_back(std::move(command));
    }
    if (!selected_ids.empty() &&
        found_ids != selected_ids) {
        return make_error_response(
            request.id,
            "OBJECT_NOT_FOUND",
            "One or more grouped object_ids are not in the current snapshot.");
    }
    if (commands.empty()) {
        return make_success_response(
            request.id,
            Json(Json::Object{
                {"applied_count", Json(0)},
                {"revision", Json(snapshot.revision)},
                {"snapshot_required", Json(false)},
                {"undo_grouped", Json(false)},
                {"valid", Json(true)},
                {"warnings", Json(Json::Array{})},
            }));
    }
    return timeline_transaction_response(
        request.id,
        sdk_.run_timeline_transaction(
            snapshot.revision,
            commands,
            true),
        true);
}

std::string CommandDispatcher::handle_split_media(
    const Request& request) {
    const TargetParseResult target =
        parse_object_target(request.params);
    if (!target.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            target.message);
    }
    const Json* frame = find_field(request.params, "frame");
    if (frame == nullptr || !frame->is_integer() ||
        frame->as_integer() < 0 ||
        frame->as_integer() > std::numeric_limits<int>::max()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "frame must be a non-negative supported integer.");
    }
    const SplitMediaResult result = sdk_.split_media_object(
        target.target.revision,
        target.target.index,
        static_cast<int>(frame->as_integer()));
    if (!result.ok) {
        Json::Object details;
        if (result.current_revision >= 0) {
            details.emplace(
                "current_revision",
                Json(result.current_revision));
        }
        return make_error_response(
            request.id,
            result.error_code,
            result.error_message,
            std::move(details),
            result.retryable);
    }
    Json left_object(nullptr);
    Json right_object(nullptr);
    Json::Array warnings{
        Json("SDK_NATIVE_SPLIT_UNAVAILABLE_ALIAS_FALLBACK"),
    };
    bool snapshot_required = result.current_revision < 0;
    if (result.current_revision >= 0) {
        const SnapshotResult after = sdk_.get_snapshot();
        if (after.ok &&
            after.revision == result.current_revision) {
            std::vector<const SnapshotObject*> left_matches;
            std::vector<const SnapshotObject*> right_matches;
            for (const SnapshotObject& object : after.objects) {
                if (object.layer != result.layer) {
                    continue;
                }
                if (object.frame_start == result.left_start &&
                    object.frame_end == result.left_end) {
                    left_matches.push_back(&object);
                }
                if (object.frame_start == result.right_start &&
                    object.frame_end == result.right_end) {
                    right_matches.push_back(&object);
                }
            }
            if (left_matches.size() == 1U) {
                left_object = Json(Json::Object{
                    {"object_id",
                     Json(left_matches.front()->object_id)},
                    {"revision", Json(after.revision)},
                });
            }
            if (right_matches.size() == 1U) {
                right_object = Json(Json::Object{
                    {"object_id",
                     Json(right_matches.front()->object_id)},
                    {"revision", Json(after.revision)},
                });
            }
            snapshot_required =
                left_matches.size() != 1U ||
                right_matches.size() != 1U;
            if (snapshot_required) {
                warnings.emplace_back(
                    Json("UPDATED_OBJECT_REFERENCE_AMBIGUOUS"));
            }
        } else {
            snapshot_required = true;
            warnings.emplace_back(
                Json("POST_MUTATION_SNAPSHOT_CHANGED"));
        }
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"left",
             Json(Json::Object{
                 {"frame_end", Json(result.left_end)},
                 {"frame_start", Json(result.left_start)},
                 {"layer", Json(result.layer)},
                 {"object", std::move(left_object)},
             })},
            {"playback_rate", Json(result.playback_rate)},
            {"revision",
             result.current_revision >= 0
                 ? Json(result.current_revision)
                 : Json(nullptr)},
            {"right",
             Json(Json::Object{
                 {"frame_end", Json(result.right_end)},
                 {"frame_start", Json(result.right_start)},
                 {"layer", Json(result.layer)},
                 {"object", std::move(right_object)},
             })},
            {"snapshot_required", Json(snapshot_required)},
            {"source_position",
             Json(Json::Object{
                 {"left", Json(result.source_position_before)},
                 {"right", Json(result.source_position_after)},
             })},
            {"undo_unit", Json("single_edit_section")},
            {"undo_grouped", Json(true)},
            {"warnings", Json(std::move(warnings))},
        }));
}

std::string CommandDispatcher::handle_create_from_alias(
    const Request& request) {
    Json::Array values{Json(request.params)};
    CommandParseResult parsed = parse_create_commands(values, false);
    if (!parsed.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            parsed.message,
            Json::Object{
                {"failed_command_index",
                 Json(static_cast<std::int64_t>(parsed.failed_index))},
            });
    }
    return run_batch(request, std::move(parsed.commands), true);
}

std::string CommandDispatcher::handle_batch(
    const Request& request,
    const bool apply) {
    const auto found = request.params.find("commands");
    if (found == request.params.end() || !found->second.is_array()) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            "commands must be a JSON array.");
    }
    CommandParseResult parsed =
        parse_create_commands(found->second.as_array(), true);
    if (!parsed.ok) {
        return make_error_response(
            request.id,
            "INVALID_ARGUMENT",
            parsed.message,
            Json::Object{
                {"failed_command_index",
                 Json(static_cast<std::int64_t>(parsed.failed_index))},
            });
    }
    return run_batch(request, std::move(parsed.commands), apply);
}

std::string CommandDispatcher::run_batch(
    const Request& request,
    std::vector<CreateAliasCommand> commands,
    const bool apply) {
    const BatchEditResult operation =
        apply
            ? sdk_.apply_create_alias_batch(commands)
            : sdk_.validate_create_alias_batch(commands);
    if (!operation.ok) {
        return make_error_response(
            request.id,
            operation.error_code,
            operation.error_message,
            batch_error_details(operation),
            operation.retryable);
    }

    if (!apply) {
        return make_success_response(
            request.id,
            Json(Json::Object{
                {"alias_semantics", Json("verified_on_apply")},
                {"command_count",
                 Json(static_cast<std::int64_t>(commands.size()))},
                {"valid", Json(true)},
                {"validation_scope",
                 Json("structure_and_requested_placement")},
            }));
    }

    Json::Array created;
    created.reserve(commands.size());
    for (std::size_t index = 0U; index < commands.size(); ++index) {
        Json::Object item{
            {"command_index", Json(static_cast<std::int64_t>(index))},
        };
        if (!commands[index].client_id.empty()) {
            item.emplace("client_id", Json(commands[index].client_id));
        }
        created.emplace_back(Json(std::move(item)));
    }
    return make_success_response(
        request.id,
        Json(Json::Object{
            {"applied_count",
             Json(static_cast<std::int64_t>(operation.applied_count))},
            {"atomic", Json(false)},
            {"created", Json(std::move(created))},
            {"revision", Json(nullptr)},
            {"snapshot_required", Json(true)},
            {"undo_unit", Json("single_edit_section")},
            {"undo_grouped", Json(true)},
            {"warnings",
             Json(Json::Array{
                 Json("FRESH_SNAPSHOT_REQUIRED"),
             })},
        }));
}

}  // namespace aviutl2::live
