#include "sdk_adapter.hpp"

#include "api_lock.hpp"
#include "alias_tools.hpp"
#include "bridge_constants.hpp"
#include "json.hpp"

#include <windows.h>

#include "plugin2.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iterator>
#include <mutex>
#include <new>
#include <set>
#include <stdexcept>
#include <unordered_map>
#include <utility>

namespace aviutl2::live {
namespace {

struct CapturedObject final {
    OBJECT_HANDLE handle = nullptr;
    int layer = 0;
    int frame_start = 0;
    int frame_end = 0;
    bool has_name = false;
    std::string name;
    std::string alias;
    bool api_locked = false;
};

struct CapturedLayer final {
    bool has_name = false;
    std::string name;
    bool enabled = true;
    bool locked = false;
};

struct CapturedTimeline final {
    std::int64_t revision = 0;
    int scene_id = 0;
    std::string scene_name;
    int width = 0;
    int height = 0;
    int rate = 0;
    int scale = 0;
    int sample_rate = 0;
    std::vector<CapturedLayer> layers;
    std::vector<CapturedObject> objects;
};

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
        throw std::runtime_error("UTF-16 object name is invalid");
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
        throw std::runtime_error("UTF-16 object name conversion failed");
    }
    return output;
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
    if (MultiByteToWideChar(
            CP_UTF8,
            MB_ERR_INVALID_CHARS,
            input.data(),
            static_cast<int>(input.size()),
            output.data(),
            required) != required) {
        throw std::runtime_error("UTF-8 conversion failed");
    }
    return output;
}

void hash_bytes(
    std::uint64_t& hash,
    const std::string_view value) noexcept {
    constexpr std::uint64_t prime = 1099511628211ULL;
    for (const unsigned char byte : value) {
        hash ^= static_cast<std::uint64_t>(byte);
        hash *= prime;
    }
    hash ^= 0xFFU;
    hash *= prime;
}

void hash_integer(
    std::uint64_t& hash,
    const std::int64_t value) {
    hash_bytes(hash, std::to_string(value));
}

[[nodiscard]] std::int64_t timeline_revision(
    const int scene_id,
    const std::string_view scene_name,
    const int width,
    const int height,
    const int rate,
    const int scale,
    const int sample_rate,
    const std::vector<CapturedLayer>& layers,
    const std::vector<CapturedObject>& objects) {
    std::uint64_t hash = 1469598103934665603ULL;
    hash_integer(hash, scene_id);
    hash_bytes(hash, scene_name);
    hash_integer(hash, width);
    hash_integer(hash, height);
    hash_integer(hash, rate);
    hash_integer(hash, scale);
    hash_integer(hash, sample_rate);
    hash_integer(hash, static_cast<std::int64_t>(layers.size()));
    for (const CapturedLayer& layer : layers) {
        hash_integer(hash, layer.has_name ? 1 : 0);
        hash_bytes(hash, layer.name);
        hash_integer(hash, layer.enabled ? 1 : 0);
        hash_integer(hash, layer.locked ? 1 : 0);
    }
    hash_integer(hash, static_cast<std::int64_t>(objects.size()));
    for (const CapturedObject& object : objects) {
        hash_integer(hash, object.layer);
        hash_integer(hash, object.frame_start);
        hash_integer(hash, object.frame_end);
        hash_integer(hash, object.has_name ? 1 : 0);
        hash_bytes(hash, object.name);
        hash_bytes(hash, object.alias);
    }
    constexpr std::uint64_t max_exact_json_integer =
        (1ULL << 53U) - 1ULL;
    std::int64_t revision = static_cast<std::int64_t>(
        hash & max_exact_json_integer);
    if (revision == 0) {
        revision = 1;
    }
    return revision;
}

[[nodiscard]] bool capture_timeline(
    EDIT_HANDLE* edit_handle,
    EDIT_SECTION* edit,
    CapturedTimeline& timeline,
    std::string& error_code,
    std::string& error_message) {
    if (edit_handle == nullptr || edit_handle->get_edit_info == nullptr ||
        edit == nullptr || edit->find_object == nullptr ||
        edit->get_object_layer_frame == nullptr ||
        edit->get_object_alias == nullptr) {
        error_code = "READ_SECTION_UNAVAILABLE";
        error_message = "The AviUtl2 timeline inspection API is unavailable.";
        return false;
    }

    EDIT_INFO info{};
    edit_handle->get_edit_info(
        &info,
        static_cast<int>(sizeof(info)));
    if (info.layer_max < 0 || info.layer_max > 100000) {
        error_code = "SNAPSHOT_TOO_LARGE";
        error_message = "The AviUtl2 layer range exceeds the snapshot limit.";
        return false;
    }

    timeline.scene_id = info.scene_id;
    timeline.width = info.width;
    timeline.height = info.height;
    timeline.rate = info.rate;
    timeline.scale = info.scale;
    timeline.sample_rate = info.sample_rate;
    if (edit->get_scene_name != nullptr) {
        const LPCWSTR scene_name = edit->get_scene_name();
        if (scene_name != nullptr) {
            constexpr std::size_t max_scene_name_characters =
                4096U;
            const std::size_t length = wcsnlen_s(
                scene_name,
                max_scene_name_characters + 1U);
            if (length > max_scene_name_characters) {
                error_code = "SNAPSHOT_TOO_LARGE";
                error_message =
                    "The current scene name exceeds the snapshot limit.";
                return false;
            }
            timeline.scene_name = wide_to_utf8(
                std::wstring_view(scene_name, length));
        }
    }
    timeline.layers.clear();
    timeline.objects.clear();
    timeline.layers.reserve(
        static_cast<std::size_t>(info.layer_max) + 1U);
    for (int layer = 0; layer <= info.layer_max; ++layer) {
        CapturedLayer captured;
        if (edit->get_layer_name != nullptr) {
            const LPCWSTR sdk_name = edit->get_layer_name(layer);
            if (sdk_name != nullptr) {
                constexpr std::size_t max_name_characters = 4096U;
                const std::size_t name_length =
                    wcsnlen_s(sdk_name, max_name_characters + 1U);
                if (name_length > max_name_characters) {
                    error_code = "SNAPSHOT_TOO_LARGE";
                    error_message =
                        "A layer name exceeds the snapshot limit.";
                    return false;
                }
                captured.has_name = true;
                captured.name = wide_to_utf8(
                    std::wstring_view(sdk_name, name_length));
            }
        }
        if (edit->get_layer_enable != nullptr) {
            captured.enabled = edit->get_layer_enable(layer);
        }
        if (edit->get_layer_lock != nullptr) {
            captured.locked = edit->get_layer_lock(layer);
        }
        timeline.layers.push_back(std::move(captured));
    }
    std::size_t total_alias_bytes = 0U;
    for (int layer = 0; layer <= info.layer_max; ++layer) {
        int search_frame = 0;
        while (true) {
            const OBJECT_HANDLE handle =
                edit->find_object(layer, search_frame);
            if (handle == nullptr) {
                break;
            }
            const OBJECT_LAYER_FRAME range =
                edit->get_object_layer_frame(handle);
            if (range.layer < 0 || range.layer > info.layer_max ||
                range.start < 0 || range.end < range.start ||
                range.end < search_frame) {
                error_code = "INVALID_HOST_OBJECT_RANGE";
                error_message =
                    "AviUtl2 returned an invalid timeline object range "
                    "while searching layer " + std::to_string(layer) +
                    " from frame " + std::to_string(search_frame) +
                    " (base layer " + std::to_string(range.layer) +
                    ", frames " + std::to_string(range.start) + "-" +
                    std::to_string(range.end) + ").";
                return false;
            }

            // find_object() also reports an object from each secondary layer
            // occupied by multi-layer media.  get_object_layer_frame() returns
            // that object's base layer, so capture it only while enumerating
            // the base layer.  The object is still used to advance the search
            // cursor on the secondary layer to guarantee forward progress.
            if (range.layer != layer) {
                if (range.end == std::numeric_limits<int>::max()) {
                    break;
                }
                search_frame = range.end + 1;
                continue;
            }

            if (timeline.objects.size() >= kMaxSnapshotObjects) {
                error_code = "SNAPSHOT_TOO_LARGE";
                error_message =
                    "The object count exceeds the snapshot limit.";
                return false;
            }

            const LPCSTR sdk_alias = edit->get_object_alias(handle);
            if (sdk_alias == nullptr) {
                error_code = "ALIAS_UNAVAILABLE";
                error_message =
                    "AviUtl2 could not provide an object Alias.";
                return false;
            }
            const std::size_t remaining =
                kMaxSnapshotAliasBytes - total_alias_bytes;
            const std::size_t alias_length =
                strnlen_s(sdk_alias, remaining + 1U);
            if (alias_length > remaining) {
                error_code = "SNAPSHOT_TOO_LARGE";
                error_message =
                    "The total object Alias data exceeds the snapshot limit.";
                return false;
            }

            CapturedObject object;
            object.handle = handle;
            object.layer = range.layer;
            object.frame_start = range.start;
            object.frame_end = range.end;
            object.alias.assign(sdk_alias, alias_length);
            if (!is_valid_utf8(object.alias)) {
                error_code = "ALIAS_INVALID_UTF8";
                error_message =
                    "AviUtl2 returned non-UTF-8 object Alias data.";
                return false;
            }
            total_alias_bytes += alias_length;
            if (edit->get_object_name != nullptr) {
                const LPCWSTR sdk_name = edit->get_object_name(handle);
                if (sdk_name != nullptr) {
                    constexpr std::size_t max_name_characters = 4096U;
                    const std::size_t name_length =
                        wcsnlen_s(sdk_name, max_name_characters + 1U);
                    if (name_length > max_name_characters) {
                        error_code = "SNAPSHOT_TOO_LARGE";
                        error_message =
                            "An object name exceeds the snapshot limit.";
                        return false;
                    }
                    object.has_name = true;
                    object.api_locked = is_api_locked_name(
                        std::wstring_view(sdk_name, name_length));
                    object.name = wide_to_utf8(
                        std::wstring_view(sdk_name, name_length));
                }
            }
            timeline.objects.push_back(std::move(object));

            if (range.end == std::numeric_limits<int>::max()) {
                break;
            }
            search_frame = range.end + 1;
        }
    }
    timeline.revision =
        timeline_revision(
            timeline.scene_id,
            timeline.scene_name,
            timeline.width,
            timeline.height,
            timeline.rate,
            timeline.scale,
            timeline.sample_rate,
            timeline.layers,
            timeline.objects);
    return true;
}

struct EffectNamesContext final {
    std::vector<CatalogEffect> effects;
    std::vector<std::wstring> wide_names;
    std::string error_code;
    std::string error_message;
};

void effect_name_callback(
    void* parameter,
    const LPCWSTR name,
    const int type,
    const int flags) noexcept {
    auto& context = *static_cast<EffectNamesContext*>(parameter);
    if (!context.error_code.empty()) {
        return;
    }
    try {
        if (name == nullptr) {
            context.error_code = "INVALID_EFFECT_CATALOG";
            context.error_message =
                "AviUtl2 returned an effect without a name.";
            return;
        }
        if (context.effects.size() >= kMaxCatalogEffects) {
            context.error_code = "EFFECT_CATALOG_TOO_LARGE";
            context.error_message =
                "The AviUtl2 effect catalog exceeds the limit.";
            return;
        }
        constexpr std::size_t max_name_characters = 4096U;
        const std::size_t length =
            wcsnlen_s(name, max_name_characters + 1U);
        if (length > max_name_characters) {
            context.error_code = "EFFECT_CATALOG_TOO_LARGE";
            context.error_message =
                "An effect name exceeds the catalog limit.";
            return;
        }
        context.wide_names.emplace_back(name, length);
        context.effects.push_back(CatalogEffect{
            wide_to_utf8(std::wstring_view(name, length)),
            type,
            flags,
            {},
        });
    } catch (const std::exception& error) {
        context.error_code = "EFFECT_CATALOG_FAILED";
        context.error_message = error.what();
    } catch (...) {
        context.error_code = "EFFECT_CATALOG_FAILED";
        context.error_message =
            "The effect catalog callback failed.";
    }
}

struct EffectItemsContext final {
    CatalogEffect* effect = nullptr;
    std::string error_code;
    std::string error_message;
};

void effect_item_callback(
    void* parameter,
    const LPCWSTR name,
    const int type) noexcept {
    auto& context = *static_cast<EffectItemsContext*>(parameter);
    if (!context.error_code.empty()) {
        return;
    }
    try {
        if (context.effect == nullptr || name == nullptr) {
            context.error_code = "INVALID_EFFECT_CATALOG";
            context.error_message =
                "AviUtl2 returned an invalid effect item.";
            return;
        }
        if (context.effect->items.size() >=
            kMaxCatalogItemsPerEffect) {
            context.error_code = "EFFECT_CATALOG_TOO_LARGE";
            context.error_message =
                "An effect contains too many catalog items.";
            return;
        }
        constexpr std::size_t max_name_characters = 4096U;
        const std::size_t length =
            wcsnlen_s(name, max_name_characters + 1U);
        if (length > max_name_characters) {
            context.error_code = "EFFECT_CATALOG_TOO_LARGE";
            context.error_message =
                "An effect item name exceeds the catalog limit.";
            return;
        }
        context.effect->items.push_back(CatalogItem{
            wide_to_utf8(std::wstring_view(name, length)),
            type,
        });
    } catch (const std::exception& error) {
        context.error_code = "EFFECT_CATALOG_FAILED";
        context.error_message = error.what();
    } catch (...) {
        context.error_code = "EFFECT_CATALOG_FAILED";
        context.error_message =
            "The effect item callback failed.";
    }
}

struct LayersContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    int start = 0;
    int count = 0;
    LayerSnapshotResult result;
};

void layers_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<LayersContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        if (context.edit_handle == nullptr ||
            context.edit_handle->get_edit_info == nullptr) {
            context.result.error_code = "READ_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 edit information API is unavailable.";
            return;
        }
        EDIT_INFO info{};
        context.edit_handle->get_edit_info(
            &info,
            static_cast<int>(sizeof(info)));
        context.result.revision = timeline.revision;
        context.result.scene_id = timeline.scene_id;
        context.result.layer_max = info.layer_max;
        context.result.display_layer_start =
            info.display_layer_start;
        context.result.display_layer_count =
            info.display_layer_num < 0 ? 0 : info.display_layer_num;
        context.result.start = context.start;

        const int visible_end =
            context.result.display_layer_count == 0
                ? info.layer_max
                : context.result.display_layer_start +
                    context.result.display_layer_count - 1;
        const int available_end =
            std::max(info.layer_max, visible_end);
        if (context.start > available_end) {
            context.result.ok = true;
            return;
        }
        const int requested_end =
            context.start + context.count - 1;
        const int end = std::min(available_end, requested_end);
        context.result.layers.reserve(
            static_cast<std::size_t>(end - context.start + 1));
        for (int layer = context.start; layer <= end; ++layer) {
            LayerInfo output;
            output.layer = layer;
            if (static_cast<std::size_t>(layer) <
                timeline.layers.size()) {
                const CapturedLayer& captured =
                    timeline.layers[static_cast<std::size_t>(layer)];
                output.has_name = captured.has_name;
                output.name = captured.name;
                output.enabled = captured.enabled;
                output.locked = captured.locked;
            } else {
                if (edit->get_layer_name != nullptr) {
                    const LPCWSTR sdk_name =
                        edit->get_layer_name(layer);
                    if (sdk_name != nullptr) {
                        constexpr std::size_t max_name_characters =
                            4096U;
                        const std::size_t length = wcsnlen_s(
                            sdk_name,
                            max_name_characters + 1U);
                        if (length > max_name_characters) {
                            context.result.error_code =
                                "LAYER_SNAPSHOT_TOO_LARGE";
                            context.result.error_message =
                                "A layer name exceeds the limit.";
                            return;
                        }
                        output.has_name = true;
                        output.name = wide_to_utf8(
                            std::wstring_view(sdk_name, length));
                    }
                }
                if (edit->get_layer_enable != nullptr) {
                    output.enabled =
                        edit->get_layer_enable(layer);
                }
                if (edit->get_layer_lock != nullptr) {
                    output.locked =
                        edit->get_layer_lock(layer);
                }
            }
            output.object_count = static_cast<std::size_t>(
                std::count_if(
                    timeline.objects.begin(),
                    timeline.objects.end(),
                    [layer](const CapturedObject& object) {
                        return object.layer == layer;
                    }));
            context.result.layers.push_back(std::move(output));
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "LAYER_SNAPSHOT_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "LAYER_SNAPSHOT_FAILED";
        context.result.error_message =
            "The layer snapshot failed inside the SDK callback.";
    }
}

struct LayerMutationContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    int layer = 0;
    const std::optional<std::wstring>* name = nullptr;
    const std::optional<bool>* enabled = nullptr;
    ObjectMutationResult result;
};

void layer_mutation_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<LayerMutationContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.layer < 0 ||
            static_cast<std::size_t>(context.layer) >=
                timeline.layers.size()) {
            context.result.error_code = "LAYER_NOT_FOUND";
            context.result.error_message =
                "The requested layer does not exist in the current scene.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(context.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The layer is locked in AviUtl2.";
            return;
        }
        if (context.name != nullptr &&
            context.name->has_value() &&
            edit->set_layer_name == nullptr) {
            context.result.error_code =
                "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 layer name API is unavailable.";
            return;
        }
        if (context.enabled != nullptr &&
            context.enabled->has_value() &&
            edit->set_layer_enable == nullptr) {
            context.result.error_code =
                "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 layer visibility API is unavailable.";
            return;
        }
        if (context.name != nullptr &&
            context.name->has_value()) {
            const std::wstring& name = context.name->value();
            edit->set_layer_name(
                context.layer,
                name.empty() ? nullptr : name.c_str());
            ++context.result.applied_count;
        }
        if (context.enabled != nullptr &&
            context.enabled->has_value()) {
            edit->set_layer_enable(
                context.layer,
                context.enabled->value());
            ++context.result.applied_count;
        }
        if (context.result.applied_count == 0U) {
            context.result.error_code = "INVALID_ARGUMENT";
            context.result.error_message =
                "At least one layer property must be supplied.";
            return;
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "LAYER_UPDATE_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "LAYER_UPDATE_FAILED";
        context.result.error_message =
            "The layer edit failed inside the SDK callback.";
    }
}

struct ObjectNameMutationContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    const std::optional<std::wstring>* name = nullptr;
    ObjectMutationResult result;
};

void object_name_mutation_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<ObjectNameMutationContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        CapturedObject& object =
            timeline.objects[context.object_index];
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }
        if (context.name == nullptr ||
            !context.name->has_value() ||
            edit->set_object_name == nullptr) {
            context.result.error_code =
                "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 object name API is unavailable.";
            return;
        }
        const std::wstring& name = context.name->value();
        edit->set_object_name(
            object.handle,
            name.empty() ? nullptr : name.c_str());
        context.result.applied_count = 1U;
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "OBJECT_NAME_UPDATE_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "OBJECT_NAME_UPDATE_FAILED";
        context.result.error_message =
            "The object name edit failed inside the SDK callback.";
    }
}

struct SceneContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    const SceneUpdate* update = nullptr;
    SceneInfoResult result;
};

void scene_read_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<SceneContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.revision = timeline.revision;
        context.result.info = SceneInfo{
            timeline.scene_id,
            timeline.scene_name,
            timeline.width,
            timeline.height,
            timeline.rate,
            timeline.scale,
            timeline.sample_rate,
        };
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SCENE_INSPECTION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SCENE_INSPECTION_FAILED";
        context.result.error_message =
            "The current scene could not be inspected.";
    }
}

