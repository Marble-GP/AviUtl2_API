#include "pipe_server.hpp"

#include "bridge_constants.hpp"
#include "logger.hpp"
#include "protocol.hpp"

#include <windows.h>
#include <sddl.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstddef>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace aviutl2::live {
namespace {

class UniqueHandle final {
public:
    UniqueHandle() noexcept = default;
    explicit UniqueHandle(HANDLE handle) noexcept : handle_(handle) {}
    ~UniqueHandle() {
        reset();
    }

    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;

    UniqueHandle(UniqueHandle&& other) noexcept
        : handle_(std::exchange(other.handle_, INVALID_HANDLE_VALUE)) {}
    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, INVALID_HANDLE_VALUE);
        }
        return *this;
    }

    [[nodiscard]] HANDLE get() const noexcept {
        return handle_;
    }
    [[nodiscard]] bool valid() const noexcept {
        return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
    }
    void reset(HANDLE handle = INVALID_HANDLE_VALUE) noexcept {
        if (valid()) {
            CloseHandle(handle_);
        }
        handle_ = handle;
    }

private:
    HANDLE handle_ = INVALID_HANDLE_VALUE;
};

class LocalMemory final {
public:
    ~LocalMemory() {
        if (pointer_ != nullptr) {
            LocalFree(pointer_);
        }
    }
    LocalMemory(const LocalMemory&) = delete;
    LocalMemory& operator=(const LocalMemory&) = delete;
    LocalMemory() = default;

    [[nodiscard]] void** address() noexcept {
        return &pointer_;
    }
    [[nodiscard]] void* get() const noexcept {
        return pointer_;
    }

private:
    void* pointer_ = nullptr;
};

[[nodiscard]] std::string win32_error_message(
    const std::string_view operation,
    const DWORD error) {
    return std::string(operation) + " failed with Win32 error " +
           std::to_string(error);
}

enum class IoResult {
    completed,
    disconnected,
    stopped,
    failed,
};

[[nodiscard]] IoResult wait_for_overlapped(
    const HANDLE pipe,
    const HANDLE stop_event,
    OVERLAPPED& overlapped,
    DWORD& transferred) noexcept {
    const std::array<HANDLE, 2> handles{stop_event, overlapped.hEvent};
    const DWORD wait_result = WaitForMultipleObjects(
        static_cast<DWORD>(handles.size()),
        handles.data(),
        FALSE,
        INFINITE);
    if (wait_result == WAIT_OBJECT_0) {
        CancelIoEx(pipe, &overlapped);
        WaitForSingleObject(overlapped.hEvent, INFINITE);
        return IoResult::stopped;
    }
    if (wait_result != WAIT_OBJECT_0 + 1U) {
        CancelIoEx(pipe, &overlapped);
        WaitForSingleObject(overlapped.hEvent, INFINITE);
        return IoResult::failed;
    }
    if (!GetOverlappedResult(pipe, &overlapped, &transferred, FALSE)) {
        const DWORD error = GetLastError();
        if (error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA ||
            error == ERROR_PIPE_NOT_CONNECTED ||
            error == ERROR_OPERATION_ABORTED) {
            return error == ERROR_OPERATION_ABORTED ? IoResult::stopped
                                                    : IoResult::disconnected;
        }
        return IoResult::failed;
    }
    return IoResult::completed;
}

[[nodiscard]] IoResult read_exact(
    const HANDLE pipe,
    const HANDLE stop_event,
    void* destination,
    const std::size_t size) {
    auto* bytes = static_cast<std::byte*>(destination);
    std::size_t offset = 0U;
    UniqueHandle io_event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!io_event.valid()) {
        return IoResult::failed;
    }

    while (offset < size) {
        ResetEvent(io_event.get());
        OVERLAPPED overlapped{};
        overlapped.hEvent = io_event.get();
        DWORD transferred = 0U;
        const DWORD requested = static_cast<DWORD>(
            (std::min)(size - offset,
                       static_cast<std::size_t>(
                           std::numeric_limits<DWORD>::max())));
        if (!ReadFile(
                pipe,
                bytes + offset,
                requested,
                &transferred,
                &overlapped)) {
            const DWORD error = GetLastError();
            if (error == ERROR_IO_PENDING) {
                const IoResult result = wait_for_overlapped(
                    pipe,
                    stop_event,
                    overlapped,
                    transferred);
                if (result != IoResult::completed) {
                    return result;
                }
            } else if (
                error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA ||
                error == ERROR_PIPE_NOT_CONNECTED) {
                return IoResult::disconnected;
            } else {
                return IoResult::failed;
            }
        }
        if (transferred == 0U) {
            return IoResult::disconnected;
        }
        offset += transferred;
    }
    return IoResult::completed;
}

