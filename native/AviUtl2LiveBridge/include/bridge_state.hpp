#pragma once

#include "command_dispatcher.hpp"
#include "instance_registry.hpp"
#include "pipe_server.hpp"
#include "sdk_adapter.hpp"

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

struct EDIT_HANDLE;

namespace aviutl2::live {

class BridgeState final {
public:
    explicit BridgeState(EDIT_HANDLE* edit_handle);
    ~BridgeState();

    BridgeState(const BridgeState&) = delete;
    BridgeState& operator=(const BridgeState&) = delete;

    [[nodiscard]] bool start(std::string& error_message);
    void stop() noexcept;
    [[nodiscard]] bool running() const noexcept;
    void record_event(std::string_view event_type) noexcept;

private:
    std::uint32_t pid_;
    std::wstring pipe_name_;
    HostSdkAdapter sdk_;
    CommandDispatcher dispatcher_;
    PipeServer pipe_server_;
    InstanceRegistry registry_;
    bool started_ = false;
};

}  // namespace aviutl2::live