void scene_update_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<SceneContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The scene changed after it was inspected.";
            return;
        }
        if (context.update == nullptr) {
            context.result.error_code = "INVALID_ARGUMENT";
            context.result.error_message =
                "A scene update is required.";
            return;
        }
        const SceneUpdate& update = *context.update;
        if (update.name.has_value() &&
            edit->set_scene_name == nullptr) {
            context.result.error_code =
                "SCENE_UPDATE_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 scene name API is unavailable.";
            return;
        }
        if ((update.width.has_value() ||
             update.height.has_value()) &&
            edit->set_scene_size == nullptr) {
            context.result.error_code =
                "SCENE_UPDATE_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 scene size API is unavailable.";
            return;
        }
        if ((update.rate.has_value() ||
             update.scale.has_value()) &&
            edit->set_scene_frame_rate == nullptr) {
            context.result.error_code =
                "SCENE_UPDATE_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 scene frame rate API is unavailable.";
            return;
        }
        if (update.sample_rate.has_value() &&
            edit->set_scene_sample_rate == nullptr) {
            context.result.error_code =
                "SCENE_UPDATE_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 scene sample rate API is unavailable.";
            return;
        }

        if (update.name.has_value()) {
            edit->set_scene_name(update.name->c_str());
            timeline.scene_name =
                wide_to_utf8(update.name.value());
        }
        if (update.width.has_value()) {
            edit->set_scene_size(
                update.width.value(),
                update.height.value());
            timeline.width = update.width.value();
            timeline.height = update.height.value();
        }
        if (update.rate.has_value()) {
            edit->set_scene_frame_rate(
                update.rate.value(),
                update.scale.value());
            timeline.rate = update.rate.value();
            timeline.scale = update.scale.value();
        }
        if (update.sample_rate.has_value()) {
            edit->set_scene_sample_rate(
                update.sample_rate.value());
            timeline.sample_rate =
                update.sample_rate.value();
        }
        timeline.revision = timeline_revision(
            timeline.scene_id,
            timeline.scene_name,
            timeline.width,
            timeline.height,
            timeline.rate,
            timeline.scale,
            timeline.sample_rate,
            timeline.layers,
            timeline.objects);
        context.result.revision = timeline.revision;
        context.result.info = SceneInfo{
            timeline.scene_id,
            timeline.scene_name,
            timeline.width,
            timeline.height,
            timeline.rate,
            timeline.scale,
            timeline.sample_rate,
        };
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SCENE_UPDATE_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SCENE_UPDATE_FAILED";
        context.result.error_message =
            "The scene update failed inside the SDK callback.";
    }
}

struct SnapshotContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    SnapshotResult result;
};

void snapshot_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<SnapshotContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.revision = timeline.revision;
        context.result.scene_id = timeline.scene_id;
        context.result.objects.reserve(timeline.objects.size());
        for (std::size_t index = 0U;
             index < timeline.objects.size();
             ++index) {
            CapturedObject& captured = timeline.objects[index];
            context.result.objects.push_back(SnapshotObject{
                "obj-" + std::to_string(timeline.revision) + "-" +
                    std::to_string(index),
                captured.layer,
                captured.frame_start,
                captured.frame_end,
                captured.has_name,
                std::move(captured.name),
                std::move(captured.alias),
                captured.api_locked,
            });
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SNAPSHOT_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SNAPSHOT_FAILED";
        context.result.error_message = "The snapshot could not be captured.";
    }
}

enum class MutationType {
    set_items,
    move,
    remove,
};

struct MutationContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    MutationType type = MutationType::set_items;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    const std::vector<ObjectItemUpdate>* updates = nullptr;
    int destination_layer = 0;
    int destination_frame = 0;
    ObjectMutationResult result;
};

void mutation_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<MutationContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }

        CapturedObject& object = timeline.objects[context.object_index];
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }
        if (context.type == MutationType::set_items) {
            if (context.updates == nullptr ||
                edit->get_object_item_value == nullptr ||
                edit->set_object_item_value == nullptr) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 item editing API is unavailable.";
                return;
            }
            for (const ObjectItemUpdate& update : *context.updates) {
                if (edit->get_object_item_value(
                        object.handle,
                        update.effect.c_str(),
                        update.item.c_str()) == nullptr) {
                    context.result.error_code = "OBJECT_ITEM_NOT_FOUND";
                    context.result.error_message =
                        "The requested effect item does not exist.";
                    return;
                }
                if (edit->find_effect != nullptr &&
                    edit->get_effect_lock != nullptr) {
                    const EFFECT_HANDLE effect = edit->find_effect(
                        object.handle,
                        update.effect.c_str());
                    if (effect != nullptr &&
                        edit->get_effect_lock(effect)) {
                        context.result.error_code = "EFFECT_LOCKED";
                        context.result.error_message =
                            "The requested effect is locked in AviUtl2.";
                        return;
                    }
                }
            }
            for (const ObjectItemUpdate& update : *context.updates) {
                if (!edit->set_object_item_value(
                        object.handle,
                        update.effect.c_str(),
                        update.item.c_str(),
                        update.value.c_str())) {
                    context.result.error_code = "ITEM_UPDATE_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected an object item value.";
                    return;
                }
                ++context.result.applied_count;
            }
        } else if (context.type == MutationType::move) {
            if (edit->move_object == nullptr) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 object move API is unavailable.";
                return;
            }
            if (edit->get_layer_lock != nullptr &&
                edit->get_layer_lock(context.destination_layer)) {
                context.result.error_code = "LAYER_LOCKED";
                context.result.error_message =
                    "The destination layer is locked in AviUtl2.";
                return;
            }
            if (object.layer != context.destination_layer ||
                object.frame_start != context.destination_frame) {
                if (!edit->move_object(
                        object.handle,
                        context.destination_layer,
                        context.destination_frame)) {
                    context.result.error_code = "PLACEMENT_COLLISION";
                    context.result.error_message =
                        "AviUtl2 rejected the destination placement.";
                    return;
                }
            }
            context.result.applied_count = 1U;
        } else {
            if (edit->delete_object == nullptr) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 object delete API is unavailable.";
                return;
            }
            edit->delete_object(object.handle);
            context.result.applied_count = 1U;
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "OBJECT_MUTATION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "OBJECT_MUTATION_FAILED";
        context.result.error_message =
            "The object edit failed inside the SDK callback.";
    }
}

enum class EffectMutationType {
    add,
    remove,
    set_enabled,
};

struct EffectMutationContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    EffectMutationType type = EffectMutationType::add;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    const std::wstring* effect = nullptr;
    const std::vector<EffectInitialItem>* initial_items = nullptr;
    bool enabled = true;
    ObjectMutationResult result;
};

void effect_mutation_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<EffectMutationContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        if (edit == nullptr || context.effect == nullptr) {
            context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 effect editing API is unavailable.";
            return;
        }
        CapturedObject& object = timeline.objects[context.object_index];
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }

        if (context.type == EffectMutationType::add) {
            if (edit->create_effect == nullptr) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 effect creation API is unavailable.";
                return;
            }
            if (context.initial_items != nullptr &&
                !context.initial_items->empty() &&
                (edit->get_effect_item_value == nullptr ||
                 edit->set_effect_item_value == nullptr ||
                 edit->delete_effect == nullptr)) {
                context.result.error_code =
                    "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 effect item API is unavailable.";
                return;
            }
            const EFFECT_HANDLE created = edit->create_effect(
                    object.handle,
                    context.effect->c_str());
            if (created == nullptr) {
                context.result.error_code = "EFFECT_CREATE_FAILED";
                context.result.error_message =
                    "AviUtl2 rejected the requested effect.";
                return;
            }
            if (context.initial_items != nullptr &&
                !context.initial_items->empty()) {
                for (const EffectInitialItem& item :
                     *context.initial_items) {
                    if (edit->get_effect_item_value(
                            created,
                            item.item.c_str()) == nullptr) {
                        edit->delete_effect(object.handle, created);
                        context.result.error_code =
                            "OBJECT_ITEM_NOT_FOUND";
                        context.result.error_message =
                            "An initial effect item does not exist.";
                        return;
                    }
                }
                for (const EffectInitialItem& item :
                     *context.initial_items) {
                    if (!edit->set_effect_item_value(
                            created,
                            item.item.c_str(),
                            item.value.c_str())) {
                        edit->delete_effect(object.handle, created);
                        context.result.error_code =
                            "ITEM_UPDATE_FAILED";
                        context.result.error_message =
                            "AviUtl2 rejected an initial effect item value.";
                        return;
                    }
                }
            }
        } else {
            if (edit->find_effect == nullptr ||
                (context.type == EffectMutationType::remove &&
                 edit->delete_effect == nullptr) ||
                (context.type ==
                     EffectMutationType::set_enabled &&
                 edit->set_effect_enable == nullptr)) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The requested AviUtl2 effect mutation API is unavailable.";
                return;
            }
            const EFFECT_HANDLE effect = edit->find_effect(
                object.handle,
                context.effect->c_str());
            if (effect == nullptr) {
                context.result.error_code = "EFFECT_NOT_FOUND";
                context.result.error_message =
                    "The requested effect selector does not exist.";
                return;
            }
            if (edit->get_effect_lock != nullptr &&
                edit->get_effect_lock(effect)) {
                context.result.error_code = "EFFECT_LOCKED";
                context.result.error_message =
                    "The requested effect is locked in AviUtl2.";
                return;
            }
            if (context.type == EffectMutationType::remove) {
                if (!edit->delete_effect(object.handle, effect)) {
                    context.result.error_code =
                        "EFFECT_DELETE_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected deletion of the requested effect.";
                    return;
                }
            } else {
                edit->set_effect_enable(effect, context.enabled);
            }
        }
        context.result.applied_count = 1U;
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "EFFECT_MUTATION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "EFFECT_MUTATION_FAILED";
        context.result.error_message =
            "The effect edit failed inside the SDK callback.";
    }
}

struct ObjectSectionsContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    ObjectSectionsResult result;
};

void object_sections_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<ObjectSectionsContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        if (edit->get_object_section_num == nullptr ||
            edit->get_object_section_frame == nullptr) {
            context.result.error_code =
                "READ_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 object section inspection API is unavailable.";
            context.result.retryable = true;
            return;
        }
        const CapturedObject& object =
            timeline.objects[context.object_index];
        const int count =
            edit->get_object_section_num(object.handle);
        if (count <= 0 || count > 100000) {
            context.result.error_code =
                "INVALID_HOST_SECTION_DATA";
            context.result.error_message =
                "AviUtl2 returned an invalid section count.";
            return;
        }
        context.result.sections.reserve(
            static_cast<std::size_t>(count));
        int previous = -1;
        for (int index = 0; index < count; ++index) {
            const int frame = edit->get_object_section_frame(
                object.handle,
                index);
            if (frame < object.frame_start ||
                frame > object.frame_end ||
                frame <= previous) {
                context.result.error_code =
                    "INVALID_HOST_SECTION_DATA";
                context.result.error_message =
                    "AviUtl2 returned invalid section boundaries.";
                context.result.sections.clear();
                return;
            }
            context.result.sections.push_back(
                ObjectSectionInfo{index, frame});
            previous = frame;
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SECTION_INSPECTION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SECTION_INSPECTION_FAILED";
        context.result.error_message =
            "The section inspection failed inside the SDK callback.";
    }
}

enum class SectionMutationType {
    create,
    remove,
    move,
};

struct SectionMutationContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    SectionMutationType type = SectionMutationType::create;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    int section = -1;
    int frame = -1;
    ObjectMutationResult result;
};

void section_mutation_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<SectionMutationContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        CapturedObject& object =
            timeline.objects[context.object_index];
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }
        if (edit->get_effect_list != nullptr &&
            edit->get_effect_lock != nullptr) {
            const int effect_count =
                edit->get_effect_list(object.handle, nullptr, 0);
            if (effect_count < 0 ||
                static_cast<std::size_t>(effect_count) >
                    kMaxInspectEffects) {
                context.result.error_code =
                    "HOST_INSPECTION_FAILED";
                context.result.error_message =
                    "AviUtl2 returned an invalid effect list.";
                return;
            }
            std::vector<EFFECT_HANDLE> effects(
                static_cast<std::size_t>(effect_count));
            if (effect_count > 0 &&
                edit->get_effect_list(
                    object.handle,
                    effects.data(),
                    effect_count) != effect_count) {
                context.result.error_code =
                    "HOST_INSPECTION_FAILED";
                context.result.error_message =
                    "AviUtl2 could not return a stable effect list.";
                return;
            }
            if (std::any_of(
                    effects.begin(),
                    effects.end(),
                    [&](const EFFECT_HANDLE effect) {
                        return edit->get_effect_lock(effect);
                    })) {
                context.result.error_code = "EFFECT_LOCKED";
                context.result.error_message =
                    "A locked effect prevents section structure edits.";
                return;
            }
        }
        if (edit->get_object_section_num == nullptr ||
            edit->get_object_section_frame == nullptr) {
            context.result.error_code =
                "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 object section API is unavailable.";
            return;
        }
        const int count =
            edit->get_object_section_num(object.handle);
        if (count <= 0 || count > 100000) {
            context.result.error_code =
                "INVALID_HOST_SECTION_DATA";
            context.result.error_message =
                "AviUtl2 returned an invalid section count.";
            return;
        }
        std::vector<int> frames(
            static_cast<std::size_t>(count));
        for (int index = 0; index < count; ++index) {
            frames[static_cast<std::size_t>(index)] =
                edit->get_object_section_frame(
                    object.handle,
                    index);
        }

        bool applied = false;
        if (context.type == SectionMutationType::create) {
            if (edit->create_object_section == nullptr) {
                context.result.error_code =
                    "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "The AviUtl2 section creation API is unavailable.";
                return;
            }
            if (context.frame <= object.frame_start ||
                context.frame > object.frame_end ||
                std::binary_search(
                    frames.begin(),
                    frames.end(),
                    context.frame)) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "A section frame must be a new boundary inside the object.";
                return;
            }
            applied = edit->create_object_section(
                object.handle,
                context.frame);
        } else {
            if (context.section <= 0 ||
                context.section >= count) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "section must identify an existing middle boundary.";
                return;
            }
            if (context.type == SectionMutationType::remove) {
                if (edit->delete_object_section == nullptr) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "The AviUtl2 section deletion API is unavailable.";
                    return;
                }
                applied = edit->delete_object_section(
                    object.handle,
                    context.section);
            } else {
                if (edit->move_object_section == nullptr) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "The AviUtl2 section move API is unavailable.";
                    return;
                }
                const int previous = frames[
                    static_cast<std::size_t>(
                        context.section - 1)];
                const bool has_next =
                    context.section + 1 < count;
                const int next =
                    has_next
                        ? frames[static_cast<std::size_t>(
                              context.section + 1)]
                        : object.frame_end;
                if (context.frame <= previous ||
                    (has_next && context.frame >= next) ||
                    (!has_next && context.frame > next)) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "A section cannot cross an adjacent boundary.";
                    return;
                }
                applied = edit->move_object_section(
                    object.handle,
                    context.section,
                    context.frame);
            }
        }
        if (!applied) {
            context.result.error_code =
                "SECTION_MUTATION_FAILED";
            context.result.error_message =
                "AviUtl2 rejected the requested section edit.";
            return;
        }
        context.result.applied_count = 1U;
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SECTION_MUTATION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SECTION_MUTATION_FAILED";
        context.result.error_message =
            "The section edit failed inside the SDK callback.";
    }
}

struct SplitMediaContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    int frame = 0;
    SplitMediaResult result;
};

[[nodiscard]] bool parse_static_number(
    const char* raw,
    double& value) noexcept {
    if (raw == nullptr) {
        return false;
    }
    const std::size_t length = strnlen_s(raw, 256U);
    if (length == 0U || length >= 256U) {
        return false;
    }
    const auto parsed = std::from_chars(
        raw,
        raw + length,
        value,
        std::chars_format::general);
    return parsed.ec == std::errc{} &&
           parsed.ptr == raw + length &&
           std::isfinite(value);
}

struct StaticPosition final {
    double value = 0.0;
    bool track_format = false;
    std::string suffix;
};

[[nodiscard]] bool parse_number_token(
    const std::string_view token,
    double& value) noexcept {
    if (token.empty()) {
        return false;
    }
    const auto parsed = std::from_chars(
        token.data(),
        token.data() + token.size(),
        value,
        std::chars_format::general);
    return parsed.ec == std::errc{} &&
           parsed.ptr == token.data() + token.size() &&
           std::isfinite(value);
}

[[nodiscard]] bool equivalent_alias_value(
    const std::string_view requested,
    const std::string_view actual) noexcept {
    if (requested == actual) {
        return true;
    }
    std::size_t requested_start = 0U;
    std::size_t actual_start = 0U;
    while (true) {
        const std::size_t requested_end = requested.find(',', requested_start);
        const std::size_t actual_end = actual.find(',', actual_start);
        const std::string_view requested_token = requested.substr(
            requested_start,
            requested_end == std::string_view::npos
                ? std::string_view::npos
                : requested_end - requested_start);
        const std::string_view actual_token = actual.substr(
            actual_start,
            actual_end == std::string_view::npos
                ? std::string_view::npos
                : actual_end - actual_start);
        double requested_number = 0.0;
        double actual_number = 0.0;
        if (parse_number_token(requested_token, requested_number) &&
            parse_number_token(actual_token, actual_number)) {
            if (std::abs(requested_number - actual_number) > 0.000001) {
                return false;
            }
        } else if (requested_token != actual_token) {
            return false;
        }
        if (requested_end == std::string_view::npos ||
            actual_end == std::string_view::npos) {
            return requested_end == actual_end;
        }
        requested_start = requested_end + 1U;
        actual_start = actual_end + 1U;
    }
}

[[nodiscard]] bool parse_static_position(
    const char* raw,
    StaticPosition& position) {
    if (raw == nullptr) {
        return false;
    }
    constexpr std::size_t max_position_bytes = 512U;
    const std::size_t length =
        strnlen_s(raw, max_position_bytes);
    if (length == 0U || length >= max_position_bytes) {
        return false;
    }
    const std::string_view value(raw, length);
    const std::size_t first_comma = value.find(',');
    if (first_comma == std::string_view::npos) {
        return parse_number_token(value, position.value) &&
               position.value >= 0.0;
    }
    const std::size_t second_comma =
        value.find(',', first_comma + 1U);
    if (second_comma == std::string_view::npos ||
        second_comma + 1U >= value.size()) {
        return false;
    }
    double start = 0.0;
    double end = 0.0;
    if (!parse_number_token(value.substr(0U, first_comma), start) ||
        !parse_number_token(
            value.substr(
                first_comma + 1U,
                second_comma - first_comma - 1U),
            end) ||
        start < 0.0 || start != end) {
        return false;
    }
    position.value = start;
    position.track_format = true;
    position.suffix.assign(value.substr(second_comma));
    return position.suffix.find('\r') == std::string::npos &&
           position.suffix.find('\n') == std::string::npos &&
           position.suffix.find('\0') == std::string::npos;
}

[[nodiscard]] std::string format_static_number(
    const double value) {
    std::array<char, 64U> buffer{};
    const auto formatted = std::to_chars(
        buffer.data(),
        buffer.data() + buffer.size(),
        value,
        std::chars_format::fixed,
        6);
    if (formatted.ec != std::errc{}) {
        throw std::runtime_error("media source position formatting failed");
    }
    return std::string(buffer.data(), formatted.ptr);
}

[[nodiscard]] std::string format_static_position(
    const StaticPosition& format,
    const double value) {
    const std::string number = format_static_number(value);
    if (!format.track_format) {
        return number;
    }
    return number + "," + number + format.suffix;
}