[[nodiscard]] IoResult write_exact(
    const HANDLE pipe,
    const HANDLE stop_event,
    const void* source,
    const std::size_t size) {
    const auto* bytes = static_cast<const std::byte*>(source);
    std::size_t offset = 0U;
    UniqueHandle io_event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!io_event.valid()) {
        return IoResult::failed;
    }

    while (offset < size) {
        ResetEvent(io_event.get());
        OVERLAPPED overlapped{};
        overlapped.hEvent = io_event.get();
        DWORD transferred = 0U;
        const DWORD requested = static_cast<DWORD>(
            (std::min)(size - offset,
                       static_cast<std::size_t>(
                           std::numeric_limits<DWORD>::max())));
        if (!WriteFile(
                pipe,
                bytes + offset,
                requested,
                &transferred,
                &overlapped)) {
            const DWORD error = GetLastError();
            if (error == ERROR_IO_PENDING) {
                const IoResult result = wait_for_overlapped(
                    pipe,
                    stop_event,
                    overlapped,
                    transferred);
                if (result != IoResult::completed) {
                    return result;
                }
            } else if (
                error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA ||
                error == ERROR_PIPE_NOT_CONNECTED) {
                return IoResult::disconnected;
            } else {
                return IoResult::failed;
            }
        }
        if (transferred == 0U) {
            return IoResult::disconnected;
        }
        offset += transferred;
    }
    return IoResult::completed;
}

}  // namespace

class PipeServer::Impl final {
public:
    struct ClientWorker final {
        std::thread thread;
        std::shared_ptr<std::atomic_bool> done;
    };

    Impl(
        std::wstring pipe_name,
        Handler handler,
        DisconnectHandler disconnect_handler)
        : pipe_name_(std::move(pipe_name)),
          handler_(std::move(handler)),
          disconnect_handler_(std::move(disconnect_handler)) {}

    ~Impl() {
        stop();
    }

    [[nodiscard]] bool start(std::string& error_message) {
        if (running_.exchange(true, std::memory_order_acq_rel)) {
            error_message = "pipe server is already running";
            return false;
        }

        stop_event_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        ready_event_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!stop_event_.valid() || !ready_event_.valid()) {
            error_message = win32_error_message("CreateEventW", GetLastError());
            running_.store(false, std::memory_order_release);
            stop_event_.reset();
            ready_event_.reset();
            return false;
        }

        worker_ = std::thread([this] { worker_main(); });
        const DWORD ready = WaitForSingleObject(ready_event_.get(), 5000U);
        if (ready != WAIT_OBJECT_0) {
            error_message = "pipe server did not become ready";
            stop();
            return false;
        }
        {
            std::scoped_lock lock(start_error_mutex_);
            if (!start_error_.empty()) {
                error_message = start_error_;
                stop();
                return false;
            }
        }
        return true;
    }

    void stop() noexcept {
        if (stop_event_.valid()) {
            SetEvent(stop_event_.get());
        }
        if (worker_.joinable()) {
            worker_.join();
        }
        std::vector<ClientWorker> clients;
        {
            std::scoped_lock lock(client_mutex_);
            clients.swap(client_workers_);
        }
        for (ClientWorker& client : clients) {
            if (client.thread.joinable()) {
                client.thread.join();
            }
        }
        running_.store(false, std::memory_order_release);
        ready_event_.reset();
        stop_event_.reset();
    }

    [[nodiscard]] bool running() const noexcept {
        return running_.load(std::memory_order_acquire);
    }

