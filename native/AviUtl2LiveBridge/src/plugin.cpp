#include "bridge_constants.hpp"
#include "bridge_state.hpp"
#include "api_lock.hpp"
#include "logger.hpp"

#include <windows.h>

#include "logger2.h"
#include "plugin2.h"

#include <atomic>
#include <exception>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

namespace {

COMMON_PLUGIN_TABLE g_plugin_table{
    L"AviUtl2 Live Bridge",
    L"AviUtl2 Live Bridge version 0.9.6",
};

// Project-load registration may synchronously invoke its initial callback
// while RegisterPlugin is still publishing g_state.
std::recursive_mutex g_state_mutex;
std::unique_ptr<aviutl2::live::BridgeState> g_state;
std::atomic<HWND> g_start_window = nullptr;
std::atomic_bool g_external_api_allowed = false;
std::atomic_bool g_bridge_active = false;
std::atomic_bool g_transitioning = false;
std::atomic_bool g_shutting_down = false;
std::thread g_transition_thread;
HWND g_status_window = nullptr;
HWND g_permission_checkbox = nullptr;
HWND g_status_text = nullptr;
ATOM g_start_window_class = 0;
ATOM g_status_window_class = 0;

constexpr wchar_t kStartWindowClass[] =
    L"AviUtl2LiveBridge.DeferredStartWindow";
constexpr wchar_t kStatusWindowClass[] =
    L"AviUtl2LiveBridge.StatusWindow";
constexpr UINT kStartBridgeMessage = WM_APP + 0x421U;
constexpr UINT kTransitionCompleteMessage = WM_APP + 0x422U;
constexpr int kPermissionCheckboxId = 1001;
char kObjectUpdatedEvent[] = "object_updated";
char kEditFrameChangedEvent[] = "edit_frame_changed";
char kEditSceneChangedEvent[] = "edit_scene_changed";
char kFocusObjectChangedEvent[] = "focus_object_changed";

void record_project_lifecycle(
    PROJECT_FILE* project,
    const std::string_view event_type) noexcept {
    try {
        std::wstring_view project_file_path;
        // PROJECT_FILE is callback-scoped. Copy its path now, but do not
        // enter an edit/read section or perform any mutation from callbacks.
        if (project != nullptr &&
            project->get_project_file_path != nullptr) {
            const LPCWSTR path = project->get_project_file_path();
            if (path != nullptr) {
                project_file_path = path;
            }
        }
        std::scoped_lock lock(g_state_mutex);
        if (g_state != nullptr &&
            !g_shutting_down.load(std::memory_order_acquire)) {
            g_state->record_project_event(event_type, project_file_path);
        }
    } catch (...) {
        // Host lifecycle callbacks must never observe plugin exceptions.
    }
}

void project_loaded_callback(PROJECT_FILE* project) noexcept {
    record_project_lifecycle(project, "project_loaded");
}

void project_saving_callback(PROJECT_FILE* project) noexcept {
    record_project_lifecycle(project, "project_saving");
}

void bridge_event_callback(void* param) noexcept {
    if (param == nullptr ||
        g_shutting_down.load(std::memory_order_acquire)) {
        return;
    }
    try {
        std::scoped_lock lock(g_state_mutex);
        if (g_state != nullptr &&
            !g_shutting_down.load(std::memory_order_acquire)) {
            g_state->record_event(
                static_cast<const char*>(param));
        }
    } catch (...) {
        // AviUtl2 invokes this on its event thread. Never propagate an
        // exception or make an SDK call from the callback.
    }
}

[[nodiscard]] std::wstring process_label() {
    return L"PID " + std::to_wstring(GetCurrentProcessId());
}

[[nodiscard]] std::wstring utf8_to_wide(const std::string& input);

[[nodiscard]] std::wstring normalize_timeline_label(
    std::wstring label) {
    for (wchar_t& character : label) {
        if (character == L'\r' || character == L'\n' ||
            character == L'\t') {
            character = L' ';
        }
    }
    constexpr std::size_t kMaxTimelineLabelCharacters = 256U;
    if (label.size() > kMaxTimelineLabelCharacters) {
        label.resize(kMaxTimelineLabelCharacters - 1U);
        label.push_back(L'\u2026');
    }
    return label;
}

[[nodiscard]] std::wstring default_object_label(
    EDIT_SECTION* edit,
    const OBJECT_HANDLE object) {
    if (edit->get_object_item_value != nullptr) {
        const LPCSTR text = edit->get_object_item_value(
            object,
            L"テキスト",
            L"テキスト");
        if (text != nullptr && text[0] != '\0') {
            return normalize_timeline_label(
                utf8_to_wide(std::string(text)));
        }
    }
    if (edit->get_object_alias != nullptr) {
        const LPCSTR sdk_alias = edit->get_object_alias(object);
        if (sdk_alias != nullptr) {
            const std::string_view alias(sdk_alias);
            constexpr std::string_view kEffectName = "effect.name=";
            const std::size_t position = alias.find(kEffectName);
            if (position != std::string_view::npos) {
                const std::size_t value_start =
                    position + kEffectName.size();
                const std::size_t line_end =
                    alias.find_first_of("\r\n", value_start);
                const std::string value(
                    alias.substr(
                        value_start,
                        line_end == std::string_view::npos
                            ? std::string_view::npos
                            : line_end - value_start));
                if (!value.empty()) {
                    return normalize_timeline_label(
                        utf8_to_wide(value));
                }
            }
        }
    }
    return {};
}

[[nodiscard]] std::vector<OBJECT_HANDLE> selected_objects(
    EDIT_SECTION* edit) {
    std::vector<OBJECT_HANDLE> objects;
    if (edit == nullptr) {
        return objects;
    }
    if (edit->get_selected_object_num != nullptr &&
        edit->get_selected_object != nullptr) {
        const int count = edit->get_selected_object_num();
        if (count > 0) {
            objects.reserve(static_cast<std::size_t>(count));
            for (int index = 0; index < count; ++index) {
                const OBJECT_HANDLE object =
                    edit->get_selected_object(index);
                if (object != nullptr) {
                    objects.push_back(object);
                }
            }
        }
    }
    if (objects.empty() && edit->get_focus_object != nullptr) {
        const OBJECT_HANDLE object = edit->get_focus_object();
        if (object != nullptr) {
            objects.push_back(object);
        }
    }
    return objects;
}

void set_selected_api_lock(
    EDIT_SECTION* edit,
    const bool locked) noexcept {
    try {
        if (edit == nullptr || edit->get_object_name == nullptr ||
            edit->set_object_name == nullptr) {
            return;
        }
        const std::vector<OBJECT_HANDLE> objects =
            selected_objects(edit);
        std::vector<std::wstring> updated_names;
        updated_names.reserve(objects.size());
        for (const OBJECT_HANDLE object : objects) {
            const LPCWSTR sdk_name = edit->get_object_name(object);
            const std::wstring current =
                sdk_name != nullptr ? std::wstring(sdk_name) : std::wstring();
            const bool currently_locked =
                aviutl2::live::is_api_locked_name(current);
            if (locked && !currently_locked) {
                if (current.empty()) {
                    const std::wstring label =
                        default_object_label(edit, object);
                    updated_names.push_back(
                        label.empty()
                            ? std::wstring(
                                  aviutl2::live::kApiLockMarker)
                            : std::wstring(
                                  aviutl2::live::
                                      kApiLockDerivedPrefix) +
                                  label);
                } else {
                    updated_names.push_back(
                        std::wstring(
                            aviutl2::live::kApiLockCustomPrefix) +
                        current);
                }
                edit->set_object_name(
                    object,
                    updated_names.back().c_str());
            } else if (!locked && currently_locked) {
                if (aviutl2::live::is_derived_api_lock_name(
                        current)) {
                    edit->set_object_name(object, nullptr);
                } else {
                    updated_names.push_back(
                        current.substr(
                            aviutl2::live::
                                kApiLockCustomPrefix.size()));
                    edit->set_object_name(
                        object,
                        updated_names.back().empty()
                            ? nullptr
                            : updated_names.back().c_str());
                }
            }
        }
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::info,
            L"plugin",
            locked ? L"api_lock_set" : L"api_lock_cleared");
    } catch (...) {
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"api_lock_failed");
    }
}