void split_media_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<SplitMediaContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        CapturedObject& object = timeline.objects[context.object_index];
        if (context.frame <= object.frame_start ||
            context.frame > object.frame_end) {
            context.result.error_code = "SPLIT_OUTSIDE_OBJECT";
            context.result.error_message =
                "The split frame must be strictly inside the object.";
            return;
        }
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit == nullptr ||
            edit->get_effect_list == nullptr ||
            edit->get_effect_name == nullptr ||
            edit->get_object_item_value == nullptr ||
            edit->set_object_item_value == nullptr ||
            edit->create_object_from_alias == nullptr ||
            edit->delete_object == nullptr ||
            edit->move_object == nullptr ||
            edit->get_object_layer_frame == nullptr) {
            context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 media split APIs are unavailable.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }
        if (edit->get_object_section_num != nullptr &&
            edit->get_object_section_num(object.handle) > 1) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "Objects with multiple sections are not safely splittable.";
            return;
        }

        const int effect_count =
            edit->get_effect_list(object.handle, nullptr, 0);
        if (effect_count <= 0) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "The object has no inspectable media input effect.";
            return;
        }
        std::vector<EFFECT_HANDLE> effects(
            static_cast<std::size_t>(effect_count));
        if (edit->get_effect_list(
                object.handle,
                effects.data(),
                effect_count) != effect_count) {
            context.result.error_code = "HOST_INSPECTION_FAILED";
            context.result.error_message =
                "AviUtl2 could not return a stable effect list.";
            return;
        }
        const LPCWSTR media_effect =
            edit->get_effect_name(effects.front());
        if (media_effect == nullptr ||
            (std::wcscmp(media_effect, L"動画ファイル") != 0 &&
             std::wcscmp(media_effect, L"音声ファイル") != 0)) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "Only basic video and audio file objects are supported.";
            return;
        }
        if (edit->get_effect_lock != nullptr &&
            edit->get_effect_lock(effects.front())) {
            context.result.error_code = "EFFECT_LOCKED";
            context.result.error_message =
                "The media input effect is locked in AviUtl2.";
            return;
        }
        const std::wstring media_effect_name(media_effect);

        StaticPosition source_position;
        double speed_percent = 0.0;
        if (!parse_static_position(
                edit->get_object_item_value(
                    object.handle,
                    media_effect_name.c_str(),
                    L"再生位置"),
                source_position) ||
            !parse_static_number(
                edit->get_object_item_value(
                    object.handle,
                    media_effect_name.c_str(),
                    L"再生速度"),
                speed_percent) ||
            speed_percent <= 0.0) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "Animated or invalid playback position/speed cannot be split safely.";
            return;
        }
        const int left_length =
            context.frame - object.frame_start;
        const int right_length =
            object.frame_end - context.frame + 1;
        const double right_source_position =
            source_position.value +
            static_cast<double>(left_length) *
                speed_percent / 100.0;
        if (!std::isfinite(right_source_position)) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "The calculated source position is outside the supported range.";
            return;
        }
        const std::string position_value =
            format_static_position(
                source_position,
                right_source_position);

        const std::string alias =
            strip_object_alias_frame_range(object.alias);
        const int layer = object.layer;
        const int original_start = object.frame_start;
        const int original_length =
            object.frame_end - object.frame_start + 1;

        int timeline_end = 0;
        for (const CapturedObject& candidate : timeline.objects) {
            timeline_end = std::max(timeline_end, candidate.frame_end);
        }
        if (timeline_end >=
            std::numeric_limits<int>::max() - original_length) {
            context.result.error_code = "SPLIT_UNSAFE";
            context.result.error_message =
                "The timeline is too long to reserve a temporary split position.";
            return;
        }
        const int scratch_frame = timeline_end + 1;
        const char* failure_stage = "move_original_to_scratch";
        bool completed = edit->move_object(
            object.handle,
            layer,
            scratch_frame);
        const bool moved_to_scratch = completed;
        if (completed) {
            const OBJECT_LAYER_FRAME scratch_range =
                edit->get_object_layer_frame(object.handle);
            completed =
                scratch_range.layer == layer &&
                scratch_range.start == scratch_frame &&
                scratch_range.end ==
                    scratch_frame + original_length - 1;
        }

        OBJECT_HANDLE left = nullptr;
        OBJECT_HANDLE right = nullptr;
        if (completed) {
            failure_stage = "create_left";
            left = edit->create_object_from_alias(
                alias.c_str(),
                layer,
                original_start,
                left_length);
            completed = left != nullptr;
        }
        if (completed) {
            failure_stage = "create_right";
            right = edit->create_object_from_alias(
                alias.c_str(),
                layer,
                context.frame,
                right_length);
            completed = right != nullptr;
        }
        if (completed) {
            failure_stage = "set_right_source_position";
            completed = edit->set_object_item_value(
                right,
                media_effect_name.c_str(),
                L"再生位置",
                position_value.c_str());
        }
        if (completed) {
            failure_stage = "verify_created_ranges";
            const OBJECT_LAYER_FRAME left_range =
                edit->get_object_layer_frame(left);
            const OBJECT_LAYER_FRAME right_range =
                edit->get_object_layer_frame(right);
            completed =
                left_range.layer == layer &&
                left_range.start == original_start &&
                left_range.end == context.frame - 1 &&
                right_range.layer == layer &&
                right_range.start == context.frame &&
                right_range.end == object.frame_end;
        }
        if (!completed) {
            if (right != nullptr) {
                edit->delete_object(right);
            }
            if (left != nullptr) {
                edit->delete_object(left);
            }
            const bool restored =
                !moved_to_scratch ||
                edit->move_object(
                    object.handle,
                    layer,
                    original_start);
            context.result.error_code =
                !restored
                    ? "SPLIT_ROLLBACK_FAILED"
                    : "SPLIT_FAILED";
            context.result.error_message =
                !restored
                    ? std::string(
                          "The split failed at ") +
                          failure_stage +
                          " and AviUtl2 could not restore the original object."
                    : std::string(
                          "AviUtl2 rejected the split at ") +
                          failure_stage +
                          "; the original object was restored.";
            return;
        }
        edit->delete_object(object.handle);

        context.result.layer = layer;
        context.result.left_start = original_start;
        context.result.left_end = context.frame - 1;
        context.result.right_start = context.frame;
        context.result.right_end = object.frame_end;
        context.result.source_position_before =
            source_position.value;
        context.result.source_position_after = right_source_position;
        context.result.playback_rate = speed_percent / 100.0;
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "SPLIT_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "SPLIT_FAILED";
        context.result.error_message =
            "The media split failed inside the SDK callback.";
    }
}

enum class StructuralEditType {
    duration,
    trim,
    reorder,
};

struct StructuralEditContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    StructuralEditType type = StructuralEditType::duration;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    int duration = 0;
    int frame_start = -1;
    int frame_end = -1;
    const std::optional<double>* source_position = nullptr;
    const std::vector<std::wstring>* selectors = nullptr;
    const std::unordered_map<std::wstring, int>* effect_types =
        nullptr;
    StructuralEditResult result;
};

void structural_edit_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<StructuralEditContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        CapturedObject& object =
            timeline.objects[context.object_index];
        if (object.api_locked) {
            context.result.error_code = "OBJECT_API_LOCKED";
            context.result.error_message =
                "The object is locked against external API edits.";
            return;
        }
        if (edit == nullptr ||
            edit->get_effect_list == nullptr ||
            edit->get_effect_name == nullptr ||
            edit->get_object_alias == nullptr ||
            edit->create_object_from_alias == nullptr ||
            edit->delete_object == nullptr ||
            edit->move_object == nullptr ||
            edit->get_object_layer_frame == nullptr) {
            context.result.error_code =
                "EDIT_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 structural replacement APIs are unavailable.";
            return;
        }
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(object.layer)) {
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The object's layer is locked in AviUtl2.";
            return;
        }
        if (edit->get_object_section_num == nullptr ||
            edit->get_object_section_num(object.handle) != 1) {
            context.result.error_code =
                "STRUCTURAL_EDIT_UNSAFE";
            context.result.error_message =
                "Only single-section objects can be structurally replaced safely.";
            return;
        }

        const int effect_count =
            edit->get_effect_list(object.handle, nullptr, 0);
        if (effect_count <= 0 ||
            static_cast<std::size_t>(effect_count) >
                kMaxInspectEffects) {
            context.result.error_code =
                "STRUCTURAL_EDIT_UNSAFE";
            context.result.error_message =
                "The object has no stable inspectable effect list.";
            return;
        }
        std::vector<EFFECT_HANDLE> effect_handles(
            static_cast<std::size_t>(effect_count));
        if (edit->get_effect_list(
                object.handle,
                effect_handles.data(),
                effect_count) != effect_count) {
            context.result.error_code =
                "HOST_INSPECTION_FAILED";
            context.result.error_message =
                "AviUtl2 could not return a stable effect list.";
            return;
        }
        if (edit->get_effect_lock != nullptr &&
            std::any_of(
                effect_handles.begin(),
                effect_handles.end(),
                [&](const EFFECT_HANDLE effect) {
                    return edit->get_effect_lock(effect);
                })) {
            context.result.error_code = "EFFECT_LOCKED";
            context.result.error_message =
                "A locked effect prevents structural replacement.";
            return;
        }
        std::vector<std::wstring> effect_names;
        effect_names.reserve(effect_handles.size());
        for (const EFFECT_HANDLE effect : effect_handles) {
            const LPCWSTR name = edit->get_effect_name(effect);
            if (name == nullptr) {
                context.result.error_code =
                    "HOST_INSPECTION_FAILED";
                context.result.error_message =
                    "AviUtl2 returned an unnamed effect.";
                return;
            }
            effect_names.emplace_back(name);
        }

        int target_start = object.frame_start;
        int target_end = object.frame_end;
        std::string replacement_alias =
            strip_object_alias_frame_range(object.alias);
        std::string expected_alias = replacement_alias;
        std::wstring media_effect_name;
        std::string source_value;
        bool update_source = false;
        std::vector<std::wstring> expected_effect_names;

        if (context.type == StructuralEditType::duration) {
            if (context.duration <= 0 ||
                object.frame_start >
                    std::numeric_limits<int>::max() -
                        context.duration + 1) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "duration is outside the supported timeline range.";
                return;
            }
            target_end =
                object.frame_start + context.duration - 1;
        } else if (context.type == StructuralEditType::trim) {
            if (context.frame_start < 0 ||
                context.frame_end < context.frame_start) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "The requested trim range is invalid.";
                return;
            }
            const std::wstring& primary = effect_names.front();
            if ((std::wcscmp(
                     primary.c_str(),
                     L"蜍慕判繝輔ぃ繧､繝ｫ") != 0 &&
                 std::wcscmp(
                     primary.c_str(),
                     L"髻ｳ螢ｰ繝輔ぃ繧､繝ｫ") != 0) ||
                edit->get_object_item_value == nullptr ||
                edit->set_object_item_value == nullptr) {
                context.result.error_code =
                    "STRUCTURAL_EDIT_UNSAFE";
                context.result.error_message =
                    "Only basic fixed-speed video/audio file objects can be trimmed.";
                return;
            }
            StaticPosition current_source;
            double speed_percent = 0.0;
            if (!parse_static_position(
                    edit->get_object_item_value(
                        object.handle,
                        primary.c_str(),
                        L"蜀咲函菴咲ｽｮ"),
                    current_source) ||
                !parse_static_number(
                    edit->get_object_item_value(
                        object.handle,
                        primary.c_str(),
                        L"蜀咲函騾溷ｺｦ"),
                    speed_percent) ||
                speed_percent <= 0.0) {
                context.result.error_code =
                    "STRUCTURAL_EDIT_UNSAFE";
                context.result.error_message =
                    "Variable, reverse, or animated source playback cannot be trimmed safely.";
                return;
            }
            double desired_source =
                current_source.value +
                static_cast<double>(
                    context.frame_start - object.frame_start) *
                    speed_percent / 100.0;
            if (context.source_position != nullptr &&
                context.source_position->has_value()) {
                desired_source =
                    context.source_position->value();
            }
            if (!std::isfinite(desired_source) ||
                desired_source < 0.0) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "source_position must be a finite non-negative value.";
                return;
            }
            target_start = context.frame_start;
            target_end = context.frame_end;
            media_effect_name = primary;
            source_value = format_static_position(
                current_source,
                desired_source);
            expected_alias =
                replace_object_alias_effect_item(
                    replacement_alias,
                    0U,
                    wide_to_utf8(L"蜀咲函菴咲ｽｮ"),
                    source_value);
            context.result.has_source_position = true;
            context.result.source_position = desired_source;
            update_source = true;
        } else {
            if (context.selectors == nullptr ||
                context.effect_types == nullptr ||
                context.selectors->size() !=
                    effect_handles.size()) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "selectors must contain the complete effect order.";
                return;
            }
            std::unordered_map<std::wstring, int> totals;
            for (const std::wstring& name : effect_names) {
                ++totals[name];
            }
            std::unordered_map<std::wstring, int> occurrences;
            std::vector<std::wstring> current_selectors;
            current_selectors.reserve(effect_names.size());
            for (const std::wstring& name : effect_names) {
                const int occurrence = occurrences[name]++;
                current_selectors.push_back(
                    totals[name] > 1
                        ? name + L":" +
                              std::to_wstring(occurrence)
                        : name);
            }
            std::vector<std::size_t> order;
            order.reserve(context.selectors->size());
            std::vector<bool> used(effect_names.size(), false);
            for (const std::wstring& selector :
                 *context.selectors) {
                const auto found = std::find(
                    current_selectors.begin(),
                    current_selectors.end(),
                    selector);
                if (found == current_selectors.end()) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "selectors are not a permutation of the current effects.";
                    return;
                }
                const std::size_t index =
                    static_cast<std::size_t>(
                        found - current_selectors.begin());
                if (used[index]) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "selectors contain a duplicate effect.";
                    return;
                }
                used[index] = true;
                order.push_back(index);
            }
            for (std::size_t new_index = 0U;
                 new_index < order.size();
                 ++new_index) {
                const std::size_t old_index = order[new_index];
                const auto type = context.effect_types->find(
                    effect_names[old_index]);
                if (type == context.effect_types->end()) {
                    context.result.error_code =
                        "STRUCTURAL_EDIT_UNSAFE";
                    context.result.error_message =
                        "An effect is absent from the live catalog.";
                    return;
                }
                if ((type->second == 2 || type->second == 5) &&
                    old_index != new_index) {
                    context.result.error_code =
                        "STRUCTURAL_EDIT_UNSAFE";
                    context.result.error_message =
                        "Input/output effects must remain in their required positions.";
                    return;
                }
            }
            replacement_alias =
                reorder_object_alias_effects(
                    replacement_alias,
                    order);
            expected_alias = replacement_alias;
            expected_effect_names.reserve(order.size());
            for (const std::size_t old_index : order) {
                expected_effect_names.push_back(
                    effect_names[old_index]);
            }
            context.result.effect_order.reserve(
                context.selectors->size());
            for (const std::wstring& selector :
                 *context.selectors) {
                context.result.effect_order.push_back(
                    wide_to_utf8(selector));
            }
        }

        for (std::size_t index = 0U;
             index < timeline.objects.size();
             ++index) {
            if (index == context.object_index) {
                continue;
            }
            const CapturedObject& candidate =
                timeline.objects[index];
            if (candidate.layer == object.layer &&
                candidate.frame_start <= target_end &&
                target_start <= candidate.frame_end) {
                context.result.error_code =
                    "PLACEMENT_COLLISION";
                context.result.error_message =
                    "The structural replacement range collides with another object.";
                return;
            }
        }

        const int original_length =
            object.frame_end - object.frame_start + 1;
        const int target_length =
            target_end - target_start + 1;
        int timeline_end = 0;
        for (const CapturedObject& candidate : timeline.objects) {
            timeline_end =
                (std::max)(timeline_end, candidate.frame_end);
        }
        if (timeline_end >
            std::numeric_limits<int>::max() -
                original_length) {
            context.result.error_code =
                "STRUCTURAL_EDIT_UNSAFE";
            context.result.error_message =
                "The timeline is too long to reserve a scratch position.";
            return;
        }
        const int scratch_frame = timeline_end + 1;
        const std::wstring preserved_name =
            object.has_name
                ? utf8_to_wide(object.name)
                : std::wstring();
        if (object.has_name &&
            edit->set_object_name == nullptr) {
            context.result.error_code =
                "STRUCTURAL_EDIT_UNSAFE";
            context.result.error_message =
                "A custom object name cannot be preserved by this SDK.";
            return;
        }

        const char* failure_stage = "move_original_to_scratch";
        bool completed = edit->move_object(
            object.handle,
            object.layer,
            scratch_frame);
        const bool moved_to_scratch = completed;
        OBJECT_HANDLE replacement = nullptr;
        if (completed) {
            const OBJECT_LAYER_FRAME scratch =
                edit->get_object_layer_frame(object.handle);
            completed =
                scratch.layer == object.layer &&
                scratch.start == scratch_frame &&
                scratch.end ==
                    scratch_frame + original_length - 1;
        }
        if (completed) {
            failure_stage = "create_replacement";
            replacement = edit->create_object_from_alias(
                replacement_alias.c_str(),
                object.layer,
                target_start,
                target_length);
            completed = replacement != nullptr;
        }
        if (completed && update_source) {
            failure_stage = "set_source_position";
            completed = edit->set_object_item_value(
                replacement,
                media_effect_name.c_str(),
                L"蜀咲函菴咲ｽｮ",
                source_value.c_str());
        }
        if (completed && object.has_name) {
            failure_stage = "restore_object_name";
            edit->set_object_name(
                replacement,
                preserved_name.c_str());
        }
        if (completed) {
            failure_stage = "verify_range";
            const OBJECT_LAYER_FRAME range =
                edit->get_object_layer_frame(replacement);
            completed =
                range.layer == object.layer &&
                range.start == target_start &&
                range.end == target_end;
        }
        if (completed) {
            failure_stage = "verify_alias";
            const LPCSTR created_alias =
                edit->get_object_alias(replacement);
            completed =
                created_alias != nullptr &&
                strip_object_alias_frame_range(created_alias) ==
                    expected_alias;
        }
        if (completed &&
            context.type == StructuralEditType::reorder) {
            failure_stage = "verify_effect_order";
            std::vector<EFFECT_HANDLE> reordered(
                effect_handles.size());
            completed =
                edit->get_effect_list(
                    replacement,
                    reordered.data(),
                    static_cast<int>(reordered.size())) ==
                static_cast<int>(reordered.size());
            if (completed) {
                for (std::size_t index = 0U;
                     index < reordered.size();
                     ++index) {
                    const LPCWSTR name =
                        edit->get_effect_name(reordered[index]);
                    if (name == nullptr ||
                        index >= expected_effect_names.size() ||
                        expected_effect_names[index] != name) {
                        completed = false;
                        break;
                    }
                }
            }
        }
        if (!completed) {
            if (replacement != nullptr) {
                edit->delete_object(replacement);
            }
            const bool restored =
                !moved_to_scratch ||
                edit->move_object(
                    object.handle,
                    object.layer,
                    object.frame_start);
            context.result.error_code =
                restored
                    ? "STRUCTURAL_EDIT_FAILED"
                    : "STRUCTURAL_EDIT_ROLLBACK_FAILED";
            context.result.error_message =
                restored
                    ? std::string(
                          "AviUtl2 rejected the structural edit at ") +
                          failure_stage +
                          "; the original object was restored."
                    : std::string(
                          "The structural edit failed at ") +
                          failure_stage +
                          " and the original object could not be restored.";
            return;
        }
        edit->delete_object(object.handle);
        context.result.layer = object.layer;
        context.result.frame_start = target_start;
        context.result.frame_end = target_end;
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "STRUCTURAL_EDIT_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "STRUCTURAL_EDIT_FAILED";
        context.result.error_message =
            "The structural edit failed inside the SDK callback.";
    }
}

struct TimelineTransactionContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    const std::vector<TimelineCommand>* commands = nullptr;
    bool apply = false;
    TimelineTransactionResult result;
};

struct TransactionRollback final {
    TimelineCommandType type = TimelineCommandType::set_items;
    OBJECT_HANDLE object = nullptr;
    EFFECT_HANDLE effect = nullptr;
    std::wstring effect_selector;
    std::wstring item;
    std::string raw_value;
    bool effect_enabled = true;
    bool has_name = false;
    std::wstring name;
};

