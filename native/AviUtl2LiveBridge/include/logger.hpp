#pragma once

#include <string_view>

struct LOG_HANDLE;

namespace aviutl2::live {

enum class LogLevel {
    info,
    warning,
    error,
    verbose,
};

void set_log_handle(LOG_HANDLE* handle) noexcept;
void log_message(
    LogLevel level,
    std::wstring_view component,
    std::wstring_view event,
    std::wstring_view detail = {}) noexcept;

}  // namespace aviutl2::live