void lock_selected_objects(EDIT_SECTION* edit) noexcept {
    set_selected_api_lock(edit, true);
}

void unlock_selected_objects(EDIT_SECTION* edit) noexcept {
    set_selected_api_lock(edit, false);
}

[[nodiscard]] std::wstring utf8_to_wide(const std::string& input) {
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
        return L"UTF-8 conversion failed";
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
        return L"UTF-8 conversion failed";
    }
    return output;
}

void start_bridge_after_registration() noexcept {
    if (!g_external_api_allowed.load(std::memory_order_acquire) ||
        g_shutting_down.load(std::memory_order_acquire)) {
        return;
    }
    std::scoped_lock lock(g_state_mutex);
    if (g_state == nullptr) {
        return;
    }
    try {
        std::string error_message;
        if (!g_state->start(error_message)) {
            g_bridge_active.store(false, std::memory_order_release);
            aviutl2::live::log_message(
                aviutl2::live::LogLevel::error,
                L"plugin",
                L"start_failed",
                utf8_to_wide(error_message));
        } else {
            g_bridge_active.store(true, std::memory_order_release);
        }
    } catch (const std::exception& error) {
        g_bridge_active.store(false, std::memory_order_release);
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"deferred_start_exception",
            utf8_to_wide(error.what()));
    } catch (...) {
        g_bridge_active.store(false, std::memory_order_release);
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"deferred_start_exception");
    }
    g_transitioning.store(false, std::memory_order_release);
    const HWND status_window = g_status_window;
    if (status_window != nullptr) {
        PostMessageW(
            status_window,
            kTransitionCompleteMessage,
            g_bridge_active.load(std::memory_order_acquire) ? 1U : 0U,
            0);
    }
}