void timeline_transaction_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<TimelineTransactionContext*>(parameter);
    try {
        if (context.commands == nullptr ||
            context.commands->empty() ||
            context.commands->size() > kMaxTimelineCommands) {
            context.result.error_code = "INVALID_ARGUMENT";
            context.result.error_message =
                "A transaction must contain between 1 and 4096 commands.";
            return;
        }
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the transaction was prepared.";
            return;
        }

        struct Placement final {
            int layer = 0;
            int start = 0;
            int end = 0;
            bool changed = false;
            bool removed = false;
        };
        std::vector<Placement> placements;
        placements.reserve(timeline.objects.size());
        for (const CapturedObject& object : timeline.objects) {
            placements.push_back(Placement{
                object.layer,
                object.frame_start,
                object.frame_end,
                false,
                false,
            });
        }

        std::vector<bool> has_move(
            timeline.objects.size(),
            false);
        std::vector<bool> has_remove(
            timeline.objects.size(),
            false);
        std::vector<TransactionRollback> rollback;
        for (std::size_t command_index = 0U;
             command_index < context.commands->size();
             ++command_index) {
            const TimelineCommand& command =
                (*context.commands)[command_index];
            context.result.failed_command_index = command_index;
            if (command.object_index >= timeline.objects.size()) {
                context.result.error_code = "OBJECT_NOT_FOUND";
                context.result.error_message =
                    "A transaction target does not exist.";
                return;
            }
            CapturedObject& object =
                timeline.objects[command.object_index];
            if (object.api_locked) {
                context.result.error_code =
                    "OBJECT_API_LOCKED";
                context.result.error_message =
                    "A transaction target is locked against external API edits.";
                return;
            }
            if (edit->get_layer_lock != nullptr &&
                edit->get_layer_lock(object.layer)) {
                context.result.error_code = "LAYER_LOCKED";
                context.result.error_message =
                    "A transaction target layer is locked.";
                return;
            }
            if (has_remove[command.object_index]) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "No command may follow deletion of the same object.";
                return;
            }

            if (command.type == TimelineCommandType::move) {
                if (edit->move_object == nullptr ||
                    command.layer < 0 || command.frame < 0 ||
                    has_move[command.object_index]) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "Each object may have one valid move command.";
                    return;
                }
                if (edit->get_layer_lock != nullptr &&
                    edit->get_layer_lock(command.layer)) {
                    context.result.error_code = "LAYER_LOCKED";
                    context.result.error_message =
                        "A transaction destination layer is locked.";
                    return;
                }
                const int length =
                    object.frame_end - object.frame_start + 1;
                if (command.frame >
                    std::numeric_limits<int>::max() -
                        length + 1) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "A transaction move exceeds the timeline range.";
                    return;
                }
                Placement& placement =
                    placements[command.object_index];
                placement.layer = command.layer;
                placement.start = command.frame;
                placement.end = command.frame + length - 1;
                placement.changed = true;
                has_move[command.object_index] = true;
            } else if (
                command.type == TimelineCommandType::remove) {
                if (edit->delete_object == nullptr ||
                    has_move[command.object_index]) {
                    context.result.error_code =
                        "INVALID_ARGUMENT";
                    context.result.error_message =
                        "A removed object must not also be moved.";
                    return;
                }
                placements[command.object_index].removed = true;
                placements[command.object_index].changed = true;
                has_remove[command.object_index] = true;
            } else if (
                command.type ==
                TimelineCommandType::set_items) {
                if (command.updates.empty() ||
                    edit->get_object_item_value == nullptr ||
                    edit->set_object_item_value == nullptr) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "The transaction item API is unavailable.";
                    return;
                }
                for (const ObjectItemUpdate& update :
                     command.updates) {
                    const LPCSTR old_value =
                        edit->get_object_item_value(
                            object.handle,
                            update.effect.c_str(),
                            update.item.c_str());
                    if (old_value == nullptr) {
                        context.result.error_code =
                            "OBJECT_ITEM_NOT_FOUND";
                        context.result.error_message =
                            "A transaction item does not exist.";
                        return;
                    }
                    if (edit->find_effect != nullptr &&
                        edit->get_effect_lock != nullptr) {
                        const EFFECT_HANDLE effect =
                            edit->find_effect(
                                object.handle,
                                update.effect.c_str());
                        if (effect != nullptr &&
                            edit->get_effect_lock(effect)) {
                            context.result.error_code =
                                "EFFECT_LOCKED";
                            context.result.error_message =
                                "A transaction effect is locked.";
                            return;
                        }
                    }
                    rollback.push_back(TransactionRollback{
                        TimelineCommandType::set_items,
                        object.handle,
                        nullptr,
                        update.effect,
                        update.item,
                        std::string(old_value),
                        true,
                        false,
                        {},
                    });
                }
            } else if (
                command.type ==
                TimelineCommandType::set_effect_enabled) {
                if (edit->find_effect == nullptr ||
                    edit->get_effect_enable == nullptr ||
                    edit->set_effect_enable == nullptr) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "The transaction effect state API is unavailable.";
                    return;
                }
                const EFFECT_HANDLE effect = edit->find_effect(
                    object.handle,
                    command.effect_selector.c_str());
                if (effect == nullptr) {
                    context.result.error_code =
                        "EFFECT_NOT_FOUND";
                    context.result.error_message =
                        "A transaction effect does not exist.";
                    return;
                }
                if (edit->get_effect_lock != nullptr &&
                    edit->get_effect_lock(effect)) {
                    context.result.error_code = "EFFECT_LOCKED";
                    context.result.error_message =
                        "A transaction effect is locked.";
                    return;
                }
                rollback.push_back(TransactionRollback{
                    TimelineCommandType::set_effect_enabled,
                    object.handle,
                    effect,
                    {},
                    {},
                    {},
                    edit->get_effect_enable(effect),
                    false,
                    {},
                });
            } else {
                if (!command.name.has_value() ||
                    edit->set_object_name == nullptr) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "The transaction object name API is unavailable.";
                    return;
                }
                rollback.push_back(TransactionRollback{
                    TimelineCommandType::set_name,
                    object.handle,
                    nullptr,
                    {},
                    {},
                    {},
                    true,
                    object.has_name,
                    object.has_name
                        ? utf8_to_wide(object.name)
                        : std::wstring(),
                });
            }
        }

        for (std::size_t left = 0U;
             left < placements.size();
             ++left) {
            if (placements[left].removed) {
                continue;
            }
            for (std::size_t right = left + 1U;
                 right < placements.size();
                 ++right) {
                if (placements[right].removed ||
                    (!placements[left].changed &&
                     !placements[right].changed) ||
                    placements[left].layer !=
                        placements[right].layer ||
                    placements[left].end <
                        placements[right].start ||
                    placements[right].end <
                        placements[left].start) {
                    continue;
                }
                context.result.error_code =
                    "PLACEMENT_COLLISION";
                context.result.error_message =
                    "The transaction final layout contains a collision.";
                context.result.has_collision = true;
                context.result.collision_layer =
                    placements[left].layer;
                context.result.collision_start = (std::max)(
                    placements[left].start,
                    placements[right].start);
                context.result.collision_end = (std::min)(
                    placements[left].end,
                    placements[right].end);
                return;
            }
        }
        context.result.failed_command_index =
            std::numeric_limits<std::size_t>::max();
        context.result.valid = true;
        if (!context.apply) {
            context.result.ok = true;
            return;
        }

        const auto rollback_metadata = [&]() noexcept {
            for (auto iterator = rollback.rbegin();
                 iterator != rollback.rend();
                 ++iterator) {
                if (iterator->type ==
                    TimelineCommandType::set_items) {
                    edit->set_object_item_value(
                        iterator->object,
                        iterator->effect_selector.c_str(),
                        iterator->item.c_str(),
                        iterator->raw_value.c_str());
                } else if (
                    iterator->type ==
                    TimelineCommandType::set_effect_enabled) {
                    edit->set_effect_enable(
                        iterator->effect,
                        iterator->effect_enabled);
                } else {
                    edit->set_object_name(
                        iterator->object,
                        iterator->has_name
                            ? iterator->name.c_str()
                            : nullptr);
                }
            }
        };

        for (const TimelineCommand& command :
             *context.commands) {
            CapturedObject& object =
                timeline.objects[command.object_index];
            if (command.type ==
                TimelineCommandType::set_items) {
                for (const ObjectItemUpdate& update :
                     command.updates) {
                    if (!edit->set_object_item_value(
                            object.handle,
                            update.effect.c_str(),
                            update.item.c_str(),
                            update.value.c_str())) {
                        rollback_metadata();
                        context.result.error_code =
                            "TRANSACTION_APPLY_FAILED";
                        context.result.error_message =
                            "AviUtl2 rejected a transaction item value; metadata was restored.";
                        return;
                    }
                }
            } else if (
                command.type ==
                TimelineCommandType::set_effect_enabled) {
                const EFFECT_HANDLE effect = edit->find_effect(
                    object.handle,
                    command.effect_selector.c_str());
                edit->set_effect_enable(effect, command.enabled);
            } else if (
                command.type == TimelineCommandType::set_name) {
                const std::wstring& name =
                    command.name.value();
                edit->set_object_name(
                    object.handle,
                    name.empty() ? nullptr : name.c_str());
            }
        }

        int timeline_end = 0;
        for (const CapturedObject& object : timeline.objects) {
            timeline_end =
                (std::max)(timeline_end, object.frame_end);
        }
        if (timeline_end == std::numeric_limits<int>::max()) {
            rollback_metadata();
            context.result.error_code =
                "TRANSACTION_ROLLBACK";
            context.result.error_message =
                "The timeline is too long to reserve scratch placement.";
            return;
        }
        std::vector<std::size_t> moved_to_scratch;
        std::int64_t scratch =
            static_cast<std::int64_t>(timeline_end) + 1;
        for (std::size_t index = 0U;
             index < placements.size();
             ++index) {
            if (!has_move[index]) {
                continue;
            }
            const int length =
                timeline.objects[index].frame_end -
                timeline.objects[index].frame_start + 1;
            if (scratch >
                std::numeric_limits<int>::max() - length + 1 ||
                !edit->move_object(
                    timeline.objects[index].handle,
                    timeline.objects[index].layer,
                    static_cast<int>(scratch))) {
                for (auto iterator =
                         moved_to_scratch.rbegin();
                     iterator != moved_to_scratch.rend();
                     ++iterator) {
                    const CapturedObject& original =
                        timeline.objects[*iterator];
                    edit->move_object(
                        original.handle,
                        original.layer,
                        original.frame_start);
                }
                rollback_metadata();
                context.result.error_code =
                    "TRANSACTION_ROLLBACK";
                context.result.error_message =
                    "AviUtl2 rejected scratch placement; all prior edits were restored.";
                return;
            }
            moved_to_scratch.push_back(index);
            scratch += length;
        }

        std::vector<std::size_t> moved_final;
        for (const std::size_t index : moved_to_scratch) {
            if (!edit->move_object(
                    timeline.objects[index].handle,
                    placements[index].layer,
                    placements[index].start)) {
                for (auto iterator = moved_final.rbegin();
                     iterator != moved_final.rend();
                     ++iterator) {
                    const CapturedObject& original =
                        timeline.objects[*iterator];
                    edit->move_object(
                        original.handle,
                        original.layer,
                        original.frame_start);
                }
                for (const std::size_t pending :
                     moved_to_scratch) {
                    if (std::find(
                            moved_final.begin(),
                            moved_final.end(),
                            pending) == moved_final.end()) {
                        const CapturedObject& original =
                            timeline.objects[pending];
                        edit->move_object(
                            original.handle,
                            original.layer,
                            original.frame_start);
                    }
                }
                rollback_metadata();
                context.result.error_code =
                    "TRANSACTION_ROLLBACK";
                context.result.error_message =
                    "AviUtl2 rejected final placement; all prior edits were restored.";
                return;
            }
            moved_final.push_back(index);
        }
        for (std::size_t index = 0U;
             index < has_remove.size();
             ++index) {
            if (has_remove[index]) {
                edit->delete_object(
                    timeline.objects[index].handle);
            }
        }
        context.result.applied_count =
            context.commands->size();
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "TRANSACTION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "TRANSACTION_FAILED";
        context.result.error_message =
            "The timeline transaction failed inside the SDK callback.";
    }
}

struct MediaProbeContext final {
    const std::wstring* file = nullptr;
    MediaProbeResult result;
};

void media_probe_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<MediaProbeContext*>(parameter);
    if (context.file == nullptr || edit == nullptr ||
        edit->is_support_media_file == nullptr ||
        edit->get_media_info == nullptr) {
        context.result.error_code = "READ_SECTION_UNAVAILABLE";
        context.result.error_message =
            "The AviUtl2 media inspection API is unavailable.";
        context.result.retryable = true;
        return;
    }
    const wchar_t* const file = context.file->c_str();
    context.result.info.extension_supported =
        edit->is_support_media_file(file, false);
    context.result.info.readable =
        edit->is_support_media_file(file, true);
    MEDIA_INFO sdk_info{};
    if (context.result.info.readable &&
        edit->get_media_info(
            file,
            &sdk_info,
            static_cast<int>(sizeof(sdk_info)))) {
        context.result.info.has_info = true;
        context.result.info.video_track_count = sdk_info.video_track_num;
        context.result.info.audio_track_count = sdk_info.audio_track_num;
        context.result.info.duration_seconds = sdk_info.total_time;
        context.result.info.width = sdk_info.width;
        context.result.info.height = sdk_info.height;
    }
    context.result.ok = true;
}

struct CreateMediaContext final {
    const std::wstring* file = nullptr;
    int layer = 0;
    int frame = 0;
    int length = 0;
    CreateMediaResult result;
};

void create_media_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<CreateMediaContext*>(parameter);
    if (context.file == nullptr || edit == nullptr ||
        edit->is_support_media_file == nullptr ||
        edit->create_object_from_media_file == nullptr ||
        edit->get_object_layer_frame == nullptr) {
        context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
        context.result.error_message =
            "The AviUtl2 media object creation API is unavailable.";
        context.result.retryable = true;
        return;
    }
    if (edit->get_layer_lock != nullptr &&
        edit->get_layer_lock(context.layer)) {
        context.result.error_code = "LAYER_LOCKED";
        context.result.error_message =
            "The destination layer is locked in AviUtl2.";
        return;
    }
    if (!edit->is_support_media_file(context.file->c_str(), true)) {
        context.result.error_code = "UNSUPPORTED_MEDIA";
        context.result.error_message =
            "AviUtl2 cannot read the requested media file.";
        return;
    }
    const OBJECT_HANDLE created = edit->create_object_from_media_file(
        context.file->c_str(),
        context.layer,
        context.frame,
        context.length);
    if (created == nullptr) {
        context.result.error_code = "MEDIA_CREATE_FAILED";
        context.result.error_message =
            "AviUtl2 rejected the media file or its timeline placement.";
        return;
    }
    const OBJECT_LAYER_FRAME range =
        edit->get_object_layer_frame(created);
    if (range.layer < 0 || range.start < 0 || range.end < range.start) {
        context.result.error_code = "INVALID_HOST_OBJECT_RANGE";
        context.result.error_message =
            "AviUtl2 returned an invalid created object range.";
        return;
    }
    context.result.layer = range.layer;
    context.result.frame_start = range.start;
    context.result.frame_end = range.end;
    context.result.ok = true;
}

struct EnumeratedItem final {
    std::wstring name;
    int type = 0;
};

struct EnumItemsContext final {
    std::vector<EnumeratedItem> items;
    std::size_t* total_items = nullptr;
    bool failed = false;
};

void enum_item_callback(
    void* parameter,
    LPCWSTR name,
    const int type) noexcept {
    auto& context = *static_cast<EnumItemsContext*>(parameter);
    if (context.failed || name == nullptr ||
        context.total_items == nullptr) {
        context.failed = true;
        return;
    }
    try {
        constexpr std::size_t max_item_name_characters = 256U;
        const std::size_t length =
            wcsnlen_s(name, max_item_name_characters + 1U);
        if (length == 0U || length > max_item_name_characters ||
            *context.total_items >= kMaxInspectItems) {
            context.failed = true;
            return;
        }
        context.items.push_back(
            EnumeratedItem{std::wstring(name, length), type});
        ++*context.total_items;
    } catch (...) {
        context.failed = true;
    }
}

struct InspectContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    std::size_t object_index = 0U;
    int sample_frame = 0;
    ObjectInspectionResult result;
};

void inspect_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<InspectContext*>(parameter);
    try {
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the snapshot was captured.";
            return;
        }
        if (context.object_index >= timeline.objects.size()) {
            context.result.error_code = "OBJECT_NOT_FOUND";
            context.result.error_message =
                "The snapshot object does not exist.";
            return;
        }
        if (context.edit_handle == nullptr ||
            context.edit_handle->enum_effect_item == nullptr ||
            edit == nullptr || edit->get_effect_list == nullptr ||
            edit->get_effect_name == nullptr ||
            edit->get_object_item_value == nullptr) {
            context.result.error_code = "READ_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 structured object inspection API is unavailable.";
            context.result.retryable = true;
            return;
        }

        const CapturedObject& object =
            timeline.objects[context.object_index];
        const int sampled_frame =
            context.sample_frame >= 0
                ? context.sample_frame
                : object.frame_start;
        context.result.sampled_frame = sampled_frame;
        const int effect_count =
            edit->get_effect_list(object.handle, nullptr, 0);
        if (effect_count < 0 ||
            static_cast<std::size_t>(effect_count) > kMaxInspectEffects) {
            context.result.error_code = "INSPECTION_TOO_LARGE";
            context.result.error_message =
                "The object effect count exceeds the inspection limit.";
            return;
        }
        std::vector<EFFECT_HANDLE> handles(
            static_cast<std::size_t>(effect_count));
        if (effect_count > 0 &&
            edit->get_effect_list(
                object.handle,
                handles.data(),
                effect_count) != effect_count) {
            context.result.error_code = "HOST_INSPECTION_FAILED";
            context.result.error_message =
                "AviUtl2 could not return a stable effect list.";
            return;
        }

        std::vector<std::wstring> names;
        names.reserve(handles.size());
        std::unordered_map<std::wstring, int> totals;
        for (const EFFECT_HANDLE handle : handles) {
            const LPCWSTR sdk_name = edit->get_effect_name(handle);
            constexpr std::size_t max_effect_name_characters = 256U;
            if (sdk_name == nullptr) {
                context.result.error_code = "HOST_INSPECTION_FAILED";
                context.result.error_message =
                    "AviUtl2 returned an unnamed effect.";
                return;
            }
            const std::size_t length =
                wcsnlen_s(sdk_name, max_effect_name_characters + 1U);
            if (length == 0U || length > max_effect_name_characters) {
                context.result.error_code = "INSPECTION_TOO_LARGE";
                context.result.error_message =
                    "An effect name exceeds the inspection limit.";
                return;
            }
            names.emplace_back(sdk_name, length);
            ++totals[names.back()];
        }

        std::unordered_map<std::wstring, int> occurrences;
        std::size_t total_items = 0U;
        std::size_t total_value_bytes = 0U;
        context.result.effects.reserve(handles.size());
        for (std::size_t index = 0U; index < handles.size(); ++index) {
            const std::wstring& name = names[index];
            const int occurrence = occurrences[name]++;
            const bool duplicated = totals[name] > 1;
            const std::wstring selector =
                duplicated
                    ? name + L":" + std::to_wstring(occurrence)
                    : name;
            InspectedEffect output;
            output.index = static_cast<int>(index);
            output.occurrence = occurrence;
            output.name = wide_to_utf8(name);
            output.selector = wide_to_utf8(selector);
            output.enabled =
                edit->get_effect_enable == nullptr ||
                edit->get_effect_enable(handles[index]);
            output.locked =
                edit->get_effect_lock != nullptr &&
                edit->get_effect_lock(handles[index]);

            EnumItemsContext enum_context{{}, &total_items, false};
            if (!context.edit_handle->enum_effect_item(
                    name.c_str(),
                    &enum_context,
                    enum_item_callback) ||
                enum_context.failed) {
                context.result.error_code = "HOST_INSPECTION_FAILED";
                context.result.error_message =
                    "AviUtl2 could not enumerate effect items.";
                return;
            }
            output.items.reserve(enum_context.items.size());
            for (const EnumeratedItem& enumerated : enum_context.items) {
                InspectedItem item;
                item.name = wide_to_utf8(enumerated.name);
                item.type = enumerated.type;
                const LPCSTR sdk_value = edit->get_object_item_value(
                    object.handle,
                    selector.c_str(),
                    enumerated.name.c_str());
                if (sdk_value != nullptr) {
                    const std::size_t remaining =
                        kMaxInspectValueBytes - total_value_bytes;
                    const std::size_t length =
                        strnlen_s(sdk_value, remaining + 1U);
                    if (length > remaining) {
                        context.result.error_code = "INSPECTION_TOO_LARGE";
                        context.result.error_message =
                            "The object item data exceeds the inspection limit.";
                        return;
                    }
                    item.value.assign(sdk_value, length);
                    if (!is_valid_utf8(item.value)) {
                        context.result.error_code =
                            "HOST_INSPECTION_FAILED";
                        context.result.error_message =
                            "AviUtl2 returned an invalid UTF-8 item value.";
                        return;
                    }
                    item.has_value = true;
                    total_value_bytes += length;
                }

                if (edit->get_object_track_info != nullptr) {
                    TRACK_INFO sdk_track{};
                    if (edit->get_object_track_info(
                            object.handle,
                            selector.c_str(),
                            enumerated.name.c_str(),
                            &sdk_track,
                            static_cast<int>(sizeof(sdk_track)))) {
                        constexpr int max_track_parameters = 64;
                        if (sdk_track.param_num < 0 ||
                            sdk_track.param_num > max_track_parameters ||
                            (sdk_track.param_num > 0 &&
                             sdk_track.param == nullptr)) {
                            context.result.error_code =
                                "HOST_INSPECTION_FAILED";
                            context.result.error_message =
                                "AviUtl2 returned invalid track information.";
                            return;
                        }
                        item.track.available = true;
                        if (sdk_track.mode != nullptr) {
                            item.track.has_mode = true;
                            item.track.mode =
                                wide_to_utf8(sdk_track.mode);
                        }
                        if (sdk_track.param_num > 0) {
                            item.track.parameters.assign(
                                sdk_track.param,
                                sdk_track.param +
                                    static_cast<std::size_t>(
                                        sdk_track.param_num));
                        }
                        item.track.accelerate = sdk_track.accelerate;
                        item.track.decelerate = sdk_track.decelerate;
                        item.track.ignore_midpoints = sdk_track.twopoint;
                        item.track.time_control = sdk_track.timecontrol;
                        item.track.group_count = sdk_track.group_num;
                        item.track.group_index = sdk_track.group_index;
                        if (sdk_track.group_name != nullptr) {
                            item.track.has_group_name = true;
                            item.track.group_name =
                                wide_to_utf8(sdk_track.group_name);
                            if (edit->get_object_track_group_names !=
                                nullptr) {
                                const int group_item_count =
                                    edit->get_object_track_group_names(
                                        object.handle,
                                        selector.c_str(),
                                        sdk_track.group_name,
                                        nullptr,
                                        0);
                                if (group_item_count < 0 ||
                                    group_item_count > 512) {
                                    context.result.error_code =
                                        "HOST_INSPECTION_FAILED";
                                    context.result.error_message =
                                        "AviUtl2 returned invalid track group information.";
                                    return;
                                }
                                std::vector<LPCWSTR> group_names(
                                    static_cast<std::size_t>(
                                        group_item_count));
                                if (group_item_count > 0 &&
                                    edit->get_object_track_group_names(
                                        object.handle,
                                        selector.c_str(),
                                        sdk_track.group_name,
                                        group_names.data(),
                                        group_item_count) !=
                                        group_item_count) {
                                    context.result.error_code =
                                        "HOST_INSPECTION_FAILED";
                                    context.result.error_message =
                                        "AviUtl2 could not return a stable track group.";
                                    return;
                                }
                                item.track.group_items.reserve(
                                    group_names.size());
                                for (const LPCWSTR group_item :
                                     group_names) {
                                    if (group_item == nullptr) {
                                        context.result.error_code =
                                            "HOST_INSPECTION_FAILED";
                                        context.result.error_message =
                                            "AviUtl2 returned an unnamed track group item.";
                                        return;
                                    }
                                    item.track.group_items.push_back(
                                        wide_to_utf8(group_item));
                                }
                            }
                        }
                        if (edit->get_object_track_value != nullptr) {
                            double sampled = 0.0;
                            if (edit->get_object_track_value(
                                    object.handle,
                                    selector.c_str(),
                                    enumerated.name.c_str(),
                                    static_cast<double>(
                                        sampled_frame),
                                    &sampled)) {
                                item.track.has_sampled_value = true;
                                item.track.sampled_value = sampled;
                            }
                        }
                    }
                }
                if (enumerated.type == 3 &&
                    edit->get_object_check_value != nullptr) {
                    bool sampled = false;
                    if (edit->get_object_check_value(
                            object.handle,
                            selector.c_str(),
                            enumerated.name.c_str(),
                            sampled_frame,
                            &sampled)) {
                        item.has_sampled_check_value = true;
                        item.sampled_check_value = sampled;
                    }
                }
                output.items.push_back(std::move(item));
            }
            context.result.effects.push_back(std::move(output));
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "HOST_INSPECTION_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "HOST_INSPECTION_FAILED";
        context.result.error_message =
            "The object inspection failed inside the SDK callback.";
    }
}

