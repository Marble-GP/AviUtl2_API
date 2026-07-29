#pragma once

#include <functional>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

namespace aviutl2::live {

class PipeServer final {
public:
    using ConnectionId = std::uint64_t;
    using Handler =
        std::function<std::string(ConnectionId, std::string_view)>;
    using LegacyHandler =
        std::function<std::string(std::string_view)>;
    using DisconnectHandler = std::function<void(ConnectionId)>;

    PipeServer(
        std::wstring pipe_name,
        Handler handler,
        DisconnectHandler disconnect_handler = {});
    PipeServer(std::wstring pipe_name, LegacyHandler handler);
    ~PipeServer();

    PipeServer(const PipeServer&) = delete;
    PipeServer& operator=(const PipeServer&) = delete;
    PipeServer(PipeServer&&) = delete;
    PipeServer& operator=(PipeServer&&) = delete;

    [[nodiscard]] bool start(std::string& error_message);
    void stop() noexcept;
    [[nodiscard]] bool running() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace aviutl2::live
