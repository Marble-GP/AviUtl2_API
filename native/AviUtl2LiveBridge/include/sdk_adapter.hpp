#pragma once

#include <atomic>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <vector>

struct EDIT_HANDLE;

namespace aviutl2::live {

enum class EditState {
    edit,
    play,
    save,
    unknown,
    unavailable,
};

struct ProjectInfo final {
    int scene_id = 0;
    int width = 0;
    int height = 0;
    int rate = 0;
    int scale = 0;
    int sample_rate = 0;
    int cursor_frame = 0;
    int cursor_layer = 0;
    int frame_max = 0;
    int layer_max = 0;
};

struct ProjectInfoResult final {
    bool ok = false;
    ProjectInfo info;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct SceneInfo final {
    int scene_id = 0;
    std::string name;
    int width = 0;
    int height = 0;
    int rate = 0;
    int scale = 0;
    int sample_rate = 0;
};

struct SceneInfoResult final {
    bool ok = false;
    std::int64_t revision = 0;
    SceneInfo info;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct SceneUpdate final {
    std::optional<std::wstring> name;
    std::optional<int> width;
    std::optional<int> height;
    std::optional<int> rate;
    std::optional<int> scale;
    std::optional<int> sample_rate;
};

struct CatalogItem final {
    std::string name;
    int type = 0;
};

struct CatalogEffect final {
    std::string name;
    int type = 0;
    int flags = 0;
    std::vector<CatalogItem> items;
};

struct EffectCatalogResult final {
    bool ok = false;
    std::size_t start = 0U;
    std::size_t total = 0U;
    std::vector<CatalogEffect> effects;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct LayerInfo final {
    int layer = 0;
    bool has_name = false;
    std::string name;
    bool enabled = true;
    bool locked = false;
    std::size_t object_count = 0U;
};

struct LayerSnapshotResult final {
    bool ok = false;
    std::int64_t revision = 0;
    int scene_id = 0;
    int layer_max = 0;
    int display_layer_start = 0;
    int display_layer_count = 0;
    int start = 0;
    std::vector<LayerInfo> layers;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct CreateAliasCommand final {
    std::string client_id;
    std::string alias;
    int layer = 0;
    int frame = 0;
    int length = 0;
};

struct BatchEditResult final {
    bool ok = false;
    std::size_t applied_count = 0U;
    std::size_t failed_command_index =
        std::numeric_limits<std::size_t>::max();
    std::string error_code;
    std::string error_message;
    bool retryable = false;
    bool has_collision = false;
    int collision_layer = -1;
    int collision_start = -1;
    int collision_end = -1;
};

struct SnapshotObject final {
    std::string object_id;
    int layer = 0;
    int frame_start = 0;
    int frame_end = 0;
    bool has_name = false;
    std::string name;
    std::string alias;
    bool api_locked = false;
};

struct SnapshotResult final {
    bool ok = false;
    std::int64_t revision = 0;
    int scene_id = 0;
    std::vector<SnapshotObject> objects;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct ObjectItemUpdate final {
    std::wstring effect;
    std::wstring item;
    std::string value;
};

struct EffectInitialItem final {
    std::wstring item;
    std::string value;
};

struct ObjectMutationResult final {
    bool ok = false;
    std::int64_t current_revision = -1;
    std::size_t applied_count = 0U;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct ObjectSectionInfo final {
    int index = 0;
    int frame = 0;
};

struct ObjectSectionsResult final {
    bool ok = false;
    std::int64_t revision = 0;
    std::vector<ObjectSectionInfo> sections;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct StringCatalogResult final {
    bool ok = false;
    std::vector<std::string> values;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct ModuleCatalogEntry final {
    int type = 0;
    std::string type_name;
    std::string name;
    std::string information;
};

struct ModuleCatalogResult final {
    bool ok = false;
    std::vector<ModuleCatalogEntry> modules;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct PaletteCatalogEntry final {
    std::string name;
    std::vector<std::uint32_t> colors_rgba;
};

struct PaletteCatalogResult final {
    bool ok = false;
    std::vector<PaletteCatalogEntry> palettes;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct SplitMediaResult final {
    bool ok = false;
    std::int64_t current_revision = -1;
    int layer = -1;
    int left_start = -1;
    int left_end = -1;
    int right_start = -1;
    int right_end = -1;
    double source_position_before = 0.0;
    double source_position_after = 0.0;
    double playback_rate = 1.0;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct StructuralEditResult final {
    bool ok = false;
    std::int64_t current_revision = -1;
    int layer = -1;
    int frame_start = -1;
    int frame_end = -1;
    bool has_source_position = false;
    double source_position = 0.0;
    std::vector<std::string> effect_order;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

enum class TimelineCommandType {
    move,
    remove,
    set_items,
    set_name,
    set_effect_enabled,
};

struct TimelineCommand final {
    TimelineCommandType type = TimelineCommandType::move;
    std::size_t object_index = 0U;
    int layer = 0;
    int frame = 0;
    std::vector<ObjectItemUpdate> updates;
    std::optional<std::wstring> name;
    std::wstring effect_selector;
    bool enabled = true;
};

struct TimelineTransactionResult final {
    bool ok = false;
    bool valid = false;
    std::int64_t current_revision = -1;
    std::size_t applied_count = 0U;
    std::size_t failed_command_index =
        std::numeric_limits<std::size_t>::max();
    std::string error_code;
    std::string error_message;
    bool retryable = false;
    bool has_collision = false;
    int collision_layer = -1;
    int collision_start = -1;
    int collision_end = -1;
};

enum class EditPlanCommandType {
    create_alias,
    create_media,
    update,
    move,
    remove,
    add_effect,
    set_effect_enabled,
};

enum class EditPlanEffectScope {
    primary,
    video,
    audio,
};

struct EditPlanEffect final {
    std::string profile;
    std::wstring effect;
    std::vector<EffectInitialItem> items;
    EditPlanEffectScope scope = EditPlanEffectScope::primary;
    bool enabled = true;
};

struct EditPlanCommand final {
    std::string key;
    EditPlanCommandType type = EditPlanCommandType::create_alias;
    std::size_t object_index = 0U;
    std::string alias;
    std::wstring file;
    int layer = 0;
    int frame = 0;
    int length = 0;
    std::vector<ObjectItemUpdate> updates;
    std::optional<std::wstring> name;
    std::wstring effect;
    std::vector<EffectInitialItem> effect_items;
    std::vector<EditPlanEffect> effects;
    bool enabled = true;
};

struct EditPlanResult final {
    bool ok = false;
    bool valid = false;
    std::int64_t current_revision = -1;
    std::size_t applied_count = 0U;
    std::size_t failed_command_index =
        std::numeric_limits<std::size_t>::max();
    bool rollback_attempted = false;
    bool rollback_complete = true;
    std::size_t restored_count = 0U;
    bool gui_undo_required = false;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct MediaInfo final {
    bool extension_supported = false;
    bool readable = false;
    bool has_info = false;
    int video_track_count = 0;
    int audio_track_count = 0;
    double duration_seconds = 0.0;
    int width = 0;
    int height = 0;
};

struct MediaProbeResult final {
    bool ok = false;
    MediaInfo info;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct CreateMediaResult final {
    bool ok = false;
    int layer = -1;
    int frame_start = -1;
    int frame_end = -1;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct InspectedTrack final {
    bool available = false;
    bool has_mode = false;
    std::string mode;
    std::vector<double> parameters;
    bool accelerate = false;
    bool decelerate = false;
    bool ignore_midpoints = false;
    bool time_control = false;
    int group_count = 1;
    int group_index = 0;
    bool has_group_name = false;
    std::string group_name;
    bool has_sampled_value = false;
    double sampled_value = 0.0;
    std::vector<std::string> group_items;
};

struct InspectedItem final {
    std::string name;
    int type = 0;
    bool has_value = false;
    std::string value;
    InspectedTrack track;
    bool has_sampled_check_value = false;
    bool sampled_check_value = false;
};

struct InspectedEffect final {
    int index = 0;
    int occurrence = 0;
    std::string name;
    std::string selector;
    bool enabled = false;
    bool locked = false;
    std::vector<InspectedItem> items;
};

struct ObjectInspectionResult final {
    bool ok = false;
    std::int64_t revision = 0;
    int sampled_frame = 0;
    std::vector<InspectedEffect> effects;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct RenderedFrameResult final {
    bool ok = false;
    int frame = 0;
    int width = 0;
    int height = 0;
    std::vector<std::uint8_t> rgba;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

struct RenderedAudioResult final {
    bool ok = false;
    int frame_start = 0;
    int frame_end = 0;
    int sample_rate = 0;
    std::vector<float> interleaved_stereo;
    std::string error_code;
    std::string error_message;
    bool retryable = false;
};

class SdkAdapter {
public:
    virtual ~SdkAdapter() = default;
    virtual void set_stopping(bool) noexcept {}
    [[nodiscard]] virtual EditState get_edit_state() const noexcept = 0;
    [[nodiscard]] virtual ProjectInfoResult get_project_info() noexcept = 0;
    [[nodiscard]] virtual SceneInfoResult get_current_scene() noexcept = 0;
    [[nodiscard]] virtual SceneInfoResult update_current_scene(
        std::int64_t expected_revision,
        const SceneUpdate& update) noexcept = 0;
    [[nodiscard]] virtual EffectCatalogResult get_effect_catalog(
        std::size_t start,
        std::size_t count) noexcept = 0;
    [[nodiscard]] virtual LayerSnapshotResult get_layers(
        int start,
        int count) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult update_layer(
        std::int64_t expected_revision,
        int layer,
        const std::optional<std::wstring>& name,
        const std::optional<bool>& enabled) noexcept = 0;
    [[nodiscard]] virtual BatchEditResult validate_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept = 0;
    [[nodiscard]] virtual BatchEditResult apply_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept = 0;
    [[nodiscard]] virtual SnapshotResult get_snapshot() noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult set_object_items(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::vector<ObjectItemUpdate>& updates) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult set_object_name(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::optional<std::wstring>& name) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult move_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int layer,
        int frame) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult delete_object(
        std::int64_t expected_revision,
        std::size_t object_index) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult add_object_effect(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& effect) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult
    add_object_effect_with_items(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& effect,
        const std::vector<EffectInitialItem>& initial_items) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult set_object_effect_enabled(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& selector,
        bool enabled) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult delete_object_effect(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& selector) noexcept = 0;
    [[nodiscard]] virtual SplitMediaResult split_media_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame) noexcept = 0;
    [[nodiscard]] virtual StructuralEditResult set_object_duration(
        std::int64_t expected_revision,
        std::size_t object_index,
        int duration) noexcept = 0;
    [[nodiscard]] virtual StructuralEditResult trim_media_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame_start,
        int frame_end,
        const std::optional<double>& source_position) noexcept = 0;
    [[nodiscard]] virtual StructuralEditResult reorder_object_effects(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::vector<std::wstring>& selectors) noexcept = 0;
    [[nodiscard]] virtual TimelineTransactionResult
    run_timeline_transaction(
        std::int64_t expected_revision,
        const std::vector<TimelineCommand>& commands,
        bool apply) noexcept = 0;
    [[nodiscard]] virtual EditPlanResult run_edit_plan(
        std::int64_t,
        const std::vector<EditPlanCommand>&,
        bool) noexcept {
        EditPlanResult result;
        result.error_code = "SDK_METHOD_UNAVAILABLE";
        result.error_message =
            "The mixed edit-plan SDK adapter is unavailable.";
        return result;
    }
    [[nodiscard]] virtual MediaProbeResult probe_media(
        const std::wstring& file) noexcept = 0;
    [[nodiscard]] virtual CreateMediaResult create_object_from_media_file(
        const std::wstring& file,
        int layer,
        int frame,
        int length) noexcept = 0;
    [[nodiscard]] virtual ObjectInspectionResult inspect_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int sample_frame) noexcept = 0;
    [[nodiscard]] virtual ObjectSectionsResult get_object_sections(
        std::int64_t expected_revision,
        std::size_t object_index) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult create_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult delete_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int section) noexcept = 0;
    [[nodiscard]] virtual ObjectMutationResult move_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int section,
        int frame) noexcept = 0;
    [[nodiscard]] virtual StringCatalogResult get_font_catalog() noexcept = 0;
    [[nodiscard]] virtual PaletteCatalogResult
    get_palette_catalog() noexcept = 0;
    [[nodiscard]] virtual ModuleCatalogResult
    get_module_catalog() noexcept = 0;
    [[nodiscard]] virtual RenderedFrameResult render_frame(
        int frame) noexcept = 0;
    [[nodiscard]] virtual RenderedAudioResult render_audio(
        int frame_start,
        int frame_end) noexcept = 0;
};

class HostSdkAdapter final : public SdkAdapter {
public:
    explicit HostSdkAdapter(EDIT_HANDLE* edit_handle) noexcept;

    void set_stopping(bool stopping) noexcept override;
    [[nodiscard]] EditState get_edit_state() const noexcept override;
    [[nodiscard]] ProjectInfoResult get_project_info() noexcept override;
    [[nodiscard]] SceneInfoResult get_current_scene() noexcept override;
    [[nodiscard]] SceneInfoResult update_current_scene(
        std::int64_t expected_revision,
        const SceneUpdate& update) noexcept override;
    [[nodiscard]] EffectCatalogResult get_effect_catalog(
        std::size_t start,
        std::size_t count) noexcept override;
    [[nodiscard]] LayerSnapshotResult get_layers(
        int start,
        int count) noexcept override;
    [[nodiscard]] ObjectMutationResult update_layer(
        std::int64_t expected_revision,
        int layer,
        const std::optional<std::wstring>& name,
        const std::optional<bool>& enabled) noexcept override;
    [[nodiscard]] BatchEditResult validate_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept override;
    [[nodiscard]] BatchEditResult apply_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept override;
    [[nodiscard]] SnapshotResult get_snapshot() noexcept override;
    [[nodiscard]] ObjectMutationResult set_object_items(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::vector<ObjectItemUpdate>& updates) noexcept override;
    [[nodiscard]] ObjectMutationResult set_object_name(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::optional<std::wstring>& name) noexcept override;
    [[nodiscard]] ObjectMutationResult move_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int layer,
        int frame) noexcept override;
    [[nodiscard]] ObjectMutationResult delete_object(
        std::int64_t expected_revision,
        std::size_t object_index) noexcept override;
    [[nodiscard]] ObjectMutationResult add_object_effect(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& effect) noexcept override;
    [[nodiscard]] ObjectMutationResult add_object_effect_with_items(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& effect,
        const std::vector<EffectInitialItem>& initial_items) noexcept override;
    [[nodiscard]] ObjectMutationResult set_object_effect_enabled(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& selector,
        bool enabled) noexcept override;
    [[nodiscard]] ObjectMutationResult delete_object_effect(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::wstring& selector) noexcept override;
    [[nodiscard]] SplitMediaResult split_media_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame) noexcept override;
    [[nodiscard]] StructuralEditResult set_object_duration(
        std::int64_t expected_revision,
        std::size_t object_index,
        int duration) noexcept override;
    [[nodiscard]] StructuralEditResult trim_media_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame_start,
        int frame_end,
        const std::optional<double>& source_position) noexcept override;
    [[nodiscard]] StructuralEditResult reorder_object_effects(
        std::int64_t expected_revision,
        std::size_t object_index,
        const std::vector<std::wstring>& selectors) noexcept override;
    [[nodiscard]] TimelineTransactionResult
    run_timeline_transaction(
        std::int64_t expected_revision,
        const std::vector<TimelineCommand>& commands,
        bool apply) noexcept override;
    [[nodiscard]] EditPlanResult run_edit_plan(
        std::int64_t expected_revision,
        const std::vector<EditPlanCommand>& commands,
        bool apply) noexcept override;
    [[nodiscard]] MediaProbeResult probe_media(
        const std::wstring& file) noexcept override;
    [[nodiscard]] CreateMediaResult create_object_from_media_file(
        const std::wstring& file,
        int layer,
        int frame,
        int length) noexcept override;
    [[nodiscard]] ObjectInspectionResult inspect_object(
        std::int64_t expected_revision,
        std::size_t object_index,
        int sample_frame) noexcept override;
    [[nodiscard]] ObjectSectionsResult get_object_sections(
        std::int64_t expected_revision,
        std::size_t object_index) noexcept override;
    [[nodiscard]] ObjectMutationResult create_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int frame) noexcept override;
    [[nodiscard]] ObjectMutationResult delete_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int section) noexcept override;
    [[nodiscard]] ObjectMutationResult move_object_section(
        std::int64_t expected_revision,
        std::size_t object_index,
        int section,
        int frame) noexcept override;
    [[nodiscard]] StringCatalogResult get_font_catalog() noexcept override;
    [[nodiscard]] PaletteCatalogResult
    get_palette_catalog() noexcept override;
    [[nodiscard]] ModuleCatalogResult
    get_module_catalog() noexcept override;
    [[nodiscard]] RenderedFrameResult render_frame(
        int frame) noexcept override;
    [[nodiscard]] RenderedAudioResult render_audio(
        int frame_start,
        int frame_end) noexcept override;

private:
    EDIT_HANDLE* edit_handle_;
    std::atomic_bool stopping_ = false;
};

[[nodiscard]] std::string edit_state_name(EditState state);

}  // namespace aviutl2::live