struct RenderContext final {
    std::atomic_uint references{2U};
    std::mutex mutex;
    std::condition_variable completed;
    bool done = false;
    RenderedFrameResult result;
};

void release_render_context(RenderContext* context) noexcept {
    if (context != nullptr &&
        context->references.fetch_sub(
            1U,
            std::memory_order_acq_rel) == 1U) {
        delete context;
    }
}

void rendering_video_callback(
    void* parameter,
    const int frame,
    const void* buffer,
    const int width,
    const int height,
    const int pitch) noexcept {
    auto* const context =
        static_cast<RenderContext*>(parameter);
    if (context == nullptr) {
        return;
    }
    {
        std::scoped_lock lock(context->mutex);
        context->result.frame = frame;
        try {
            constexpr std::size_t bytes_per_pixel = 4U;
            if (buffer == nullptr || width <= 0 || height <= 0 ||
                width > kMaxRenderDimension ||
                height > kMaxRenderDimension ||
                pitch < width * static_cast<int>(bytes_per_pixel)) {
                context->result.error_code =
                    "INVALID_RENDER_BUFFER";
                context->result.error_message =
                    "AviUtl2 returned an invalid RGBA render buffer.";
            } else {
                const std::size_t row_bytes =
                    static_cast<std::size_t>(width) *
                    bytes_per_pixel;
                const std::size_t total_bytes =
                    row_bytes * static_cast<std::size_t>(height);
                if (total_bytes > kMaxRenderRgbaBytes) {
                    context->result.error_code =
                        "RENDER_BUFFER_TOO_LARGE";
                    context->result.error_message =
                        "The rendered RGBA frame exceeds the memory limit.";
                } else {
                    context->result.rgba.resize(total_bytes);
                    const auto* const source =
                        static_cast<const std::uint8_t*>(buffer);
                    for (int row = 0; row < height; ++row) {
                        std::memcpy(
                            context->result.rgba.data() +
                                static_cast<std::size_t>(row) *
                                    row_bytes,
                            source +
                                static_cast<std::size_t>(row) *
                                    static_cast<std::size_t>(pitch),
                            row_bytes);
                    }
                    context->result.width = width;
                    context->result.height = height;
                    context->result.ok = true;
                }
            }
        } catch (const std::exception& error) {
            context->result.error_code = "FRAME_RENDER_FAILED";
            context->result.error_message = error.what();
        } catch (...) {
            context->result.error_code = "FRAME_RENDER_FAILED";
            context->result.error_message =
                "The frame callback could not copy the render buffer.";
        }
        context->done = true;
    }
    context->completed.notify_all();
    release_render_context(context);
}

struct AudioRenderContext final {
    std::atomic_uint references{2U};
    std::mutex mutex;
    std::condition_variable completed;
    bool done = false;
    int requested_frame = 0;
    std::vector<float> interleaved_stereo;
    std::string error_code;
    std::string error_message;
};

void release_audio_render_context(
    AudioRenderContext* context) noexcept {
    if (context != nullptr &&
        context->references.fetch_sub(
            1U,
            std::memory_order_acq_rel) == 1U) {
        delete context;
    }
}

void rendering_audio_callback(
    void* parameter,
    const int frame,
    const float* buffer0,
    const float* buffer1,
    const int sample_num) noexcept {
    auto* const context =
        static_cast<AudioRenderContext*>(parameter);
    if (context == nullptr) {
        return;
    }
    {
        std::scoped_lock lock(context->mutex);
        try {
            if (frame != context->requested_frame ||
                buffer0 == nullptr || buffer1 == nullptr ||
                sample_num < 0) {
                context->error_code =
                    "INVALID_AUDIO_RENDER_BUFFER";
                context->error_message =
                    "AviUtl2 returned an invalid stereo audio buffer.";
            } else {
                const std::size_t samples =
                    static_cast<std::size_t>(sample_num);
                if (samples >
                    kMaxAudioCaptureBytes /
                        (sizeof(float) * 2U)) {
                    context->error_code =
                        "AUDIO_RENDER_BUFFER_TOO_LARGE";
                    context->error_message =
                        "The rendered audio frame exceeds the memory limit.";
                } else {
                    context->interleaved_stereo.resize(
                        samples * 2U);
                    for (std::size_t index = 0U;
                         index < samples;
                         ++index) {
                        context->interleaved_stereo[
                            index * 2U] = buffer0[index];
                        context->interleaved_stereo[
                            index * 2U + 1U] = buffer1[index];
                    }
                }
            }
        } catch (const std::exception& error) {
            context->error_code = "AUDIO_RENDER_FAILED";
            context->error_message = error.what();
        } catch (...) {
            context->error_code = "AUDIO_RENDER_FAILED";
            context->error_message =
                "The audio callback could not copy the render buffer.";
        }
        context->done = true;
    }
    context->completed.notify_all();
    release_audio_render_context(context);
}

struct BatchContext final {
    const std::vector<CreateAliasCommand>* commands = nullptr;
    BatchEditResult result;
};

[[nodiscard]] bool ranges_overlap(
    const CreateAliasCommand& left,
    const CreateAliasCommand& right) noexcept {
    if (left.layer != right.layer) {
        return false;
    }
    const int left_end = left.frame + left.length - 1;
    const int right_end = right.frame + right.length - 1;
    return left.frame <= right_end && right.frame <= left_end;
}

void set_collision(
    BatchContext& context,
    const std::size_t index,
    const int layer,
    const int start,
    const int end) noexcept {
    context.result.failed_command_index = index;
    context.result.error_code = "PLACEMENT_COLLISION";
    context.result.error_message =
        "The requested timeline range is already occupied.";
    context.result.has_collision = true;
    context.result.collision_layer = layer;
    context.result.collision_start = start;
    context.result.collision_end = end;
}

[[nodiscard]] bool preflight_batch(
    BatchContext& context,
    EDIT_SECTION* edit) noexcept {
    if (context.commands == nullptr || edit == nullptr ||
        edit->find_object == nullptr ||
        edit->get_object_layer_frame == nullptr) {
        context.result.error_code = "READ_SECTION_UNAVAILABLE";
        context.result.error_message =
            "The AviUtl2 timeline cannot be inspected.";
        context.result.retryable = true;
        return false;
    }

    const auto& commands = *context.commands;
    for (std::size_t index = 0U; index < commands.size(); ++index) {
        const CreateAliasCommand& command = commands[index];
        if (edit->get_layer_lock != nullptr &&
            edit->get_layer_lock(command.layer)) {
            context.result.failed_command_index = index;
            context.result.error_code = "LAYER_LOCKED";
            context.result.error_message =
                "The destination layer is locked in AviUtl2.";
            return false;
        }
        for (std::size_t prior = 0U; prior < index; ++prior) {
            if (ranges_overlap(command, commands[prior])) {
                const int overlap_start =
                    command.frame > commands[prior].frame
                        ? command.frame
                        : commands[prior].frame;
                const int command_end =
                    command.frame + command.length - 1;
                const int prior_end =
                    commands[prior].frame + commands[prior].length - 1;
                const int overlap_end =
                    command_end < prior_end ? command_end : prior_end;
                set_collision(
                    context,
                    index,
                    command.layer,
                    overlap_start,
                    overlap_end);
                return false;
            }
        }

        const int requested_end = command.frame + command.length - 1;
        int search_frame = 0;
        while (search_frame <= requested_end) {
            const OBJECT_HANDLE object =
                edit->find_object(command.layer, search_frame);
            if (object == nullptr) {
                break;
            }
            const OBJECT_LAYER_FRAME occupied =
                edit->get_object_layer_frame(object);
            if (occupied.start > requested_end) {
                break;
            }
            if (occupied.end >= command.frame) {
                const int overlap_start =
                    occupied.start > command.frame
                        ? occupied.start
                        : command.frame;
                const int overlap_end =
                    occupied.end < requested_end
                        ? occupied.end
                        : requested_end;
                set_collision(
                    context,
                    index,
                    command.layer,
                    overlap_start,
                    overlap_end);
                return false;
            }
            if (occupied.end < search_frame ||
                occupied.end == std::numeric_limits<int>::max()) {
                context.result.failed_command_index = index;
                context.result.error_code = "INVALID_HOST_OBJECT_RANGE";
                context.result.error_message =
                    "AviUtl2 returned an invalid timeline object range.";
                return false;
            }
            search_frame = occupied.end + 1;
        }
    }
    return true;
}

void validate_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<BatchContext*>(parameter);
    if (preflight_batch(context, edit)) {
        context.result.ok = true;
    }
}

void apply_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<BatchContext*>(parameter);
    if (!preflight_batch(context, edit)) {
        return;
    }
    if (edit->create_object_from_alias == nullptr ||
        context.commands == nullptr) {
        context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
        context.result.error_message =
            "The AviUtl2 object creation API is unavailable.";
        context.result.retryable = true;
        return;
    }

    const auto& commands = *context.commands;
    for (std::size_t index = 0U; index < commands.size(); ++index) {
        const CreateAliasCommand& command = commands[index];
        if (edit->create_object_from_alias(
                command.alias.c_str(),
                command.layer,
                command.frame,
                command.length) == nullptr) {
            context.result.failed_command_index = index;
            context.result.error_code =
                "ALIAS_INVALID_OR_PLACEMENT_COLLISION";
            context.result.error_message =
                "AviUtl2 rejected the object Alias or its expanded placement.";
            return;
        }
        ++context.result.applied_count;
    }
    context.result.ok = true;
}

struct EditPlanContext final {
    EDIT_HANDLE* edit_handle = nullptr;
    std::int64_t expected_revision = 0;
    const std::vector<EditPlanCommand>* commands = nullptr;
    bool apply = false;
    EditPlanResult result;
};

struct CreatedPlanObject final {
    OBJECT_HANDLE object = nullptr;
    bool media = false;
};

struct CreatedPlanEffect final {
    OBJECT_HANDLE object = nullptr;
    EFFECT_HANDLE effect = nullptr;
};

struct HeldPlanObject final {
    OBJECT_HANDLE object = nullptr;
    int layer = 0;
    int frame = 0;
};

