#include "bridge_state.hpp"

#include "logger.hpp"

#include <windows.h>

#include <utility>

namespace aviutl2::live {
namespace {

[[nodiscard]] std::wstring make_pipe_name(const std::uint32_t pid) {
    return L"\\\\.\\pipe\\AviUtl2.LiveBridge." + std::to_wstring(pid);
}

}  // namespace

BridgeState::BridgeState(EDIT_HANDLE* edit_handle)
    : pid_(GetCurrentProcessId()),
      pipe_name_(make_pipe_name(pid_)),
      sdk_(edit_handle),
      dispatcher_(sdk_, pid_),
      pipe_server_(
          pipe_name_,
          [this](
              const PipeServer::ConnectionId connection_id,
              const std::string_view payload) {
              return dispatcher_.handle_payload(
                  connection_id,
                  payload);
          },
          [this](
              const PipeServer::ConnectionId connection_id) {
              dispatcher_.close_connection(connection_id);
          }),
      registry_(pid_, pipe_name_) {}

BridgeState::~BridgeState() {
    stop();
}

bool BridgeState::start(std::string& error_message) {
    if (started_) {
        error_message = "bridge is already started";
        return false;
    }
    dispatcher_.start();
    if (!pipe_server_.start(error_message)) {
        dispatcher_.stop();
        dispatcher_.finish_stop();
        return false;
    }

    int scene_id = 0;
    const ProjectInfoResult project = sdk_.get_project_info();
    if (project.ok) {
        scene_id = project.info.scene_id;
    }
    if (!registry_.publish(scene_id, error_message)) {
        dispatcher_.stop();
        pipe_server_.stop();
        dispatcher_.finish_stop();
        return false;
    }
    started_ = true;
    log_message(LogLevel::info, L"bridge_state", L"started");
    return true;
}

void BridgeState::stop() noexcept {
    if (!started_ && !pipe_server_.running()) {
        return;
    }
    registry_.remove();
    dispatcher_.stop();
    pipe_server_.stop();
    dispatcher_.finish_stop();
    started_ = false;
    log_message(LogLevel::info, L"bridge_state", L"stopped");
}

bool BridgeState::running() const noexcept {
    return started_ && pipe_server_.running();
}

void BridgeState::record_event(
    const std::string_view event_type) noexcept {
    dispatcher_.record_event(event_type);
}

void BridgeState::record_project_event(
    const std::string_view event_type,
    const std::wstring_view project_file_path) noexcept {
    sdk_.observe_project_file_path(project_file_path);
    dispatcher_.record_event(event_type);
}

}  // namespace aviutl2::live