void update_status_window() noexcept {
    if (g_permission_checkbox != nullptr) {
        SendMessageW(
            g_permission_checkbox,
            BM_SETCHECK,
            g_external_api_allowed.load(std::memory_order_acquire)
                ? BST_CHECKED
                : BST_UNCHECKED,
            0);
        EnableWindow(
            g_permission_checkbox,
            !g_transitioning.load(std::memory_order_acquire));
    }
    if (g_status_text == nullptr) {
        return;
    }
    if (g_transitioning.load(std::memory_order_acquire)) {
        const std::wstring text =
            L"外部API連携: 切り替え中...（" + process_label() + L"）";
        SetWindowTextW(g_status_text, text.c_str());
    } else if (!g_external_api_allowed.load(std::memory_order_acquire)) {
        const std::wstring text =
            L"外部API連携: Disabled（接続拒否 / " +
            process_label() + L"）";
        SetWindowTextW(g_status_text, text.c_str());
    } else if (g_bridge_active.load(std::memory_order_acquire)) {
        const std::wstring text =
            L"外部API連携: Enabled（接続受付中 / " +
            process_label() + L"）";
        SetWindowTextW(g_status_text, text.c_str());
    } else {
        const std::wstring text =
            L"外部API連携: Enabled（起動失敗 / " +
            process_label() + L"）";
        SetWindowTextW(g_status_text, text.c_str());
    }
}

void begin_permission_transition(const bool enabled) noexcept {
    if (g_shutting_down.load(std::memory_order_acquire) ||
        g_transitioning.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    if (g_transition_thread.joinable()) {
        g_transition_thread.join();
    }

    g_external_api_allowed.store(enabled, std::memory_order_release);
    update_status_window();

    try {
        g_transition_thread = std::thread([enabled] {
            bool active = false;
            {
                std::scoped_lock lock(g_state_mutex);
                if (g_state != nullptr &&
                    !g_shutting_down.load(std::memory_order_acquire)) {
                    if (enabled) {
                        if (g_state->running()) {
                            active = true;
                        } else {
                            std::string error_message;
                            active = g_state->start(error_message);
                            if (!active) {
                                aviutl2::live::log_message(
                                    aviutl2::live::LogLevel::error,
                                    L"plugin",
                                    L"enable_failed",
                                    utf8_to_wide(error_message));
                            }
                        }
                    } else {
                        g_state->stop();
                    }
                }
            }
            g_bridge_active.store(active, std::memory_order_release);
            g_transitioning.store(false, std::memory_order_release);
            const HWND window = g_status_window;
            if (window != nullptr) {
                PostMessageW(
                    window,
                    kTransitionCompleteMessage,
                    active ? 1U : 0U,
                    0);
            }
        });
    } catch (...) {
        g_bridge_active.store(false, std::memory_order_release);
        g_transitioning.store(false, std::memory_order_release);
        update_status_window();
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"transition_thread_failed");
    }
}