void edit_plan_callback(void* parameter, EDIT_SECTION* edit) noexcept {
    auto& context = *static_cast<EditPlanContext*>(parameter);
    try {
        if (context.commands == nullptr || context.commands->empty() ||
            context.commands->size() > kMaxBatchCommands) {
            context.result.error_code = "INVALID_ARGUMENT";
            context.result.error_message =
                "An edit plan must contain between 1 and 128 commands.";
            return;
        }
        CapturedTimeline timeline;
        if (!capture_timeline(
                context.edit_handle,
                edit,
                timeline,
                context.result.error_code,
                context.result.error_message)) {
            return;
        }
        context.result.current_revision = timeline.revision;
        if (timeline.revision != context.expected_revision) {
            context.result.error_code = "STALE_PROJECT_STATE";
            context.result.error_message =
                "The AviUtl2 project changed after the edit plan was prepared.";
            return;
        }

        std::vector<CreateAliasCommand> create_placements;
        std::vector<std::size_t> create_plan_indices;
        std::vector<TimelineCommand> timeline_commands;
        std::vector<std::size_t> timeline_plan_indices;
        for (std::size_t index = 0U;
             index < context.commands->size();
             ++index) {
            const EditPlanCommand& command = (*context.commands)[index];
            context.result.failed_command_index = index;
            if (command.type == EditPlanCommandType::create_alias ||
                command.type == EditPlanCommandType::create_media) {
                if (command.layer < 0 || command.frame < 0 ||
                    command.length <= 0) {
                    context.result.error_code = "INVALID_ARGUMENT";
                    context.result.error_message =
                        "Plan creation placement is invalid.";
                    return;
                }
                create_placements.push_back(CreateAliasCommand{
                    command.key,
                    command.type == EditPlanCommandType::create_alias
                        ? command.alias
                        : std::string("[Object]\neffect.name=Media"),
                    command.layer,
                    command.frame,
                    command.length,
                });
                create_plan_indices.push_back(index);
                if (command.type == EditPlanCommandType::create_alias) {
                    if (command.alias.empty() ||
                        edit->create_object_from_alias == nullptr) {
                        context.result.error_code =
                            "EDIT_SECTION_UNAVAILABLE";
                        context.result.error_message =
                            "Alias creation is unavailable for this edit plan.";
                        return;
                    }
                } else {
                    if (command.file.empty() ||
                        edit->is_support_media_file == nullptr ||
                        edit->create_object_from_media_file == nullptr ||
                        !edit->is_support_media_file(
                            command.file.c_str(),
                            true)) {
                        context.result.error_code = "UNSUPPORTED_MEDIA";
                        context.result.error_message =
                            "AviUtl2 cannot read a planned media file.";
                        return;
                    }
                    if (!command.updates.empty() &&
                        (edit->get_object_item_value == nullptr ||
                         edit->set_object_item_value == nullptr)) {
                        context.result.error_code =
                            "EDIT_SECTION_UNAVAILABLE";
                        context.result.error_message =
                            "Initial media item editing is unavailable.";
                        return;
                    }
                }
                if (!command.effects.empty() &&
                    (edit->create_effect == nullptr ||
                     edit->delete_effect == nullptr ||
                     edit->get_effect_list == nullptr ||
                     edit->get_effect_name == nullptr ||
                     edit->get_effect_item_value == nullptr ||
                     edit->set_effect_item_value == nullptr ||
                     edit->get_effect_enable == nullptr ||
                     edit->set_effect_enable == nullptr)) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "Create-time effect editing is unavailable.";
                    return;
                }
                continue;
            }
            if (command.object_index >= timeline.objects.size()) {
                context.result.error_code = "OBJECT_NOT_FOUND";
                context.result.error_message =
                    "An edit-plan target does not exist.";
                return;
            }
            CapturedObject& object =
                timeline.objects[command.object_index];
            if (object.api_locked) {
                context.result.error_code = "OBJECT_API_LOCKED";
                context.result.error_message =
                    "An edit-plan target is API locked.";
                return;
            }
            if (edit->get_layer_lock != nullptr &&
                edit->get_layer_lock(object.layer)) {
                context.result.error_code = "LAYER_LOCKED";
                context.result.error_message =
                    "An edit-plan target layer is locked.";
                return;
            }
            if (command.type == EditPlanCommandType::update) {
                if (!command.updates.empty()) {
                    timeline_commands.push_back(TimelineCommand{
                        TimelineCommandType::set_items,
                        command.object_index,
                        0,
                        0,
                        command.updates,
                    });
                    timeline_plan_indices.push_back(index);
                }
                if (command.name.has_value()) {
                    TimelineCommand item;
                    item.type = TimelineCommandType::set_name;
                    item.object_index = command.object_index;
                    item.name = command.name;
                    timeline_commands.push_back(std::move(item));
                    timeline_plan_indices.push_back(index);
                }
                if (command.updates.empty() &&
                    !command.name.has_value()) {
                    context.result.error_code = "INVALID_ARGUMENT";
                    context.result.error_message =
                        "An object update must change at least one value.";
                    return;
                }
            } else if (command.type == EditPlanCommandType::move) {
                TimelineCommand item;
                item.type = TimelineCommandType::move;
                item.object_index = command.object_index;
                item.layer = command.layer;
                item.frame = command.frame;
                timeline_commands.push_back(std::move(item));
                timeline_plan_indices.push_back(index);
            } else if (command.type == EditPlanCommandType::remove) {
                TimelineCommand item;
                item.type = TimelineCommandType::remove;
                item.object_index = command.object_index;
                timeline_commands.push_back(std::move(item));
                timeline_plan_indices.push_back(index);
            } else if (
                command.type ==
                EditPlanCommandType::set_effect_enabled) {
                TimelineCommand item;
                item.type = TimelineCommandType::set_effect_enabled;
                item.object_index = command.object_index;
                item.effect_selector = command.effect;
                item.enabled = command.enabled;
                timeline_commands.push_back(std::move(item));
                timeline_plan_indices.push_back(index);
            } else if (command.type == EditPlanCommandType::add_effect) {
                if (command.effect.empty() ||
                    edit->create_effect == nullptr ||
                    edit->delete_effect == nullptr ||
                    edit->get_effect_list == nullptr ||
                    edit->get_effect_name == nullptr ||
                    edit->get_effect_enable == nullptr ||
                    edit->set_effect_enable == nullptr ||
                    (!command.effect_items.empty() &&
                     (edit->get_effect_item_value == nullptr ||
                      edit->set_effect_item_value == nullptr))) {
                    context.result.error_code =
                        "EDIT_SECTION_UNAVAILABLE";
                    context.result.error_message =
                        "Effect creation is unavailable for this edit plan.";
                    return;
                }
            } else {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "The edit plan contains an unsupported command.";
                return;
            }
        }

        if (!timeline_commands.empty()) {
            TimelineTransactionContext validation{
                context.edit_handle,
                context.expected_revision,
                &timeline_commands,
                false,
                {},
            };
            timeline_transaction_callback(&validation, edit);
            if (!validation.result.ok) {
                context.result.error_code =
                    validation.result.error_code;
                context.result.error_message =
                    validation.result.error_message;
                context.result.retryable =
                    validation.result.retryable;
                if (validation.result.failed_command_index <
                    timeline_plan_indices.size()) {
                    context.result.failed_command_index =
                        timeline_plan_indices[
                            validation.result.failed_command_index];
                }
                return;
            }
        }

        struct FinalPlacement final {
            int layer = 0;
            int start = 0;
            int end = 0;
            bool removed = false;
        };
        std::vector<FinalPlacement> final_placements;
        final_placements.reserve(timeline.objects.size());
        for (const CapturedObject& object : timeline.objects) {
            final_placements.push_back(FinalPlacement{
                object.layer,
                object.frame_start,
                object.frame_end,
                false,
            });
        }
        for (const EditPlanCommand& command : *context.commands) {
            if (command.type == EditPlanCommandType::move) {
                FinalPlacement& placement =
                    final_placements[command.object_index];
                const int length =
                    timeline.objects[command.object_index].frame_end -
                    timeline.objects[command.object_index].frame_start + 1;
                placement.layer = command.layer;
                placement.start = command.frame;
                placement.end = command.frame + length - 1;
            } else if (command.type == EditPlanCommandType::remove) {
                final_placements[command.object_index].removed = true;
            }
        }
        for (std::size_t create_index = 0U;
             create_index < create_placements.size();
             ++create_index) {
            const CreateAliasCommand& creation =
                create_placements[create_index];
            context.result.failed_command_index =
                create_plan_indices[create_index];
            if (creation.frame >
                std::numeric_limits<int>::max() - creation.length + 1) {
                context.result.error_code = "INVALID_ARGUMENT";
                context.result.error_message =
                    "A planned creation exceeds the timeline range.";
                return;
            }
            if (edit->get_layer_lock != nullptr &&
                edit->get_layer_lock(creation.layer)) {
                context.result.error_code = "LAYER_LOCKED";
                context.result.error_message =
                    "A planned creation destination layer is locked.";
                return;
            }
            const int creation_end =
                creation.frame + creation.length - 1;
            for (std::size_t prior = 0U;
                 prior < create_index;
                 ++prior) {
                if (ranges_overlap(creation, create_placements[prior])) {
                    context.result.error_code = "PLACEMENT_COLLISION";
                    context.result.error_message =
                        "Planned creations overlap each other.";
                    return;
                }
            }
            for (const FinalPlacement& placement : final_placements) {
                if (!placement.removed &&
                    placement.layer == creation.layer &&
                    placement.start <= creation_end &&
                    creation.frame <= placement.end) {
                    context.result.error_code = "PLACEMENT_COLLISION";
                    context.result.error_message =
                        "A planned creation overlaps the final timeline layout.";
                    return;
                }
            }
        }
        context.result.valid = true;
        context.result.failed_command_index =
            std::numeric_limits<std::size_t>::max();
        if (!context.apply) {
            context.result.ok = true;
            return;
        }

        std::vector<CreatedPlanObject> created_objects;
        std::vector<CreatedPlanEffect> created_effects;
        std::vector<HeldPlanObject> held_objects;
        const auto rollback = [&]() noexcept {
            context.result.rollback_attempted =
                !created_objects.empty() || !created_effects.empty() ||
                !held_objects.empty();
            bool complete = true;
            for (auto iterator = created_effects.rbegin();
                 iterator != created_effects.rend();
                 ++iterator) {
                if (!edit->delete_effect(
                        iterator->object,
                        iterator->effect)) {
                    complete = false;
                } else {
                    ++context.result.restored_count;
                }
            }
            for (auto iterator = created_objects.rbegin();
                 iterator != created_objects.rend();
                 ++iterator) {
                if (iterator->object == nullptr ||
                    edit->delete_object == nullptr) {
                    complete = false;
                } else {
                    edit->delete_object(iterator->object);
                    ++context.result.restored_count;
                }
                if (iterator->media) {
                    complete = false;
                }
            }
            for (auto iterator = held_objects.rbegin();
                 iterator != held_objects.rend();
                 ++iterator) {
                if (edit->move_object == nullptr ||
                    !edit->move_object(
                        iterator->object,
                        iterator->layer,
                        iterator->frame)) {
                    complete = false;
                } else {
                    ++context.result.restored_count;
                }
            }
            context.result.rollback_complete = complete;
            context.result.gui_undo_required = !complete;
        };
        const auto apply_effect_to_object = [&context, &created_effects, edit](
            const OBJECT_HANDLE object,
            const std::wstring& effect_name,
            const std::vector<EffectInitialItem>& items,
            const bool enabled) -> bool {
            const EFFECT_HANDLE effect = edit->create_effect(
                object,
                effect_name.c_str());
            if (effect == nullptr) {
                context.result.error_code = "PLAN_APPLY_FAILED";
                context.result.error_message =
                    "AviUtl2 rejected a planned effect.";
                return false;
            }
            created_effects.push_back({object, effect});
            const LPCWSTR actual_name = edit->get_effect_name(effect);
            if (actual_name == nullptr || actual_name != effect_name) {
                context.result.error_code = "EFFECT_READBACK_MISMATCH";
                context.result.error_message =
                    "AviUtl2 created a different effect than requested.";
                return false;
            }
            for (const EffectInitialItem& item : items) {
                if (edit->get_effect_item_value(
                        effect,
                        item.item.c_str()) == nullptr ||
                    !edit->set_effect_item_value(
                        effect,
                        item.item.c_str(),
                        item.value.c_str())) {
                    context.result.error_code = "PLAN_APPLY_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected a planned effect item value.";
                    return false;
                }
                const LPCSTR actual = edit->get_effect_item_value(
                    effect,
                    item.item.c_str());
                if (actual == nullptr ||
                    !equivalent_alias_value(item.value, actual)) {
                    context.result.error_code =
                        "EFFECT_READBACK_MISMATCH";
                    context.result.error_message =
                        "AviUtl2 normalized or ignored a planned effect item.";
                    return false;
                }
            }
            edit->set_effect_enable(effect, enabled);
            if (edit->get_effect_enable(effect) != enabled) {
                context.result.error_code = "EFFECT_READBACK_MISMATCH";
                context.result.error_message =
                    "AviUtl2 rejected the planned effect enabled state.";
                return false;
            }
            const int effect_count =
                edit->get_effect_list(object, nullptr, 0);
            if (effect_count <= 0 ||
                static_cast<std::size_t>(effect_count) >
                    kMaxInspectEffects) {
                context.result.error_code =
                    "EFFECT_READBACK_MISMATCH";
                context.result.error_message =
                    "AviUtl2 returned an invalid post-create effect list.";
                return false;
            }
            std::vector<EFFECT_HANDLE> actual_effects(
                static_cast<std::size_t>(effect_count));
            if (edit->get_effect_list(
                    object,
                    actual_effects.data(),
                    effect_count) != effect_count) {
                context.result.error_code =
                    "EFFECT_READBACK_MISMATCH";
                context.result.error_message =
                    "AviUtl2 returned an unstable post-create effect list.";
                return false;
            }
            const auto current = std::find(
                actual_effects.begin(),
                actual_effects.end(),
                effect);
            if (current == actual_effects.end()) {
                context.result.error_code =
                    "EFFECT_READBACK_MISMATCH";
                context.result.error_message =
                    "The created effect is absent from the object effect list.";
                return false;
            }
            for (const CreatedPlanEffect& previous : created_effects) {
                if (previous.object != object || previous.effect == effect) {
                    continue;
                }
                const auto previous_position = std::find(
                    actual_effects.begin(),
                    actual_effects.end(),
                    previous.effect);
                if (previous_position == actual_effects.end() ||
                    previous_position >= current) {
                    context.result.error_code =
                        "EFFECT_READBACK_MISMATCH";
                    context.result.error_message =
                        "AviUtl2 changed the requested effect stack order.";
                    return false;
                }
            }
            return true;
        };

        std::set<OBJECT_HANDLE> known_objects;
        for (const CapturedObject& object : timeline.objects) {
            known_objects.insert(object.handle);
        }
        const auto capture_media_group =
            [&context, &known_objects, edit](
                const OBJECT_HANDLE primary,
                std::vector<OBJECT_HANDLE>& group) -> bool {
            CapturedTimeline current;
            if (!capture_timeline(
                    context.edit_handle,
                    edit,
                    current,
                    context.result.error_code,
                    context.result.error_message)) {
                return false;
            }
            for (const CapturedObject& candidate : current.objects) {
                if (known_objects.insert(candidate.handle).second) {
                    group.push_back(candidate.handle);
                }
            }
            if (std::find(group.begin(), group.end(), primary) ==
                group.end()) {
                context.result.error_code =
                    "MEDIA_GROUP_VERIFICATION_FAILED";
                context.result.error_message =
                    "The primary media object is absent from the created object group.";
                return false;
            }
            return true;
        };
        const auto media_effect_target = [&context, edit](
            const OBJECT_HANDLE primary,
            const std::vector<OBJECT_HANDLE>& group,
            const EditPlanEffectScope scope) -> OBJECT_HANDLE {
            if (scope == EditPlanEffectScope::primary) {
                return primary;
            }
            std::vector<OBJECT_HANDLE> video_matches;
            std::vector<OBJECT_HANDLE> dedicated_audio_matches;
            std::vector<OBJECT_HANDLE> combined_audio_matches;
            for (const OBJECT_HANDLE candidate : group) {
                const int effect_count =
                    edit->get_effect_list(candidate, nullptr, 0);
                if (effect_count <= 0 ||
                    static_cast<std::size_t>(effect_count) >
                        kMaxInspectEffects) {
                    context.result.error_code =
                        "MEDIA_EFFECT_ROUTE_FAILED";
                    context.result.error_message =
                        "A created media object has no stable effect list for domain routing.";
                    return nullptr;
                }
                std::vector<EFFECT_HANDLE> effects(
                    static_cast<std::size_t>(effect_count));
                if (edit->get_effect_list(
                        candidate,
                        effects.data(),
                        effect_count) != effect_count) {
                    context.result.error_code =
                        "MEDIA_EFFECT_ROUTE_FAILED";
                    context.result.error_message =
                        "A created media object returned an unstable effect list for domain routing.";
                    return nullptr;
                }
                bool video = false;
                bool dedicated_audio = false;
                bool combined_audio = false;
                for (const EFFECT_HANDLE effect : effects) {
                    const LPCWSTR name = edit->get_effect_name(effect);
                    if (name == nullptr) {
                        context.result.error_code =
                            "MEDIA_EFFECT_ROUTE_FAILED";
                        context.result.error_message =
                            "A created media object contains an unnamed effect.";
                        return nullptr;
                    }
                    dedicated_audio = dedicated_audio ||
                        std::wcscmp(name, L"音声ファイル") == 0 ||
                        std::wcscmp(name, L"音声再生") == 0;
                    combined_audio = combined_audio ||
                        std::wcscmp(name, L"映像再生") == 0;
                    video = video ||
                        std::wcscmp(name, L"動画ファイル") == 0 ||
                        std::wcscmp(name, L"画像ファイル") == 0 ||
                        std::wcscmp(name, L"標準描画") == 0 ||
                        std::wcscmp(name, L"映像再生") == 0;
                }
                if (video) {
                    video_matches.push_back(candidate);
                }
                if (dedicated_audio) {
                    dedicated_audio_matches.push_back(candidate);
                } else if (combined_audio) {
                    combined_audio_matches.push_back(candidate);
                }
            }
            const auto unique = [](const std::vector<OBJECT_HANDLE>& matches) {
                return matches.size() == 1U ? matches.front() : nullptr;
            };
            if (scope == EditPlanEffectScope::video) {
                const OBJECT_HANDLE match = unique(video_matches);
                if (match != nullptr) {
                    return match;
                }
            } else if (scope == EditPlanEffectScope::audio) {
                const OBJECT_HANDLE dedicated = unique(dedicated_audio_matches);
                if (dedicated != nullptr) {
                    return dedicated;
                }
                if (dedicated_audio_matches.empty()) {
                    const OBJECT_HANDLE combined = unique(combined_audio_matches);
                    if (combined != nullptr) {
                        return combined;
                    }
                }
            }
            context.result.error_code =
                "MEDIA_EFFECT_ROUTE_FAILED";
            context.result.error_message =
                "The requested Effect domain did not resolve to one dedicated or combined media object.";
            return nullptr;
        };

        if (!timeline_commands.empty()) {
            if (edit->move_object == nullptr) {
                context.result.error_code = "EDIT_SECTION_UNAVAILABLE";
                context.result.error_message =
                    "Scratch placement is unavailable for this mixed edit plan.";
                return;
            }
            std::int64_t scratch = 0;
            for (const CapturedObject& object : timeline.objects) {
                scratch = (std::max)(
                    scratch,
                    static_cast<std::int64_t>(object.frame_end) + 1);
            }
            for (const CreateAliasCommand& creation : create_placements) {
                scratch = (std::max)(
                    scratch,
                    static_cast<std::int64_t>(creation.frame) +
                        creation.length);
            }
            std::set<std::size_t> held_indices;
            for (std::size_t index = 0U;
                 index < context.commands->size();
                 ++index) {
                const EditPlanCommand& command = (*context.commands)[index];
                if ((command.type != EditPlanCommandType::move &&
                     command.type != EditPlanCommandType::remove) ||
                    !held_indices.insert(command.object_index).second) {
                    continue;
                }
                const CapturedObject& object =
                    timeline.objects[command.object_index];
                const int length =
                    object.frame_end - object.frame_start + 1;
                context.result.failed_command_index = index;
                if (scratch >
                        std::numeric_limits<int>::max() - length + 1 ||
                    !edit->move_object(
                        object.handle,
                        object.layer,
                        static_cast<int>(scratch))) {
                    context.result.error_code = "PLAN_SCRATCH_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected scratch placement for a plan target.";
                    rollback();
                    return;
                }
                held_objects.push_back(HeldPlanObject{
                    object.handle,
                    object.layer,
                    object.frame_start,
                });
                scratch += length;
            }
        }

        for (std::size_t index = 0U;
             index < context.commands->size();
             ++index) {
            const EditPlanCommand& command = (*context.commands)[index];
            context.result.failed_command_index = index;
            if (command.type == EditPlanCommandType::create_alias) {
                const OBJECT_HANDLE object =
                    edit->create_object_from_alias(
                        command.alias.c_str(),
                        command.layer,
                        command.frame,
                        command.length);
                if (object == nullptr) {
                    context.result.error_code = "PLAN_APPLY_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected a planned Alias object.";
                    rollback();
                    return;
                }
                created_objects.push_back({object, false});
                known_objects.insert(object);
                for (const EditPlanEffect& planned : command.effects) {
                    if (!apply_effect_to_object(
                            object,
                            planned.effect,
                            planned.items,
                            planned.enabled)) {
                        rollback();
                        return;
                    }
                }
            } else if (
                command.type == EditPlanCommandType::create_media) {
                const OBJECT_HANDLE object =
                    edit->create_object_from_media_file(
                        command.file.c_str(),
                        command.layer,
                        command.frame,
                        command.length);
                if (object == nullptr) {
                    context.result.error_code = "PLAN_APPLY_FAILED";
                    context.result.error_message =
                        "AviUtl2 rejected a planned media object.";
                    rollback();
                    return;
                }
                created_objects.push_back({object, true});
                std::vector<OBJECT_HANDLE> media_group;
                if (!capture_media_group(object, media_group)) {
                    rollback();
                    return;
                }
                for (const ObjectItemUpdate& update :
                     command.updates) {
                    std::vector<OBJECT_HANDLE> item_targets;
                    for (const OBJECT_HANDLE candidate : media_group) {
                        if (edit->get_object_item_value(
                                candidate,
                                update.effect.c_str(),
                                update.item.c_str()) != nullptr) {
                            item_targets.push_back(candidate);
                        }
                    }
                    if (item_targets.size() != 1U) {
                        context.result.error_code =
                            "MEDIA_ITEM_ROUTE_FAILED";
                        context.result.error_message =
                            "A planned media item did not resolve to exactly one created object.";
                        rollback();
                        return;
                    }
                    if (!edit->set_object_item_value(
                            item_targets.front(),
                            update.effect.c_str(),
                            update.item.c_str(),
                            update.value.c_str())) {
                        context.result.error_code =
                            "PLAN_APPLY_FAILED";
                        context.result.error_message =
                            "AviUtl2 rejected a planned media item value.";
                        rollback();
                        return;
                    }
                    const LPCSTR actual = edit->get_object_item_value(
                        item_targets.front(),
                        update.effect.c_str(),
                        update.item.c_str());
                    if (actual == nullptr ||
                        !equivalent_alias_value(update.value, actual)) {
                        context.result.error_code =
                            "MEDIA_ITEM_READBACK_MISMATCH";
                        context.result.error_message =
                            "AviUtl2 normalized or ignored a planned media item value.";
                        rollback();
                        return;
                    }
                }
                for (const EditPlanEffect& planned : command.effects) {
                    const OBJECT_HANDLE target = media_effect_target(
                        object,
                        media_group,
                        planned.scope);
                    if (target == nullptr) {
                        rollback();
                        return;
                    }
                    if (!apply_effect_to_object(
                            target,
                            planned.effect,
                            planned.items,
                            planned.enabled)) {
                        rollback();
                        return;
                    }
                }
            } else if (command.type == EditPlanCommandType::add_effect) {
                CapturedObject& object =
                    timeline.objects[command.object_index];
                if (!apply_effect_to_object(
                        object.handle,
                        command.effect,
                        command.effect_items,
                        command.enabled)) {
                    rollback();
                    return;
                }
            }
        }

        if (!timeline_commands.empty()) {
            CapturedTimeline after_creates;
            std::string capture_error;
            std::string capture_message;
            if (!capture_timeline(
                    context.edit_handle,
                    edit,
                    after_creates,
                    capture_error,
                    capture_message)) {
                context.result.error_code = std::move(capture_error);
                context.result.error_message = std::move(capture_message);
                rollback();
                return;
            }
            std::vector<TimelineCommand> remapped_commands =
                timeline_commands;
            for (TimelineCommand& command : remapped_commands) {
                const OBJECT_HANDLE target =
                    timeline.objects[command.object_index].handle;
                const auto found = std::find_if(
                    after_creates.objects.begin(),
                    after_creates.objects.end(),
                    [&](const CapturedObject& candidate) {
                        return candidate.handle == target;
                    });
                if (found == after_creates.objects.end()) {
                    context.result.error_code = "OBJECT_NOT_FOUND";
                    context.result.error_message =
                        "A plan target disappeared before final mutation.";
                    rollback();
                    return;
                }
                command.object_index = static_cast<std::size_t>(
                    std::distance(after_creates.objects.begin(), found));
            }
            TimelineTransactionContext mutation{
                context.edit_handle,
                after_creates.revision,
                &remapped_commands,
                true,
                {},
            };
            timeline_transaction_callback(&mutation, edit);
            if (!mutation.result.ok) {
                context.result.error_code = mutation.result.error_code;
                context.result.error_message =
                    mutation.result.error_message;
                context.result.retryable = mutation.result.retryable;
                if (mutation.result.failed_command_index <
                    timeline_plan_indices.size()) {
                    context.result.failed_command_index =
                        timeline_plan_indices[
                            mutation.result.failed_command_index];
                }
                rollback();
                return;
            }
        }
        context.result.applied_count = context.commands->size();
        context.result.failed_command_index =
            std::numeric_limits<std::size_t>::max();
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "EDIT_PLAN_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "EDIT_PLAN_FAILED";
        context.result.error_message =
            "The edit plan failed inside the SDK callback.";
    }
}

struct StringCatalogContext final {
    StringCatalogResult result;
    std::size_t limit = 0U;
};

