#pragma once

#include "protocol.hpp"
#include "sdk_adapter.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace aviutl2::live {

class CommandDispatcher final {
public:
    using ConnectionId = std::uint64_t;

    CommandDispatcher(SdkAdapter& sdk, std::uint32_t pid) noexcept;

    [[nodiscard]] std::string handle_payload(std::string_view payload);
    [[nodiscard]] std::string handle_payload(
        ConnectionId connection_id,
        std::string_view payload);
    [[nodiscard]] std::string dispatch(const Request& request);
    void start() noexcept;
    void stop() noexcept;
    void finish_stop() noexcept;
    void close_connection(ConnectionId connection_id) noexcept;
    void record_event(std::string_view event_type) noexcept;

private:
    struct CachedOperation final {
        std::string fingerprint;
        std::string response;
    };

    struct SessionState final {
        std::string session_id;
        std::string client_name;
        std::unordered_map<std::string, CachedOperation> operations;
        std::deque<std::string> operation_order;
    };

    struct EventEntry final {
        std::uint64_t sequence = 0U;
        std::int64_t timestamp_ms = 0;
        std::string type;
    };

    [[nodiscard]] std::string dispatch_for_connection(
        ConnectionId connection_id,
        const Request& request);
    [[nodiscard]] std::string dispatch_serialized(
        const Request& request);
    [[nodiscard]] std::string handle_session_open(
        ConnectionId connection_id,
        const Request& request);
    [[nodiscard]] std::string handle_event_watch(
        const Request& request);
    [[nodiscard]] static bool is_mutation_method(
        std::string_view method) noexcept;
    [[nodiscard]] static std::string operation_fingerprint(
        const Request& request);
    [[nodiscard]] static std::string response_with_request_id(
        std::string_view response,
        std::string_view request_id);
    [[nodiscard]] std::optional<std::string> cached_operation_response(
        ConnectionId connection_id,
        const Request& request,
        std::string_view operation_id,
        std::string_view fingerprint);
    void cache_operation_response(
        ConnectionId connection_id,
        std::string operation_id,
        std::string fingerprint,
        std::string response);
    [[nodiscard]] SessionState& ensure_session_locked(
        ConnectionId connection_id);

    [[nodiscard]] Json hello_result() const;
    [[nodiscard]] Json capabilities_result() const;
    [[nodiscard]] std::string handle_effect_catalog(
        const Request& request);
    [[nodiscard]] std::string handle_layers(
        const Request& request);
    [[nodiscard]] std::string handle_layer_update(
        const Request& request);
    [[nodiscard]] std::string handle_runtime_catalog(
        const Request& request,
        std::string_view kind);
    [[nodiscard]] std::string handle_scene(
        const Request& request,
        bool update);
    [[nodiscard]] std::string handle_create_from_alias(
        const Request& request);
    [[nodiscard]] std::string handle_batch(
        const Request& request,
        bool apply);
    [[nodiscard]] std::string handle_snapshot(
        const Request& request);
    [[nodiscard]] std::string handle_set_items(
        const Request& request,
        bool single_item);
    [[nodiscard]] std::string handle_set_name(
        const Request& request);
    [[nodiscard]] std::string handle_move(
        const Request& request);
    [[nodiscard]] std::string handle_delete(
        const Request& request);
    [[nodiscard]] std::string handle_effect_mutation(
        const Request& request,
        bool add);
    [[nodiscard]] std::string handle_effect_enabled(
        const Request& request);
    [[nodiscard]] std::string handle_sections(
        const Request& request,
        std::string_view action);
    [[nodiscard]] std::string handle_split_media(
        const Request& request);
    [[nodiscard]] std::string handle_structural_edit(
        const Request& request,
        std::string_view action);
    [[nodiscard]] std::string handle_timeline_transaction(
        const Request& request,
        bool apply);
    [[nodiscard]] std::string handle_edit_plan(
        const Request& request,
        bool apply);
    [[nodiscard]] std::string handle_timeline_shift(
        const Request& request,
        std::string_view action);
    [[nodiscard]] std::string handle_media_probe(
        const Request& request);
    [[nodiscard]] std::string handle_media_inventory(
        const Request& request);
    [[nodiscard]] std::string handle_media_relink(
        const Request& request);
    [[nodiscard]] std::string handle_create_from_media(
        const Request& request);
    [[nodiscard]] std::string handle_inspect(
        const Request& request);
    [[nodiscard]] std::string handle_frame_render(
        const Request& request);
    [[nodiscard]] std::string handle_frame_read_chunk(
        const Request& request);
    [[nodiscard]] std::string handle_frame_release(
        const Request& request);
    [[nodiscard]] std::string handle_audio_render(
        const Request& request);
    [[nodiscard]] std::string handle_audio_read_chunk(
        const Request& request);
    [[nodiscard]] std::string handle_audio_release(
        const Request& request);
    [[nodiscard]] std::string run_batch(
        const Request& request,
        std::vector<CreateAliasCommand> commands,
        bool apply);
    void cleanup_captures();
    void cleanup_audio_captures();

    struct FrameCapture final {
        int frame = 0;
        int width = 0;
        int height = 0;
        int scene_id = 0;
        std::int64_t revision = 0;
        std::string sha256;
        std::vector<std::uint8_t> png;
        std::chrono::steady_clock::time_point expires_at;
    };

    struct AudioCapture final {
        int frame_start = 0;
        int frame_end = 0;
        int sample_rate = 0;
        int scene_id = 0;
        std::int64_t revision = 0;
        std::string sha256;
        std::vector<std::uint8_t> pcm;
        std::chrono::steady_clock::time_point expires_at;
    };

    SdkAdapter& sdk_;
    std::uint32_t pid_;
    std::uint64_t next_capture_id_ = 1U;
    std::size_t capture_bytes_ = 0U;
    std::map<std::string, FrameCapture, std::less<>> captures_;
    std::uint64_t next_audio_capture_id_ = 1U;
    std::size_t audio_capture_bytes_ = 0U;
    std::map<std::string, AudioCapture, std::less<>>
        audio_captures_;

    std::mutex scheduler_mutex_;
    std::condition_variable scheduler_cv_;
    std::uint64_t next_ticket_ = 0U;
    std::uint64_t serving_ticket_ = 0U;
    bool scheduler_stopping_ = false;

    std::mutex sessions_mutex_;
    std::map<ConnectionId, SessionState> sessions_;

    std::mutex events_mutex_;
    std::condition_variable events_cv_;
    std::deque<EventEntry> events_;
    std::uint64_t next_event_sequence_ = 1U;
    bool events_stopping_ = false;
};

}  // namespace aviutl2::live