private:
    [[nodiscard]] UniqueHandle create_pipe(std::string& error_message) const {
        LocalMemory security_descriptor;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
                L"D:P(A;;GA;;;SY)(A;;GA;;;OW)",
                SDDL_REVISION_1,
                security_descriptor.address(),
                nullptr)) {
            error_message = win32_error_message(
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                GetLastError());
            return {};
        }
        SECURITY_ATTRIBUTES attributes{};
        attributes.nLength = sizeof(attributes);
        attributes.lpSecurityDescriptor = security_descriptor.get();
        attributes.bInheritHandle = FALSE;

        UniqueHandle pipe(CreateNamedPipeW(
            pipe_name_.c_str(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
                PIPE_REJECT_REMOTE_CLIENTS,
            static_cast<DWORD>(kMaxPipeClients),
            64U * 1024U,
            64U * 1024U,
            0U,
            &attributes));
        if (!pipe.valid()) {
            error_message =
                win32_error_message("CreateNamedPipeW", GetLastError());
        }
        return pipe;
    }

    [[nodiscard]] IoResult connect_client(const HANDLE pipe) const {
        UniqueHandle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.valid()) {
            return IoResult::failed;
        }
        OVERLAPPED overlapped{};
        overlapped.hEvent = event.get();
        if (ConnectNamedPipe(pipe, &overlapped)) {
            return IoResult::completed;
        }
        const DWORD error = GetLastError();
        if (error == ERROR_PIPE_CONNECTED) {
            return IoResult::completed;
        }
        if (error != ERROR_IO_PENDING) {
            return IoResult::failed;
        }
        DWORD transferred = 0U;
        return wait_for_overlapped(
            pipe,
            stop_event_.get(),
            overlapped,
            transferred);
    }

    void handle_client(
        const HANDLE pipe,
        const ConnectionId connection_id) const {
        while (WaitForSingleObject(stop_event_.get(), 0U) == WAIT_TIMEOUT) {
            std::array<std::uint8_t, 4> header{};
            const IoResult header_result =
                read_exact(pipe, stop_event_.get(), header.data(), header.size());
            if (header_result != IoResult::completed) {
                return;
            }
            const std::uint32_t length =
                static_cast<std::uint32_t>(header[0]) |
                (static_cast<std::uint32_t>(header[1]) << 8U) |
                (static_cast<std::uint32_t>(header[2]) << 16U) |
                (static_cast<std::uint32_t>(header[3]) << 24U);
            if (length == 0U || length > kMaxPayloadBytes) {
                log_message(
                    LogLevel::warning,
                    L"pipe_server",
                    L"invalid_payload_length");
                return;
            }

            std::string payload(length, '\0');
            const IoResult payload_result =
                read_exact(pipe, stop_event_.get(), payload.data(), payload.size());
            if (payload_result != IoResult::completed) {
                return;
            }

            std::string response;
            try {
                response = handler_(connection_id, payload);
            } catch (...) {
                response = make_error_response(
                    "",
                    "INTERNAL_PLUGIN_ERROR",
                    "The request failed inside the bridge.");
            }
            if (response.empty() || response.size() > kMaxPayloadBytes) {
                response = make_error_response(
                    "",
                    "INTERNAL_PLUGIN_ERROR",
                    "The bridge generated an invalid response.");
            }
            const std::string frame = encode_frame(response);
            if (write_exact(
                    pipe,
                    stop_event_.get(),
                    frame.data(),
                    frame.size()) != IoResult::completed) {
                return;
            }
        }
    }

    void run_client(
        UniqueHandle pipe,
        const ConnectionId connection_id) noexcept {
        try {
            handle_client(pipe.get(), connection_id);
        } catch (...) {
            log_message(
                LogLevel::error,
                L"pipe_server",
                L"client_worker_exception");
        }
        DisconnectNamedPipe(pipe.get());
        if (disconnect_handler_) {
            try {
                disconnect_handler_(connection_id);
            } catch (...) {
                log_message(
                    LogLevel::warning,
                    L"pipe_server",
                    L"disconnect_handler_exception");
            }
        }
        active_clients_.fetch_sub(1U, std::memory_order_acq_rel);
    }

    void reap_finished_clients() noexcept {
        std::vector<std::thread> completed;
        {
            std::scoped_lock lock(client_mutex_);
            for (auto iterator = client_workers_.begin();
                 iterator != client_workers_.end();) {
                if (iterator->done->load(
                        std::memory_order_acquire)) {
                    completed.push_back(
                        std::move(iterator->thread));
                    iterator = client_workers_.erase(iterator);
                } else {
                    ++iterator;
                }
            }
        }
        for (std::thread& thread : completed) {
            if (thread.joinable()) {
                thread.join();
            }
        }
    }

    void worker_main() noexcept {
        try {
            bool signaled_ready = false;
            while (WaitForSingleObject(stop_event_.get(), 0U) == WAIT_TIMEOUT) {
                reap_finished_clients();
                if (active_clients_.load(std::memory_order_acquire) >=
                    kMaxPipeClients) {
                    WaitForSingleObject(stop_event_.get(), 25U);
                    continue;
                }
                std::string error_message;
                UniqueHandle pipe = create_pipe(error_message);
                if (!pipe.valid()) {
                    if (!signaled_ready) {
                        std::scoped_lock lock(start_error_mutex_);
                        start_error_ = std::move(error_message);
                        SetEvent(ready_event_.get());
                    } else {
                        log_message(
                            LogLevel::error,
                            L"pipe_server",
                            L"create_pipe_failed");
                    }
                    running_.store(false, std::memory_order_release);
                    return;
                }
                if (!signaled_ready) {
                    signaled_ready = true;
                    SetEvent(ready_event_.get());
                    log_message(
                        LogLevel::info,
                        L"pipe_server",
                        L"ready");
                }

                const IoResult connected = connect_client(pipe.get());
                if (connected == IoResult::stopped) {
                    break;
                }
                if (connected == IoResult::completed) {
                    const std::uint64_t connection_id =
                        next_connection_id_.fetch_add(
                            1U,
                            std::memory_order_relaxed);
                    active_clients_.fetch_add(1U, std::memory_order_acq_rel);
                    auto done =
                        std::make_shared<std::atomic_bool>(false);
                    std::scoped_lock lock(client_mutex_);
                    client_workers_.push_back(ClientWorker{
                        std::thread(
                            [this,
                             connection_id,
                             done,
                             connected_pipe = std::move(pipe)]() mutable {
                            run_client(
                                std::move(connected_pipe),
                                connection_id);
                            done->store(
                                true,
                                std::memory_order_release);
                        }),
                        std::move(done),
                    });
                } else {
                    log_message(
                        LogLevel::warning,
                        L"pipe_server",
                        L"connection_failed");
                }
            }
        } catch (...) {
            log_message(
                LogLevel::error,
                L"pipe_server",
                L"worker_exception");
        }
        running_.store(false, std::memory_order_release);
        log_message(
            LogLevel::info,
            L"pipe_server",
            L"stopped");
    }

    std::wstring pipe_name_;
    Handler handler_;
    DisconnectHandler disconnect_handler_;
    std::atomic_bool running_ = false;
    std::atomic<std::uint64_t> next_connection_id_ = 1U;
    std::atomic<std::size_t> active_clients_ = 0U;
    UniqueHandle stop_event_;
    UniqueHandle ready_event_;
    std::thread worker_;
    mutable std::mutex client_mutex_;
    std::vector<ClientWorker> client_workers_;
    mutable std::mutex start_error_mutex_;
    std::string start_error_;
};

PipeServer::PipeServer(
    std::wstring pipe_name,
    Handler handler,
    DisconnectHandler disconnect_handler)
    : impl_(std::make_unique<Impl>(
          std::move(pipe_name),
          std::move(handler),
          std::move(disconnect_handler))) {}

PipeServer::PipeServer(
    std::wstring pipe_name,
    LegacyHandler handler)
    : PipeServer(
          std::move(pipe_name),
          [legacy = std::move(handler)](
              const ConnectionId,
              const std::string_view payload) {
              return legacy(payload);
          }) {}

PipeServer::~PipeServer() = default;

bool PipeServer::start(std::string& error_message) {
    return impl_->start(error_message);
}

void PipeServer::stop() noexcept {
    impl_->stop();
}

bool PipeServer::running() const noexcept {
    return impl_->running();
}

}  // namespace aviutl2::live
