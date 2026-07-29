#include "instance_registry.hpp"

#include "bridge_constants.hpp"
#include "json.hpp"
#include "logger.hpp"

#include <windows.h>

#include <array>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <system_error>
#include <vector>

namespace aviutl2::live {
namespace {

[[nodiscard]] std::filesystem::path local_app_data_path() {
    const DWORD required =
        GetEnvironmentVariableW(L"LOCALAPPDATA", nullptr, 0U);
    if (required == 0U) {
        throw std::runtime_error("LOCALAPPDATA is not available");
    }
    std::vector<wchar_t> buffer(required);
    const DWORD written =
        GetEnvironmentVariableW(L"LOCALAPPDATA", buffer.data(), required);
    if (written == 0U || written >= required) {
        throw std::runtime_error("LOCALAPPDATA could not be read");
    }
    return std::filesystem::path(buffer.data());
}

[[nodiscard]] std::string wide_to_utf8(const std::wstring_view input) {
    if (input.empty()) {
        return {};
    }
    const int required = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        input.data(),
        static_cast<int>(input.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (required <= 0) {
        throw std::runtime_error("UTF-16 to UTF-8 conversion failed");
    }
    std::string output(static_cast<std::size_t>(required), '\0');
    const int written = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        input.data(),
        static_cast<int>(input.size()),
        output.data(),
        required,
        nullptr,
        nullptr);
    if (written != required) {
        throw std::runtime_error("UTF-16 to UTF-8 conversion failed");
    }
    return output;
}

[[nodiscard]] std::string utc_timestamp() {
    SYSTEMTIME time{};
    GetSystemTime(&time);
    std::array<char, 32> buffer{};
    const int written = std::snprintf(
        buffer.data(),
        buffer.size(),
        "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",
        static_cast<unsigned int>(time.wYear),
        static_cast<unsigned int>(time.wMonth),
        static_cast<unsigned int>(time.wDay),
        static_cast<unsigned int>(time.wHour),
        static_cast<unsigned int>(time.wMinute),
        static_cast<unsigned int>(time.wSecond),
        static_cast<unsigned int>(time.wMilliseconds));
    if (written <= 0 || static_cast<std::size_t>(written) >= buffer.size()) {
        throw std::runtime_error("timestamp formatting failed");
    }
    return std::string(buffer.data(), static_cast<std::size_t>(written));
}

}  // namespace

InstanceRegistry::InstanceRegistry(
    const std::uint32_t pid,
    std::wstring pipe_name)
    : pid_(pid), pipe_name_(std::move(pipe_name)) {}

InstanceRegistry::~InstanceRegistry() {
    remove();
}

bool InstanceRegistry::publish(
    const int scene_id,
    std::string& error_message) {
    try {
        const std::filesystem::path directory =
            local_app_data_path() / L"AviUtl2LiveBridge" / L"instances";
        std::error_code error;
        std::filesystem::create_directories(directory, error);
        if (error) {
            error_message = "instance directory could not be created: " +
                            error.message();
            return false;
        }

        path_ = directory / (std::to_wstring(pid_) + L".json");
        const std::filesystem::path temporary =
            directory / (std::to_wstring(pid_) + L".json.tmp");
        Json::Object document{
            {"pid", Json(static_cast<std::int64_t>(pid_))},
            {"pipe", Json(wide_to_utf8(pipe_name_))},
            {"plugin_version", Json(std::string(kPluginVersion))},
            {"project_path", Json(nullptr)},
            {"protocol_version",
             Json(static_cast<std::int64_t>(kProtocolVersion))},
            {"scene_id", Json(scene_id)},
            {"sdk_baseline", Json(std::string(kSdkBaseline))},
            {"started_at", Json(utc_timestamp())},
        };
        const std::string contents = serialize_json(Json(std::move(document)));

        {
            std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
            if (!stream) {
                error_message = "temporary instance file could not be opened";
                return false;
            }
            stream.write(
                contents.data(),
                static_cast<std::streamsize>(contents.size()));
            stream.put('\n');
            stream.flush();
            if (!stream) {
                error_message = "temporary instance file could not be written";
                stream.close();
                std::filesystem::remove(temporary, error);
                return false;
            }
        }

        if (!MoveFileExW(
                temporary.c_str(),
                path_.c_str(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
            error_message =
                "instance file could not be published (Win32 error " +
                std::to_string(GetLastError()) + ")";
            std::filesystem::remove(temporary, error);
            return false;
        }
        published_ = true;
        return true;
    } catch (const std::exception& error) {
        error_message = error.what();
        return false;
    }
}

void InstanceRegistry::remove() noexcept {
    if (!published_ || path_.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::remove(path_, error);
    if (error) {
        log_message(
            LogLevel::warning,
            L"instance_registry",
            L"remove_failed");
    }
    published_ = false;
}

const std::filesystem::path& InstanceRegistry::path() const noexcept {
    return path_;
}

}  // namespace aviutl2::live