void font_name_callback(
    void* parameter,
    const LPCWSTR name) noexcept {
    auto& context =
        *static_cast<StringCatalogContext*>(parameter);
    if (!context.result.error_code.empty()) {
        return;
    }
    try {
        if (name == nullptr ||
            context.result.values.size() >= context.limit) {
            context.result.error_code = "CATALOG_TOO_LARGE";
            context.result.error_message =
                "The font catalog exceeds the safety limit.";
            return;
        }
        constexpr std::size_t max_characters = 4096U;
        const std::size_t length =
            wcsnlen_s(name, max_characters + 1U);
        if (length == 0U || length > max_characters) {
            context.result.error_code = "INVALID_CATALOG_ENTRY";
            context.result.error_message =
                "AviUtl2 returned an invalid font name.";
            return;
        }
        context.result.values.push_back(
            wide_to_utf8(std::wstring_view(name, length)));
    } catch (const std::exception& error) {
        context.result.error_code = "FONT_CATALOG_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "FONT_CATALOG_FAILED";
        context.result.error_message =
            "The font catalog callback failed.";
    }
}

struct ModuleCatalogContext final {
    ModuleCatalogResult result;
    std::size_t limit = 0U;
};

[[nodiscard]] std::string_view module_type_name(
    const int type) noexcept {
    switch (type) {
        case MODULE_INFO::TYPE_SCRIPT_FILTER:
            return "script_filter";
        case MODULE_INFO::TYPE_SCRIPT_OBJECT:
            return "script_object";
        case MODULE_INFO::TYPE_SCRIPT_CAMERA:
            return "script_camera";
        case MODULE_INFO::TYPE_SCRIPT_TRACK:
            return "script_track";
        case MODULE_INFO::TYPE_SCRIPT_MODULE:
            return "script_module";
        case MODULE_INFO::TYPE_PLUGIN_INPUT:
            return "plugin_input";
        case MODULE_INFO::TYPE_PLUGIN_OUTPUT:
            return "plugin_output";
        case MODULE_INFO::TYPE_PLUGIN_FILTER:
            return "plugin_filter";
        case MODULE_INFO::TYPE_PLUGIN_COMMON:
            return "plugin_common";
        default:
            return "unknown";
    }
}

void module_info_callback(
    void* parameter,
    MODULE_INFO* info) noexcept {
    auto& context =
        *static_cast<ModuleCatalogContext*>(parameter);
    if (!context.result.error_code.empty()) {
        return;
    }
    try {
        if (info == nullptr || info->name == nullptr ||
            context.result.modules.size() >= context.limit) {
            context.result.error_code = "CATALOG_TOO_LARGE";
            context.result.error_message =
                "The module catalog exceeds the safety limit.";
            return;
        }
        constexpr std::size_t max_characters = 16384U;
        const std::size_t name_length =
            wcsnlen_s(info->name, max_characters + 1U);
        if (name_length == 0U ||
            name_length > max_characters) {
            context.result.error_code = "INVALID_CATALOG_ENTRY";
            context.result.error_message =
                "AviUtl2 returned an invalid module name.";
            return;
        }
        std::string information;
        if (info->information != nullptr) {
            const std::size_t information_length = wcsnlen_s(
                info->information,
                max_characters + 1U);
            if (information_length > max_characters) {
                context.result.error_code =
                    "INVALID_CATALOG_ENTRY";
                context.result.error_message =
                    "AviUtl2 returned oversized module information.";
                return;
            }
            information = wide_to_utf8(std::wstring_view(
                info->information,
                information_length));
        }
        context.result.modules.push_back(ModuleCatalogEntry{
            info->type,
            std::string(module_type_name(info->type)),
            wide_to_utf8(std::wstring_view(
                info->name,
                name_length)),
            std::move(information),
        });
    } catch (const std::exception& error) {
        context.result.error_code = "MODULE_CATALOG_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "MODULE_CATALOG_FAILED";
        context.result.error_message =
            "The module catalog callback failed.";
    }
}

struct PaletteNamesContext final {
    std::vector<std::wstring> names;
    std::string error_code;
    std::string error_message;
};

void palette_name_callback(
    void* parameter,
    const LPCWSTR name) noexcept {
    auto& context =
        *static_cast<PaletteNamesContext*>(parameter);
    if (!context.error_code.empty()) {
        return;
    }
    try {
        constexpr std::size_t max_palettes = 256U;
        constexpr std::size_t max_characters = 4096U;
        if (name == nullptr ||
            context.names.size() >= max_palettes) {
            context.error_code = "CATALOG_TOO_LARGE";
            context.error_message =
                "The palette catalog exceeds the safety limit.";
            return;
        }
        const std::size_t length =
            wcsnlen_s(name, max_characters + 1U);
        if (length == 0U || length > max_characters) {
            context.error_code = "INVALID_CATALOG_ENTRY";
            context.error_message =
                "AviUtl2 returned an invalid palette name.";
            return;
        }
        context.names.emplace_back(name, length);
    } catch (const std::exception& error) {
        context.error_code = "PALETTE_CATALOG_FAILED";
        context.error_message = error.what();
    } catch (...) {
        context.error_code = "PALETTE_CATALOG_FAILED";
        context.error_message =
            "The palette catalog callback failed.";
    }
}

struct PaletteDetailsContext final {
    const std::vector<std::wstring>* names = nullptr;
    PaletteCatalogResult result;
};

void palette_details_callback(
    void* parameter,
    EDIT_SECTION* edit) noexcept {
    auto& context =
        *static_cast<PaletteDetailsContext*>(parameter);
    try {
        if (context.names == nullptr || edit == nullptr ||
            edit->get_palette_info == nullptr) {
            context.result.error_code =
                "READ_SECTION_UNAVAILABLE";
            context.result.error_message =
                "The AviUtl2 palette inspection API is unavailable.";
            context.result.retryable = true;
            return;
        }
        context.result.palettes.reserve(context.names->size());
        for (const std::wstring& name : *context.names) {
            PALETTE_INFO info{};
            if (!edit->get_palette_info(
                    name.c_str(),
                    &info,
                    static_cast<int>(sizeof(info)))) {
                context.result.error_code =
                    "PALETTE_CATALOG_FAILED";
                context.result.error_message =
                    "AviUtl2 could not inspect a palette.";
                context.result.palettes.clear();
                return;
            }
            PaletteCatalogEntry entry;
            entry.name = wide_to_utf8(name);
            entry.colors_rgba.reserve(PALETTE_INFO::PALETTE_NUM);
            for (const auto& color : info.color) {
                entry.colors_rgba.push_back(
                    (static_cast<std::uint32_t>(color.r) << 24U) |
                    (static_cast<std::uint32_t>(color.g) << 16U) |
                    (static_cast<std::uint32_t>(color.b) << 8U) |
                    static_cast<std::uint32_t>(color.a));
            }
            context.result.palettes.push_back(std::move(entry));
        }
        context.result.ok = true;
    } catch (const std::exception& error) {
        context.result.error_code = "PALETTE_CATALOG_FAILED";
        context.result.error_message = error.what();
    } catch (...) {
        context.result.error_code = "PALETTE_CATALOG_FAILED";
        context.result.error_message =
            "The palette catalog callback failed.";
    }
}

[[nodiscard]] BatchEditResult unavailable_result(
    const std::string_view code,
    const std::string_view message,
    const bool retryable) {
    BatchEditResult result;
    result.error_code = std::string(code);
    result.error_message = std::string(message);
    result.retryable = retryable;
    return result;
}

}  // namespace

HostSdkAdapter::HostSdkAdapter(EDIT_HANDLE* edit_handle) noexcept
    : edit_handle_(edit_handle) {}

void HostSdkAdapter::observe_project_file_path(
    const std::wstring_view path) noexcept {
    try {
        std::scoped_lock lock(project_path_mutex_);
        project_file_path_ = wide_to_utf8(path);
    } catch (...) {
        // Lifecycle observation must never interfere with host load/save.
    }
}

void HostSdkAdapter::set_stopping(
    const bool stopping) noexcept {
    stopping_.store(stopping, std::memory_order_release);
}

EditState HostSdkAdapter::get_edit_state() const noexcept {
    if (edit_handle_ == nullptr || edit_handle_->get_edit_state == nullptr) {
        return EditState::unavailable;
    }
    const int state = edit_handle_->get_edit_state();
    switch (state) {
        case EDIT_HANDLE::EDIT_STATE_EDIT:
            return EditState::edit;
        case EDIT_HANDLE::EDIT_STATE_PLAY:
            return EditState::play;
        case EDIT_HANDLE::EDIT_STATE_SAVE:
            return EditState::save;
        default:
            return EditState::unknown;
    }
}

ProjectInfoResult HostSdkAdapter::get_project_info() noexcept {
    if (edit_handle_ == nullptr || edit_handle_->get_edit_info == nullptr) {
        return ProjectInfoResult{
            false,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 edit handle is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ProjectInfoResult{
            false,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    EDIT_INFO sdk_info{};
    edit_handle_->get_edit_info(
        &sdk_info,
        static_cast<int>(sizeof(sdk_info)));
    if (sdk_info.width <= 0 || sdk_info.height <= 0 || sdk_info.rate <= 0 ||
        sdk_info.scale <= 0) {
        return ProjectInfoResult{
            false,
            {},
            "NOT_CONNECTED_TO_PROJECT",
            "No editable AviUtl2 project is currently available.",
            false,
        };
    }

    std::string project_file_path;
    {
        std::scoped_lock lock(project_path_mutex_);
        project_file_path = project_file_path_;
    }
    return ProjectInfoResult{
        true,
        ProjectInfo{
            sdk_info.scene_id,
            sdk_info.width,
            sdk_info.height,
            sdk_info.rate,
            sdk_info.scale,
            sdk_info.sample_rate,
            sdk_info.frame,
            sdk_info.layer,
            sdk_info.frame_max,
            sdk_info.layer_max,
            std::move(project_file_path),
        },
        {},
        {},
        false,
    };
}

SceneInfoResult HostSdkAdapter::get_current_scene() noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return SceneInfoResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    SceneContext context{edit_handle_, 0, nullptr, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            scene_read_callback)) {
        return SceneInfoResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

SceneInfoResult HostSdkAdapter::update_current_scene(
    const std::int64_t expected_revision,
    const SceneUpdate& update) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return SceneInfoResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    const bool has_size =
        update.width.has_value() &&
        update.height.has_value();
    const bool partial_size =
        update.width.has_value() !=
        update.height.has_value();
    const bool has_rate =
        update.rate.has_value() &&
        update.scale.has_value();
    const bool partial_rate =
        update.rate.has_value() !=
        update.scale.has_value();
    if (partial_size || partial_rate ||
        (!update.name.has_value() && !has_size &&
         !has_rate && !update.sample_rate.has_value())) {
        return SceneInfoResult{
            false,
            expected_revision,
            {},
            "INVALID_ARGUMENT",
            "Scene size/rate fields must be supplied as pairs and at least one update is required.",
            false,
        };
    }
    SceneContext context{
        edit_handle_,
        expected_revision,
        &update,
        {},
    };
    if (!edit_handle_->call_read_section_param(
            &context,
            scene_update_callback)) {
        return SceneInfoResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a scene update section.",
            true,
        };
    }
    return std::move(context.result);
}

EffectCatalogResult HostSdkAdapter::get_effect_catalog(
    const std::size_t start,
    const std::size_t count) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->enum_effect_name == nullptr ||
        edit_handle_->enum_effect_item == nullptr) {
        return EffectCatalogResult{
            false,
            start,
            0U,
            {},
            "EFFECT_CATALOG_UNAVAILABLE",
            "The AviUtl2 effect catalog API is unavailable.",
            false,
        };
    }
    if (count == 0U || count > kMaxCatalogPageEffects) {
        return EffectCatalogResult{
            false,
            start,
            0U,
            {},
            "INVALID_ARGUMENT",
            "The effect catalog page size is outside the limit.",
            false,
        };
    }

    EffectNamesContext names;
    edit_handle_->enum_effect_name(
        &names,
        effect_name_callback);
    if (!names.error_code.empty()) {
        return EffectCatalogResult{
            false,
            start,
            names.effects.size(),
            {},
            std::move(names.error_code),
            std::move(names.error_message),
            false,
        };
    }

    EffectCatalogResult result;
    result.start = start;
    result.total = names.effects.size();
    if (start >= names.effects.size()) {
        result.ok = true;
        return result;
    }
    const std::size_t end =
        std::min(names.effects.size(), start + count);
    result.effects.reserve(end - start);
    for (std::size_t index = start; index < end; ++index) {
        CatalogEffect effect = std::move(names.effects[index]);
        EffectItemsContext items{&effect, {}, {}};
        if (!edit_handle_->enum_effect_item(
                names.wide_names[index].c_str(),
                &items,
                effect_item_callback)) {
            result.error_code = "EFFECT_CATALOG_FAILED";
            result.error_message =
                "AviUtl2 could not enumerate an effect's items.";
            return result;
        }
        if (!items.error_code.empty()) {
            result.error_code = std::move(items.error_code);
            result.error_message = std::move(items.error_message);
            return result;
        }
        result.effects.push_back(std::move(effect));
    }
    result.ok = true;
    return result;
}

LayerSnapshotResult HostSdkAdapter::get_layers(
    const int start,
    const int count) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return LayerSnapshotResult{
            false,
            0,
            0,
            0,
            0,
            0,
            start,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    if (start < 0 || count <= 0 ||
        static_cast<std::size_t>(count) > kMaxLayerPageSize) {
        return LayerSnapshotResult{
            false,
            0,
            0,
            0,
            0,
            0,
            start,
            {},
            "INVALID_ARGUMENT",
            "The requested layer page is outside the limit.",
            false,
        };
    }
    if (get_edit_state() == EditState::save) {
        return LayerSnapshotResult{
            false,
            0,
            0,
            0,
            0,
            0,
            start,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    LayersContext context{edit_handle_, start, count, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            layers_callback)) {
        return LayerSnapshotResult{
            false,
            0,
            0,
            0,
            0,
            0,
            start,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::update_layer(
    const std::int64_t expected_revision,
    const int layer,
    const std::optional<std::wstring>& name,
    const std::optional<bool>& enabled) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    LayerMutationContext context{
        edit_handle_,
        expected_revision,
        layer,
        &name,
        &enabled,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            layer_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

BatchEditResult HostSdkAdapter::validate_create_alias_batch(
    const std::vector<CreateAliasCommand>& commands) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return unavailable_result(
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true);
    }
    if (get_edit_state() == EditState::save) {
        return unavailable_result(
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true);
    }

    BatchContext context{&commands, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            validate_callback)) {
        return unavailable_result(
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true);
    }
    return context.result;
}

BatchEditResult HostSdkAdapter::apply_create_alias_batch(
    const std::vector<CreateAliasCommand>& commands) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return unavailable_result(
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true);
    }
    if (get_edit_state() == EditState::save) {
        return unavailable_result(
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true);
    }

    BatchContext context{&commands, {}};
    if (!edit_handle_->call_edit_section_param(
            &context,
            apply_callback)) {
        return unavailable_result(
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true);
    }
    return context.result;
}