LRESULT CALLBACK status_window_proc(
    const HWND window,
    const UINT message,
    const WPARAM wparam,
    const LPARAM lparam) {
    switch (message) {
        case WM_CREATE: {
            const HINSTANCE instance = GetModuleHandleW(nullptr);
            g_permission_checkbox = CreateWindowExW(
                0,
                L"BUTTON",
                L"このウィンドウの外部API連携を許可",
                WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                10,
                8,
                220,
                24,
                window,
                reinterpret_cast<HMENU>(
                    static_cast<INT_PTR>(kPermissionCheckboxId)),
                instance,
                nullptr);
            g_status_text = CreateWindowExW(
                0,
                L"STATIC",
                L"",
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                10,
                38,
                420,
                24,
                window,
                nullptr,
                instance,
                nullptr);
            const HFONT font = static_cast<HFONT>(
                GetStockObject(DEFAULT_GUI_FONT));
            if (g_permission_checkbox != nullptr) {
                SendMessageW(
                    g_permission_checkbox,
                    WM_SETFONT,
                    reinterpret_cast<WPARAM>(font),
                    TRUE);
            }
            if (g_status_text != nullptr) {
                SendMessageW(
                    g_status_text,
                    WM_SETFONT,
                    reinterpret_cast<WPARAM>(font),
                    TRUE);
            }
            update_status_window();
            return 0;
        }
        case WM_SIZE: {
            const int width = LOWORD(lparam);
            if (g_permission_checkbox != nullptr) {
                MoveWindow(
                    g_permission_checkbox,
                    10,
                    8,
                    (width > 20) ? width - 20 : 0,
                    24,
                    TRUE);
            }
            if (g_status_text != nullptr) {
                MoveWindow(
                    g_status_text,
                    10,
                    38,
                    (width > 20) ? width - 20 : 0,
                    24,
                    TRUE);
            }
            return 0;
        }
        case WM_COMMAND:
            if (LOWORD(wparam) == kPermissionCheckboxId &&
                HIWORD(wparam) == BN_CLICKED &&
                g_permission_checkbox != nullptr) {
                begin_permission_transition(
                    SendMessageW(
                        g_permission_checkbox,
                        BM_GETCHECK,
                        0,
                        0) == BST_CHECKED);
                return 0;
            }
            break;
        case kTransitionCompleteMessage:
            update_status_window();
            return 0;
        case WM_CLOSE:
            ShowWindow(window, SW_HIDE);
            return 0;
        case WM_NCDESTROY:
            g_permission_checkbox = nullptr;
            g_status_text = nullptr;
            g_status_window = nullptr;
            break;
        default:
            break;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

[[nodiscard]] bool create_status_window(
    HOST_APP_TABLE* host) noexcept {
    if (host == nullptr || host->register_config_menu == nullptr) {
        return false;
    }
    const HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.hInstance = instance;
    window_class.lpfnWndProc = status_window_proc;
    window_class.lpszClassName = kStatusWindowClass;
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(
        static_cast<INT_PTR>(COLOR_WINDOW + 1));
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    g_status_window_class = RegisterClassExW(&window_class);
    if (g_status_window_class == 0 &&
        GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return false;
    }

    const std::wstring window_title =
        L"AviUtl2 Live Bridge (" + process_label() + L")";
    g_status_window = CreateWindowExW(
        WS_EX_TOOLWINDOW,
        kStatusWindowClass,
        window_title.c_str(),
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        480,
        120,
        nullptr,
        nullptr,
        instance,
        nullptr);
    if (g_status_window == nullptr) {
        return false;
    }
    return true;
}

void show_config_menu(
    const HWND owner,
    HINSTANCE /*dll_instance*/) {
    if (g_status_window == nullptr ||
        !IsWindow(g_status_window)) {
        MessageBoxW(
            owner,
            L"外部API連携設定ウィンドウを開けませんでした。",
            L"AviUtl2 Live Bridge",
            MB_OK | MB_ICONERROR);
        return;
    }
    update_status_window();
    if (owner != nullptr && IsWindow(owner)) {
        RECT owner_rect{};
        RECT settings_rect{};
        if (GetWindowRect(owner, &owner_rect) &&
            GetWindowRect(g_status_window, &settings_rect)) {
            const int width =
                settings_rect.right - settings_rect.left;
            const int height =
                settings_rect.bottom - settings_rect.top;
            const int x =
                owner_rect.left +
                ((owner_rect.right - owner_rect.left) - width) / 2;
            const int y =
                owner_rect.top +
                ((owner_rect.bottom - owner_rect.top) - height) / 2;
            SetWindowPos(
                g_status_window,
                HWND_TOP,
                x,
                y,
                0,
                0,
                SWP_NOSIZE | SWP_SHOWWINDOW);
        } else {
            ShowWindow(g_status_window, SW_SHOWNORMAL);
        }
    } else {
        ShowWindow(g_status_window, SW_SHOWNORMAL);
    }
    SetForegroundWindow(g_status_window);
}

LRESULT CALLBACK start_window_proc(
    const HWND window,
    const UINT message,
    const WPARAM wparam,
    const LPARAM lparam) {
    if (message == kStartBridgeMessage) {
        start_bridge_after_registration();
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_NCDESTROY) {
        HWND expected = window;
        g_start_window.compare_exchange_strong(expected, nullptr);
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

[[nodiscard]] bool schedule_deferred_start() noexcept {
    try {
        const HINSTANCE instance = GetModuleHandleW(nullptr);
        if (g_start_window_class == 0) {
            WNDCLASSEXW window_class{};
            window_class.cbSize = sizeof(window_class);
            window_class.hInstance = instance;
            window_class.lpfnWndProc = start_window_proc;
            window_class.lpszClassName = kStartWindowClass;
            g_start_window_class = RegisterClassExW(&window_class);
            if (g_start_window_class == 0 &&
                GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
                return false;
            }
        }

        const HWND window = CreateWindowExW(
            0,
            kStartWindowClass,
            L"",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            nullptr,
            instance,
            nullptr);
        if (window == nullptr) {
            return false;
        }
        g_start_window.store(window, std::memory_order_release);
        if (!PostMessageW(window, kStartBridgeMessage, 0, 0)) {
            g_start_window.store(nullptr, std::memory_order_release);
            DestroyWindow(window);
            return false;
        }
        return true;
    } catch (...) {
        return false;
    }
}

void destroy_deferred_start_window() noexcept {
    const HWND window =
        g_start_window.exchange(nullptr, std::memory_order_acq_rel);
    if (window != nullptr && IsWindow(window)) {
        DestroyWindow(window);
    }
    if (g_start_window_class != 0) {
        UnregisterClassW(kStartWindowClass, GetModuleHandleW(nullptr));
        g_start_window_class = 0;
    }
}

void destroy_status_window() noexcept {
    if (g_status_window != nullptr && IsWindow(g_status_window)) {
        DestroyWindow(g_status_window);
    }
    g_status_window = nullptr;
    if (g_status_window_class != 0) {
        UnregisterClassW(kStatusWindowClass, GetModuleHandleW(nullptr));
        g_status_window_class = 0;
    }
}

}  // namespace

EXTERN_C __declspec(dllexport) DWORD RequiredVersion() {
    return static_cast<DWORD>(aviutl2::live::kRequiredHostVersion);
}

EXTERN_C __declspec(dllexport) void InitializeLogger(LOG_HANDLE* handle) {
    aviutl2::live::set_log_handle(handle);
}

EXTERN_C __declspec(dllexport) bool InitializePlugin(DWORD version) {
    return version >= RequiredVersion();
}

EXTERN_C __declspec(dllexport) COMMON_PLUGIN_TABLE* GetCommonPluginTable() {
    return &g_plugin_table;
}

EXTERN_C __declspec(dllexport) void RegisterPlugin(HOST_APP_TABLE* host) {
    std::scoped_lock lock(g_state_mutex);
    g_shutting_down.store(false, std::memory_order_release);
    if (g_state != nullptr) {
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::warning,
            L"plugin",
            L"register_ignored");
        return;
    }
    if (host == nullptr || host->create_edit_handle == nullptr) {
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"host_api_unavailable");
        return;
    }

    try {
        EDIT_HANDLE* const edit_handle = host->create_edit_handle();
        if (edit_handle == nullptr) {
            aviutl2::live::log_message(
                aviutl2::live::LogLevel::error,
                L"plugin",
                L"edit_handle_unavailable");
            return;
        }
        auto state =
            std::make_unique<aviutl2::live::BridgeState>(edit_handle);
        g_state = std::move(state);
        if (host->register_project_load_handler != nullptr) {
            host->register_project_load_handler(project_loaded_callback);
        }
        if (host->register_project_save_handler != nullptr) {
            host->register_project_save_handler(project_saving_callback);
        }
        if (host->register_event_listener != nullptr) {
            host->register_event_listener(
                EVENT_TYPE::UPDATE_OBJECT,
                kObjectUpdatedEvent,
                bridge_event_callback);
            host->register_event_listener(
                EVENT_TYPE::CHANGE_EDIT_FRAME,
                kEditFrameChangedEvent,
                bridge_event_callback);
            host->register_event_listener(
                EVENT_TYPE::CHANGE_EDIT_SCENE,
                kEditSceneChangedEvent,
                bridge_event_callback);
            host->register_event_listener(
                EVENT_TYPE::CHANGE_FOCUS_OBJECT,
                kFocusObjectChangedEvent,
                bridge_event_callback);
        }
        g_external_api_allowed.store(false, std::memory_order_release);
        g_bridge_active.store(false, std::memory_order_release);
        g_transitioning.store(false, std::memory_order_release);
        if (create_status_window(host)) {
            host->register_config_menu(
                L"外部API連携設定...",
                show_config_menu);
            update_status_window();
        } else {
            aviutl2::live::log_message(
                aviutl2::live::LogLevel::warning,
                L"plugin",
                L"status_window_unavailable");
        }
        if (host->register_object_menu != nullptr) {
            host->register_object_menu(
                L"外部API編集ロック\\ロック",
                lock_selected_objects);
            host->register_object_menu(
                L"外部API編集ロック\\解除",
                unlock_selected_objects);
        }
        if (g_external_api_allowed.load(std::memory_order_acquire)) {
            g_transitioning.store(true, std::memory_order_release);
            update_status_window();
            if (!schedule_deferred_start()) {
                g_transitioning.store(false, std::memory_order_release);
                update_status_window();
                aviutl2::live::log_message(
                    aviutl2::live::LogLevel::error,
                    L"plugin",
                    L"deferred_start_schedule_failed");
            }
        }
    } catch (const std::exception& error) {
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"register_exception",
            utf8_to_wide(error.what()));
    } catch (...) {
        aviutl2::live::log_message(
            aviutl2::live::LogLevel::error,
            L"plugin",
            L"register_exception");
    }
}

EXTERN_C __declspec(dllexport) void UninitializePlugin() {
    g_shutting_down.store(true, std::memory_order_release);
    destroy_deferred_start_window();
    if (g_transition_thread.joinable()) {
        g_transition_thread.join();
    }
    std::unique_ptr<aviutl2::live::BridgeState> state;
    {
        std::scoped_lock lock(g_state_mutex);
        state = std::move(g_state);
    }
    if (state != nullptr) {
        state->stop();
    }
    g_bridge_active.store(false, std::memory_order_release);
    destroy_status_window();
    aviutl2::live::set_log_handle(nullptr);
}
