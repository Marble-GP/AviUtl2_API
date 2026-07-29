#include "logger.hpp"

#include <windows.h>

#include "logger2.h"

#include <atomic>
#include <string>

namespace aviutl2::live {
namespace {

std::atomic<LOG_HANDLE*> g_log_handle = nullptr;

[[nodiscard]] std::wstring escape_json_string(const std::wstring_view input) {
    std::wstring output;
    output.reserve(input.size() + 8U);
    for (const wchar_t character : input) {
        switch (character) {
            case L'\\':
                output.append(L"\\\\");
                break;
            case L'"':
                output.append(L"\\\"");
                break;
            case L'\r':
                output.append(L"\\r");
                break;
            case L'\n':
                output.append(L"\\n");
                break;
            case L'\t':
                output.append(L"\\t");
                break;
            default:
                if (character >= 0x20) {
                    output.push_back(character);
                }
                break;
        }
    }
    return output;
}

[[nodiscard]] const wchar_t* level_name(const LogLevel level) noexcept {
    switch (level) {
        case LogLevel::warning:
            return L"warning";
        case LogLevel::error:
            return L"error";
        case LogLevel::verbose:
            return L"verbose";
        case LogLevel::info:
        default:
            return L"info";
    }
}

}  // namespace

void set_log_handle(LOG_HANDLE* handle) noexcept {
    g_log_handle.store(handle, std::memory_order_release);
}

void log_message(
    const LogLevel level,
    const std::wstring_view component,
    const std::wstring_view event,
    const std::wstring_view detail) noexcept {
    try {
        std::wstring message =
            L"{\"component\":\"" + escape_json_string(component) +
            L"\",\"event\":\"" + escape_json_string(event) +
            L"\",\"level\":\"" + level_name(level) + L"\"";
        if (!detail.empty()) {
            message.append(L",\"detail\":\"");
            message.append(escape_json_string(detail));
            message.push_back(L'"');
        }
        message.push_back(L'}');
        if (message.size() > 1000U) {
            message.resize(997U);
            message.append(L"...");
        }

        LOG_HANDLE* const handle =
            g_log_handle.load(std::memory_order_acquire);
        if (handle != nullptr) {
            switch (level) {
                case LogLevel::warning:
                    if (handle->warn != nullptr) {
                        handle->warn(handle, message.c_str());
                        return;
                    }
                    break;
                case LogLevel::error:
                    if (handle->error != nullptr) {
                        handle->error(handle, message.c_str());
                        return;
                    }
                    break;
                case LogLevel::verbose:
                    if (handle->verbose != nullptr) {
                        handle->verbose(handle, message.c_str());
                        return;
                    }
                    break;
                case LogLevel::info:
                default:
                    if (handle->info != nullptr) {
                        handle->info(handle, message.c_str());
                        return;
                    }
                    break;
            }
        }
        message.push_back(L'\n');
        OutputDebugStringW(message.c_str());
    } catch (...) {
        OutputDebugStringW(L"AviUtl2LiveBridge logging failure\n");
    }
}

}  // namespace aviutl2::live