SnapshotResult HostSdkAdapter::get_snapshot() noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return SnapshotResult{
            false,
            0,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return SnapshotResult{
            false,
            0,
            0,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    SnapshotContext context{edit_handle_, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            snapshot_callback)) {
        return SnapshotResult{
            false,
            0,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::set_object_items(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::vector<ObjectItemUpdate>& updates) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    MutationContext context{
        edit_handle_,
        MutationType::set_items,
        expected_revision,
        object_index,
        &updates,
        0,
        0,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::set_object_name(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::optional<std::wstring>& name) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    ObjectNameMutationContext context{
        edit_handle_,
        expected_revision,
        object_index,
        &name,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            object_name_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::move_object(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int layer,
    const int frame) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    MutationContext context{
        edit_handle_,
        MutationType::move,
        expected_revision,
        object_index,
        nullptr,
        layer,
        frame,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::delete_object(
    const std::int64_t expected_revision,
    const std::size_t object_index) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    MutationContext context{
        edit_handle_,
        MutationType::remove,
        expected_revision,
        object_index,
        nullptr,
        0,
        0,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::add_object_effect(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::wstring& effect) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    EffectMutationContext context{
        edit_handle_,
        EffectMutationType::add,
        expected_revision,
        object_index,
        &effect,
        nullptr,
        true,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            effect_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::add_object_effect_with_items(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::wstring& effect,
    const std::vector<EffectInitialItem>& initial_items) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    EffectMutationContext context{
        edit_handle_,
        EffectMutationType::add,
        expected_revision,
        object_index,
        &effect,
        &initial_items,
        true,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            effect_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::set_object_effect_enabled(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::wstring& selector,
    const bool enabled) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    EffectMutationContext context{
        edit_handle_,
        EffectMutationType::set_enabled,
        expected_revision,
        object_index,
        &selector,
        nullptr,
        enabled,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            effect_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::delete_object_effect(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::wstring& selector) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    EffectMutationContext context{
        edit_handle_,
        EffectMutationType::remove,
        expected_revision,
        object_index,
        &selector,
        nullptr,
        true,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            effect_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

SplitMediaResult HostSdkAdapter::split_media_object(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int frame) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return SplitMediaResult{
            false,
            -1,
            -1,
            -1,
            -1,
            -1,
            -1,
            0.0,
            0.0,
            1.0,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        SplitMediaResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message = "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    SplitMediaContext context{
        edit_handle_,
        expected_revision,
        object_index,
        frame,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            split_media_callback)) {
        SplitMediaResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open an edit section.";
        result.retryable = true;
        return result;
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

StructuralEditResult HostSdkAdapter::set_object_duration(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int duration) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 edit section is unavailable.";
        result.retryable = true;
        return result;
    }
    if (get_edit_state() == EditState::save) {
        StructuralEditResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message =
            "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    StructuralEditContext context{
        edit_handle_,
        StructuralEditType::duration,
        expected_revision,
        object_index,
        duration,
        -1,
        -1,
        nullptr,
        nullptr,
        nullptr,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            structural_edit_callback)) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open an edit section.";
        result.retryable = true;
        return result;
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

StructuralEditResult HostSdkAdapter::trim_media_object(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int frame_start,
    const int frame_end,
    const std::optional<double>& source_position) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 edit section is unavailable.";
        result.retryable = true;
        return result;
    }
    if (get_edit_state() == EditState::save) {
        StructuralEditResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message =
            "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    StructuralEditContext context{
        edit_handle_,
        StructuralEditType::trim,
        expected_revision,
        object_index,
        0,
        frame_start,
        frame_end,
        &source_position,
        nullptr,
        nullptr,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            structural_edit_callback)) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open an edit section.";
        result.retryable = true;
        return result;
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

StructuralEditResult HostSdkAdapter::reorder_object_effects(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const std::vector<std::wstring>& selectors) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr ||
        edit_handle_->enum_effect_name == nullptr) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 structural effect APIs are unavailable.";
        result.retryable = true;
        return result;
    }
    if (get_edit_state() == EditState::save) {
        StructuralEditResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message =
            "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    EffectNamesContext names;
    edit_handle_->enum_effect_name(
        &names,
        effect_name_callback);
    if (!names.error_code.empty()) {
        StructuralEditResult result;
        result.error_code = std::move(names.error_code);
        result.error_message = std::move(names.error_message);
        return result;
    }
    std::unordered_map<std::wstring, int> effect_types;
    for (std::size_t index = 0U;
         index < names.effects.size();
         ++index) {
        effect_types.emplace(
            names.wide_names[index],
            names.effects[index].type);
    }
    StructuralEditContext context{
        edit_handle_,
        StructuralEditType::reorder,
        expected_revision,
        object_index,
        0,
        -1,
        -1,
        nullptr,
        &selectors,
        &effect_types,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            structural_edit_callback)) {
        StructuralEditResult result;
        result.error_code = "EDIT_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open an edit section.";
        result.retryable = true;
        return result;
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

TimelineTransactionResult
HostSdkAdapter::run_timeline_transaction(
    const std::int64_t expected_revision,
    const std::vector<TimelineCommand>& commands,
    const bool apply) noexcept {
    if (edit_handle_ == nullptr ||
        (apply &&
         edit_handle_->call_edit_section_param == nullptr) ||
        (!apply &&
         edit_handle_->call_read_section_param == nullptr)) {
        TimelineTransactionResult result;
        result.error_code =
            apply
                ? "EDIT_SECTION_UNAVAILABLE"
                : "READ_SECTION_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 transaction section is unavailable.";
        result.retryable = true;
        return result;
    }
    if (get_edit_state() == EditState::save) {
        TimelineTransactionResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message =
            "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    TimelineTransactionContext context{
        edit_handle_,
        expected_revision,
        &commands,
        apply,
        {},
    };
    const bool opened =
        apply
            ? edit_handle_->call_edit_section_param(
                  &context,
                  timeline_transaction_callback)
            : edit_handle_->call_read_section_param(
                  &context,
                  timeline_transaction_callback);
    if (!opened) {
        TimelineTransactionResult result;
        result.error_code =
            apply
                ? "EDIT_SECTION_UNAVAILABLE"
                : "READ_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open the transaction section.";
        result.retryable = true;
        return result;
    }
    if (apply && context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision =
                snapshot.revision;
        }
    }
    return std::move(context.result);
}

EditPlanResult HostSdkAdapter::run_edit_plan(
    const std::int64_t expected_revision,
    const std::vector<EditPlanCommand>& commands,
    const bool apply) noexcept {
    if (edit_handle_ == nullptr ||
        (apply && edit_handle_->call_edit_section_param == nullptr) ||
        (!apply && edit_handle_->call_read_section_param == nullptr)) {
        EditPlanResult result;
        result.error_code =
            apply ? "EDIT_SECTION_UNAVAILABLE" : "READ_SECTION_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 mixed edit-plan section is unavailable.";
        result.retryable = true;
        return result;
    }
    if (get_edit_state() == EditState::save) {
        EditPlanResult result;
        result.error_code = "HOST_EXPORTING";
        result.error_message = "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    try {
        bool needs_catalog = false;
        for (const EditPlanCommand& command : commands) {
            needs_catalog = needs_catalog ||
                command.type == EditPlanCommandType::add_effect ||
                !command.effects.empty() ||
                (command.type == EditPlanCommandType::create_media &&
                 !command.updates.empty());
        }
        if (needs_catalog) {
            std::vector<CatalogEffect> catalog;
            std::size_t start = 0U;
            while (true) {
                EffectCatalogResult page = get_effect_catalog(
                    start,
                    kMaxCatalogPageEffects);
                if (!page.ok) {
                    EditPlanResult result;
                    result.error_code = page.error_code;
                    result.error_message = page.error_message;
                    result.retryable = page.retryable;
                    return result;
                }
                catalog.insert(
                    catalog.end(),
                    std::make_move_iterator(page.effects.begin()),
                    std::make_move_iterator(page.effects.end()));
                start += page.effects.size();
                if (start >= page.total) {
                    break;
                }
                if (page.effects.empty()) {
                    EditPlanResult result;
                    result.error_code = "EFFECT_CATALOG_FAILED";
                    result.error_message =
                        "AviUtl2 returned invalid effect catalog paging.";
                    return result;
                }
            }
            const auto schema_matches = [&](
                const std::wstring& effect,
                const std::wstring& item) {
                const std::string effect_utf8 = wide_to_utf8(effect);
                const std::string item_utf8 = wide_to_utf8(item);
                std::size_t effect_matches = 0U;
                bool item_found = false;
                for (const CatalogEffect& candidate : catalog) {
                    if (candidate.name != effect_utf8) {
                        continue;
                    }
                    ++effect_matches;
                    item_found = item_found || std::any_of(
                        candidate.items.begin(),
                        candidate.items.end(),
                        [&](const CatalogItem& candidate_item) {
                            return candidate_item.name == item_utf8;
                        });
                }
                return effect_matches == 1U && item_found;
            };
            for (std::size_t index = 0U;
                 index < commands.size();
                 ++index) {
                const EditPlanCommand& command = commands[index];
                for (const EditPlanEffect& planned : command.effects) {
                    const std::string effect_utf8 =
                        wide_to_utf8(planned.effect);
                    const CatalogEffect* match = nullptr;
                    std::size_t matches = 0U;
                    for (const CatalogEffect& candidate : catalog) {
                        if (candidate.name == effect_utf8) {
                            match = &candidate;
                            ++matches;
                        }
                    }
                    const int required_flag =
                        planned.scope == EditPlanEffectScope::audio
                            ? 2
                            : planned.scope == EditPlanEffectScope::video
                                  ? 1
                                  : 0;
                    if (matches != 1U || match == nullptr ||
                        (required_flag != 0 &&
                         (match->flags & required_flag) == 0) ||
                        std::any_of(
                            planned.items.begin(),
                            planned.items.end(),
                            [&](const EffectInitialItem& item) {
                                return !schema_matches(planned.effect, item.item);
                            })) {
                        EditPlanResult result;
                        result.failed_command_index = index;
                        result.error_code = "EFFECT_SCHEMA_NOT_FOUND";
                        result.error_message =
                            "A create-time effect, scope, or item is absent from the live catalog.";
                        return result;
                    }
                }
                if (command.type == EditPlanCommandType::add_effect) {
                    const std::string effect_utf8 =
                        wide_to_utf8(command.effect);
                    const auto matching_effects = std::count_if(
                        catalog.begin(),
                        catalog.end(),
                        [&](const CatalogEffect& candidate) {
                            return candidate.name == effect_utf8;
                        });
                    if (matching_effects != 1 ||
                        std::any_of(
                            command.effect_items.begin(),
                            command.effect_items.end(),
                            [&](const EffectInitialItem& item) {
                                return !schema_matches(
                                    command.effect,
                                    item.item);
                            })) {
                        EditPlanResult result;
                        result.failed_command_index = index;
                        result.error_code = "EFFECT_SCHEMA_NOT_FOUND";
                        result.error_message =
                            "A planned effect or initial item is absent from the live catalog.";
                        return result;
                    }
                } else if (
                    command.type == EditPlanCommandType::create_media &&
                    std::any_of(
                        command.updates.begin(),
                        command.updates.end(),
                        [&](const ObjectItemUpdate& update) {
                            return !schema_matches(
                                update.effect,
                                update.item);
                        })) {
                    EditPlanResult result;
                    result.failed_command_index = index;
                    result.error_code = "EFFECT_SCHEMA_NOT_FOUND";
                    result.error_message =
                        "A planned media item is absent from the live catalog.";
                    return result;
                }
            }
        }
    } catch (const std::exception& error) {
        EditPlanResult result;
        result.error_code = "EFFECT_CATALOG_FAILED";
        result.error_message = error.what();
        return result;
    } catch (...) {
        EditPlanResult result;
        result.error_code = "EFFECT_CATALOG_FAILED";
        result.error_message =
            "The effect catalog could not validate the edit plan.";
        return result;
    }
    EditPlanContext context{
        edit_handle_,
        expected_revision,
        &commands,
        apply,
        {},
    };
    const bool opened =
        apply
            ? edit_handle_->call_edit_section_param(
                  &context,
                  edit_plan_callback)
            : edit_handle_->call_read_section_param(
                  &context,
                  edit_plan_callback);
    if (!opened) {
        EditPlanResult result;
        result.error_code =
            apply ? "EDIT_SECTION_UNAVAILABLE" : "READ_SECTION_UNAVAILABLE";
        result.error_message =
            "AviUtl2 could not open the mixed edit-plan section.";
        result.retryable = true;
        return result;
    }
    if (apply && context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

MediaProbeResult HostSdkAdapter::probe_media(
    const std::wstring& file) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return MediaProbeResult{
            false,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return MediaProbeResult{
            false,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    MediaProbeContext context{&file, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            media_probe_callback)) {
        return MediaProbeResult{
            false,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

CreateMediaResult HostSdkAdapter::create_object_from_media_file(
    const std::wstring& file,
    const int layer,
    const int frame,
    const int length) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return CreateMediaResult{
            false,
            -1,
            -1,
            -1,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return CreateMediaResult{
            false,
            -1,
            -1,
            -1,
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    CreateMediaContext context{&file, layer, frame, length, {}};
    if (!edit_handle_->call_edit_section_param(
            &context,
            create_media_callback)) {
        return CreateMediaResult{
            false,
            -1,
            -1,
            -1,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    return std::move(context.result);
}

ObjectInspectionResult HostSdkAdapter::inspect_object(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int sample_frame) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return ObjectInspectionResult{
            false,
            0,
            sample_frame,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectInspectionResult{
            false,
            0,
            sample_frame,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    InspectContext context{
        edit_handle_,
        expected_revision,
        object_index,
        sample_frame,
        {},
    };
    if (!edit_handle_->call_read_section_param(
            &context,
            inspect_callback)) {
        return ObjectInspectionResult{
            false,
            0,
            sample_frame,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

ObjectSectionsResult HostSdkAdapter::get_object_sections(
    const std::int64_t expected_revision,
    const std::size_t object_index) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return ObjectSectionsResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "The AviUtl2 read section is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return ObjectSectionsResult{
            false,
            0,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }
    ObjectSectionsContext context{
        edit_handle_,
        expected_revision,
        object_index,
        {},
    };
    if (!edit_handle_->call_read_section_param(
            &context,
            object_sections_callback)) {
        return ObjectSectionsResult{
            false,
            0,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::create_object_section(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int frame) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    SectionMutationContext context{
        edit_handle_,
        SectionMutationType::create,
        expected_revision,
        object_index,
        -1,
        frame,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            section_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::delete_object_section(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int section) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    SectionMutationContext context{
        edit_handle_,
        SectionMutationType::remove,
        expected_revision,
        object_index,
        section,
        -1,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            section_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

ObjectMutationResult HostSdkAdapter::move_object_section(
    const std::int64_t expected_revision,
    const std::size_t object_index,
    const int section,
    const int frame) noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->call_edit_section_param == nullptr) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "The AviUtl2 edit section is unavailable.",
            true,
        };
    }
    SectionMutationContext context{
        edit_handle_,
        SectionMutationType::move,
        expected_revision,
        object_index,
        section,
        frame,
        {},
    };
    if (!edit_handle_->call_edit_section_param(
            &context,
            section_mutation_callback)) {
        return ObjectMutationResult{
            false,
            -1,
            0U,
            "EDIT_SECTION_UNAVAILABLE",
            "AviUtl2 could not open an edit section.",
            true,
        };
    }
    if (context.result.ok) {
        context.result.current_revision = -1;
        SnapshotResult snapshot = get_snapshot();
        if (snapshot.ok) {
            context.result.current_revision = snapshot.revision;
        }
    }
    return std::move(context.result);
}

StringCatalogResult HostSdkAdapter::get_font_catalog() noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->enum_font_name == nullptr) {
        return StringCatalogResult{
            false,
            {},
            "FONT_CATALOG_UNAVAILABLE",
            "The AviUtl2 font catalog API is unavailable.",
            false,
        };
    }
    StringCatalogContext context{{}, 4096U};
    edit_handle_->enum_font_name(
        &context,
        font_name_callback);
    if (!context.result.error_code.empty()) {
        return std::move(context.result);
    }
    std::sort(
        context.result.values.begin(),
        context.result.values.end());
    context.result.values.erase(
        std::unique(
            context.result.values.begin(),
            context.result.values.end()),
        context.result.values.end());
    context.result.ok = true;
    return std::move(context.result);
}

PaletteCatalogResult
HostSdkAdapter::get_palette_catalog() noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->enum_palette_name == nullptr ||
        edit_handle_->call_read_section_param == nullptr) {
        return PaletteCatalogResult{
            false,
            {},
            "PALETTE_CATALOG_UNAVAILABLE",
            "The AviUtl2 palette catalog API is unavailable.",
            false,
        };
    }
    PaletteNamesContext names;
    edit_handle_->enum_palette_name(
        &names,
        palette_name_callback);
    if (!names.error_code.empty()) {
        return PaletteCatalogResult{
            false,
            {},
            std::move(names.error_code),
            std::move(names.error_message),
            false,
        };
    }
    PaletteDetailsContext context{&names.names, {}};
    if (!edit_handle_->call_read_section_param(
            &context,
            palette_details_callback)) {
        return PaletteCatalogResult{
            false,
            {},
            "READ_SECTION_UNAVAILABLE",
            "AviUtl2 could not open a read section.",
            true,
        };
    }
    return std::move(context.result);
}

ModuleCatalogResult HostSdkAdapter::get_module_catalog() noexcept {
    if (edit_handle_ == nullptr ||
        edit_handle_->enum_module_info == nullptr) {
        return ModuleCatalogResult{
            false,
            {},
            "MODULE_CATALOG_UNAVAILABLE",
            "The AviUtl2 module catalog API is unavailable.",
            false,
        };
    }
    ModuleCatalogContext context{{}, 4096U};
    edit_handle_->enum_module_info(
        &context,
        module_info_callback);
    if (!context.result.error_code.empty()) {
        return std::move(context.result);
    }
    context.result.ok = true;
    return std::move(context.result);
}

RenderedFrameResult HostSdkAdapter::render_frame(
    const int frame) noexcept {
    if (stopping_.load(std::memory_order_acquire)) {
        return RenderedFrameResult{
            false,
            frame,
            0,
            0,
            {},
            "BRIDGE_STOPPING",
            "The bridge stopped before frame rendering.",
            true,
        };
    }
    if (edit_handle_ == nullptr ||
        edit_handle_->rendering_scene_video == nullptr) {
        return RenderedFrameResult{
            false,
            frame,
            0,
            0,
            {},
            "RENDERING_UNAVAILABLE",
            "The AviUtl2 scene rendering API is unavailable.",
            true,
        };
    }
    if (get_edit_state() == EditState::save) {
        return RenderedFrameResult{
            false,
            frame,
            0,
            0,
            {},
            "HOST_EXPORTING",
            "AviUtl2 is currently exporting.",
            true,
        };
    }

    RenderContext* const context = new (std::nothrow) RenderContext();
    if (context == nullptr) {
        return RenderedFrameResult{
            false,
            frame,
            0,
            0,
            {},
            "RENDER_ALLOCATION_FAILED",
            "The render context could not be allocated.",
            true,
        };
    }
    context->result.frame = frame;
    if (!edit_handle_->rendering_scene_video(
            frame,
            context,
            rendering_video_callback)) {
        release_render_context(context);
        release_render_context(context);
        return RenderedFrameResult{
            false,
            frame,
            0,
            0,
            {},
            "FRAME_RENDER_REJECTED",
            "AviUtl2 rejected the scene rendering request.",
            true,
        };
    }

    RenderedFrameResult result;
    {
        std::unique_lock lock(context->mutex);
        const auto deadline =
            std::chrono::steady_clock::now() +
            std::chrono::seconds(kRenderTimeoutSeconds);
        while (!context->done &&
               !stopping_.load(std::memory_order_acquire) &&
               std::chrono::steady_clock::now() < deadline) {
            context->completed.wait_for(
                lock,
                std::chrono::milliseconds(100));
        }
        if (stopping_.load(std::memory_order_acquire) &&
            !context->done) {
            result = RenderedFrameResult{
                false,
                frame,
                0,
                0,
                {},
                "BRIDGE_STOPPING",
                "The bridge stopped during frame rendering.",
                true,
            };
        } else if (!context->done) {
            result = RenderedFrameResult{
                false,
                frame,
                0,
                0,
                {},
                "FRAME_RENDER_TIMEOUT",
                "AviUtl2 did not complete the frame render in time.",
                true,
            };
        } else {
            result = std::move(context->result);
        }
    }
    release_render_context(context);
    return result;
}

RenderedAudioResult HostSdkAdapter::render_audio(
    const int frame_start,
    const int frame_end) noexcept {
    RenderedAudioResult result;
    result.frame_start = frame_start;
    result.frame_end = frame_end;
    if (stopping_.load(std::memory_order_acquire)) {
        result.error_code = "BRIDGE_STOPPING";
        result.error_message =
            "The bridge stopped before audio rendering.";
        result.retryable = true;
        return result;
    }
    if (edit_handle_ == nullptr ||
        edit_handle_->rendering_scene_audio == nullptr) {
        result.error_code = "AUDIO_RENDERING_UNAVAILABLE";
        result.error_message =
            "The AviUtl2 scene audio rendering API is unavailable.";
        result.retryable = true;
        return result;
    }
    const std::int64_t frame_count =
        static_cast<std::int64_t>(frame_end) -
        static_cast<std::int64_t>(frame_start) + 1;
    if (frame_start < 0 || frame_end < frame_start ||
        frame_count > kMaxAudioRenderFrames) {
        result.error_code = "INVALID_ARGUMENT";
        result.error_message =
            "The audio frame range is invalid or exceeds the limit.";
        return result;
    }
    if (get_edit_state() == EditState::save) {
        result.error_code = "HOST_EXPORTING";
        result.error_message =
            "AviUtl2 is currently exporting.";
        result.retryable = true;
        return result;
    }
    const ProjectInfoResult project = get_project_info();
    if (!project.ok || project.info.sample_rate <= 0) {
        result.error_code =
            project.error_code.empty()
                ? "PROJECT_INFO_UNAVAILABLE"
                : project.error_code;
        result.error_message =
            project.error_message.empty()
                ? "The scene sample rate is unavailable."
                : project.error_message;
        result.retryable = project.retryable;
        return result;
    }
    result.sample_rate = project.info.sample_rate;

    try {
        for (int frame = frame_start;
             frame <= frame_end;
             ++frame) {
            if (stopping_.load(
                    std::memory_order_acquire)) {
                result.interleaved_stereo.clear();
                result.error_code = "BRIDGE_STOPPING";
                result.error_message =
                    "The bridge stopped during audio rendering.";
                result.retryable = true;
                return result;
            }
            AudioRenderContext* const context =
                new (std::nothrow) AudioRenderContext();
            if (context == nullptr) {
                result.error_code =
                    "AUDIO_RENDER_ALLOCATION_FAILED";
                result.error_message =
                    "The audio render context could not be allocated.";
                result.retryable = true;
                return result;
            }
            context->requested_frame = frame;
            if (!edit_handle_->rendering_scene_audio(
                    frame,
                    context,
                    rendering_audio_callback)) {
                release_audio_render_context(context);
                release_audio_render_context(context);
                result.error_code =
                    "AUDIO_RENDER_REJECTED";
                result.error_message =
                    "AviUtl2 rejected the scene audio rendering request.";
                result.retryable = true;
                return result;
            }

            std::vector<float> frame_audio;
            {
                std::unique_lock lock(context->mutex);
                const auto deadline =
                    std::chrono::steady_clock::now() +
                    std::chrono::seconds(
                        kRenderTimeoutSeconds);
                while (!context->done &&
                       !stopping_.load(
                           std::memory_order_acquire) &&
                       std::chrono::steady_clock::now() <
                           deadline) {
                    context->completed.wait_for(
                        lock,
                        std::chrono::milliseconds(100));
                }
                if (stopping_.load(
                        std::memory_order_acquire) &&
                    !context->done) {
                    result.error_code =
                        "BRIDGE_STOPPING";
                    result.error_message =
                        "The bridge stopped during audio rendering.";
                    result.retryable = true;
                } else if (!context->done) {
                    result.error_code =
                        "AUDIO_RENDER_TIMEOUT";
                    result.error_message =
                        "AviUtl2 did not complete the audio render in time.";
                    result.retryable = true;
                } else if (!context->error_code.empty()) {
                    result.error_code =
                        std::move(context->error_code);
                    result.error_message =
                        std::move(context->error_message);
                } else {
                    frame_audio =
                        std::move(context->interleaved_stereo);
                }
            }
            release_audio_render_context(context);
            if (!result.error_code.empty()) {
                return result;
            }
            const std::size_t current_bytes =
                result.interleaved_stereo.size() *
                sizeof(float);
            const std::size_t frame_bytes =
                frame_audio.size() * sizeof(float);
            if (frame_bytes > kMaxAudioCaptureBytes ||
                current_bytes >
                    kMaxAudioCaptureBytes - frame_bytes) {
                result.interleaved_stereo.clear();
                result.error_code =
                    "AUDIO_CAPTURE_TOO_LARGE";
                result.error_message =
                    "The rendered audio range exceeds the capture limit.";
                return result;
            }
            result.interleaved_stereo.insert(
                result.interleaved_stereo.end(),
                frame_audio.begin(),
                frame_audio.end());
        }
        result.ok = true;
    } catch (const std::exception& error) {
        result.interleaved_stereo.clear();
        result.error_code = "AUDIO_RENDER_FAILED";
        result.error_message = error.what();
    } catch (...) {
        result.interleaved_stereo.clear();
        result.error_code = "AUDIO_RENDER_FAILED";
        result.error_message =
            "The audio range could not be rendered.";
    }
    return result;
}

std::string edit_state_name(const EditState state) {
    switch (state) {
        case EditState::edit:
            return "edit";
        case EditState::play:
            return "play";
        case EditState::save:
            return "save";
        case EditState::unavailable:
            return "unavailable";
        case EditState::unknown:
        default:
            return "unknown";
    }
}

}  // namespace aviutl2::live
