#include "api_lock.hpp"
#include "alias_tools.hpp"
#include "bridge_constants.hpp"
#include "command_dispatcher.hpp"
#include "host_version.hpp"
#include "instance_registry.hpp"
#include "json.hpp"
#include "pipe_server.hpp"
#include "protocol.hpp"
#include "sdk_adapter.hpp"

#include <windows.h>
#include <plugin2.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>

namespace {

using aviutl2::live::CommandDispatcher;
using aviutl2::live::BatchEditResult;
using aviutl2::live::CatalogEffect;
using aviutl2::live::CatalogItem;
using aviutl2::live::CreateAliasCommand;
using aviutl2::live::EditState;
using aviutl2::live::EditPlanCommand;
using aviutl2::live::EditPlanEffectScope;
using aviutl2::live::EditPlanResult;
using aviutl2::live::EffectCatalogResult;
using aviutl2::live::EffectInitialItem;
using aviutl2::live::FrameMark;
using aviutl2::live::FrameMarksResult;
using aviutl2::live::MarkEditResult;
using aviutl2::live::Json;
using aviutl2::live::LayerInfo;
using aviutl2::live::LayerSnapshotResult;
using aviutl2::live::CreateMediaResult;
using aviutl2::live::InspectedEffect;
using aviutl2::live::InspectedItem;
using aviutl2::live::InspectedTrack;
using aviutl2::live::MediaInfo;
using aviutl2::live::MediaProbeResult;
using aviutl2::live::ObjectInspectionResult;
using aviutl2::live::ObjectItemUpdate;
using aviutl2::live::ObjectMutationResult;
using aviutl2::live::ObjectSectionsResult;
using aviutl2::live::PaletteCatalogResult;
using aviutl2::live::ModuleCatalogResult;
using aviutl2::live::StringCatalogResult;
using aviutl2::live::StructuralEditResult;
using aviutl2::live::TimelineCommand;
using aviutl2::live::TimelineTransactionResult;
using aviutl2::live::PipeServer;
using aviutl2::live::ProjectInfo;
using aviutl2::live::ProjectInfoResult;
using aviutl2::live::ProjectMutationResult;
using aviutl2::live::RenderedAudioResult;
using aviutl2::live::RenderedFrameResult;
using aviutl2::live::SdkAdapter;
using aviutl2::live::HostSdkAdapter;
using aviutl2::live::SceneInfoResult;
using aviutl2::live::SceneUpdate;
using aviutl2::live::SceneListItem;
using aviutl2::live::SceneListResult;
using aviutl2::live::SceneCreateCommand;
using aviutl2::live::ProjectCreateCommand;
using aviutl2::live::ExportStartCommand;
using aviutl2::live::ExportStartResult;
using aviutl2::live::ObjectFlagKind;
using aviutl2::live::ObjectFlagResult;
using aviutl2::live::StableIdResult;
using aviutl2::live::SnapshotObject;
using aviutl2::live::SnapshotResult;
using aviutl2::live::SplitMediaResult;

void require(const bool condition, const std::string_view message) {
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

[[nodiscard]] std::string read_file(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("fixture could not be opened");
    }
    return {
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>(),
    };
}

[[nodiscard]] bool json_equal(
    const std::string_view left,
    const std::string_view right) {
    return aviutl2::live::serialize_json(aviutl2::live::parse_json(left)) ==
           aviutl2::live::serialize_json(aviutl2::live::parse_json(right));
}

class FakeSdkAdapter final : public SdkAdapter {
public:
    [[nodiscard]] EditState get_edit_state() const noexcept override {
        return state;
    }

    [[nodiscard]] ProjectInfoResult get_project_info() noexcept override {
        return project;
    }

    [[nodiscard]] SceneInfoResult get_current_scene() noexcept override {
        return scene_result;
    }

    [[nodiscard]] SceneInfoResult update_current_scene(
        const std::int64_t expected_revision,
        const SceneUpdate&) noexcept override {
        last_revision = expected_revision;
        return scene_result;
    }

    SceneListResult list_scenes_result;
    ProjectMutationResult project_mutation_result;
    ExportStartResult export_result;
    ObjectFlagResult flag_result;
    StableIdResult stable_id_result;
    std::wstring last_effect_name;
    ObjectFlagKind last_flag_kind = ObjectFlagKind::enable_group;
    bool last_flag_value = false;
    int last_scene_id = -1;
    bool last_show_confirm = false;

    [[nodiscard]] SceneListResult list_scenes() noexcept override {
        ++scene_list_calls;
        return list_scenes_result;
    }

    [[nodiscard]] ProjectMutationResult create_scene(
        const SceneCreateCommand&) noexcept override {
        ++scene_create_calls;
        return project_mutation_result;
    }

    [[nodiscard]] ProjectMutationResult switch_scene(
        const int scene_id) noexcept override {
        last_scene_id = scene_id;
        return project_mutation_result;
    }

    [[nodiscard]] ProjectMutationResult create_project(
        const ProjectCreateCommand&) noexcept override {
        ++project_create_calls;
        return project_mutation_result;
    }

    [[nodiscard]] ProjectMutationResult open_project_file(
        const std::wstring&,
        const bool show_confirm) noexcept override {
        ++project_open_calls;
        last_show_confirm = show_confirm;
        return project_mutation_result;
    }

    [[nodiscard]] ProjectMutationResult save_project_file(
        const std::wstring&) noexcept override {
        ++project_save_calls;
        return project_mutation_result;
    }

    [[nodiscard]] ExportStartResult start_export(
        const ExportStartCommand&) noexcept override {
        ++export_calls;
        return export_result;
    }

    [[nodiscard]] ObjectFlagResult get_object_flag(
        const std::int64_t expected_revision,
        const std::size_t,
        const ObjectFlagKind kind) noexcept override {
        last_revision = expected_revision;
        last_flag_kind = kind;
        return flag_result;
    }

    [[nodiscard]] ProjectMutationResult set_object_flag(
        const std::int64_t expected_revision,
        const std::size_t,
        const ObjectFlagKind kind,
        const bool flag) noexcept override {
        last_revision = expected_revision;
        last_flag_kind = kind;
        last_flag_value = flag;
        return project_mutation_result;
    }

    [[nodiscard]] StableIdResult get_object_id(
        const std::int64_t expected_revision,
        const std::size_t) noexcept override {
        last_revision = expected_revision;
        return stable_id_result;
    }

    [[nodiscard]] StableIdResult get_effect_id(
        const std::int64_t expected_revision,
        const std::size_t,
        const std::wstring& effect) noexcept override {
        last_revision = expected_revision;
        last_effect_name = effect;
        return stable_id_result;
    }

    [[nodiscard]] EffectCatalogResult get_effect_catalog(
        const std::size_t start,
        const std::size_t count) noexcept override {
        last_page_start = start;
        last_page_count = count;
        ++catalog_calls;
        return catalog_result;
    }

    [[nodiscard]] LayerSnapshotResult get_layers(
        const int start,
        const int count) noexcept override {
        last_layer = start;
        last_length = count;
        ++layers_calls;
        return layers_result;
    }

    [[nodiscard]] ObjectMutationResult update_layer(
        const std::int64_t expected_revision,
        const int layer,
        const std::optional<std::wstring>&,
        const std::optional<bool>&) noexcept override {
        last_revision = expected_revision;
        last_layer = layer;
        return mutation_result;
    }

    [[nodiscard]] BatchEditResult validate_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept override {
        last_commands = commands;
        ++validate_calls;
        BatchEditResult result = validate_result;
        return result;
    }

    [[nodiscard]] BatchEditResult apply_create_alias_batch(
        const std::vector<CreateAliasCommand>& commands) noexcept override {
        last_commands = commands;
        ++apply_calls;
        BatchEditResult result = apply_result;
        if (result.ok) {
            result.applied_count = commands.size();
        }
        return result;
    }

    [[nodiscard]] SnapshotResult get_snapshot() noexcept override {
        ++snapshot_calls;
        return snapshot;
    }

    [[nodiscard]] ObjectMutationResult set_object_items(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::vector<ObjectItemUpdate>& updates) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_updates = updates;
        ++set_items_calls;
        ObjectMutationResult result = mutation_result;
        if (result.ok) {
            result.applied_count = updates.size();
        }
        return result;
    }

    [[nodiscard]] ObjectMutationResult set_object_name(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::optional<std::wstring>&) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        ++set_name_calls;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult move_object(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int layer,
        const int frame) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_layer = layer;
        last_frame = frame;
        ++move_calls;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult delete_object(
        const std::int64_t expected_revision,
        const std::size_t object_index) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        ++delete_calls;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult add_object_effect(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::wstring& effect) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_effect = effect;
        ++add_effect_calls;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult add_object_effect_with_items(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::wstring& effect,
        const std::vector<EffectInitialItem>&) noexcept override {
        return add_object_effect(
            expected_revision,
            object_index,
            effect);
    }

    [[nodiscard]] ObjectMutationResult set_object_effect_enabled(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::wstring& selector,
        const bool) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_effect = selector;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult delete_object_effect(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::wstring& selector) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_effect = selector;
        ++delete_effect_calls;
        return mutation_result;
    }

    [[nodiscard]] SplitMediaResult split_media_object(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int frame) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_frame = frame;
        ++split_calls;
        return split_result;
    }

    [[nodiscard]] StructuralEditResult set_object_duration(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int duration) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_length = duration;
        return structural_result;
    }

    [[nodiscard]] StructuralEditResult trim_media_object(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int frame_start,
        const int frame_end,
        const std::optional<double>&) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_frame = frame_start;
        last_length = frame_end;
        return structural_result;
    }

    [[nodiscard]] StructuralEditResult reorder_object_effects(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const std::vector<std::wstring>&) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        return structural_result;
    }

    [[nodiscard]] TimelineTransactionResult
    run_timeline_transaction(
        const std::int64_t expected_revision,
        const std::vector<TimelineCommand>& commands,
        const bool apply) noexcept override {
        last_revision = expected_revision;
        TimelineTransactionResult result = transaction_result;
        result.applied_count = apply ? commands.size() : 0U;
        return result;
    }

    [[nodiscard]] EditPlanResult run_edit_plan(
        const std::int64_t expected_revision,
        const std::vector<EditPlanCommand>& commands,
        const bool apply) noexcept override {
        last_revision = expected_revision;
        last_plan_commands = commands;
        last_plan_apply = apply;
        ++plan_calls;
        EditPlanResult result = plan_result;
        result.applied_count = apply ? commands.size() : 0U;
        return result;
    }

    [[nodiscard]] MediaProbeResult probe_media(
        const std::wstring& file) noexcept override {
        last_file = file;
        ++probe_media_calls;
        return media_probe_result;
    }

    [[nodiscard]] CreateMediaResult create_object_from_media_file(
        const std::wstring& file,
        const int layer,
        const int frame,
        const int length) noexcept override {
        last_file = file;
        last_layer = layer;
        last_frame = frame;
        last_length = length;
        ++create_media_calls;
        if (create_media_result.ok) {
            snapshot.revision += 1;
            for (std::size_t index = 0U;
                 index < snapshot.objects.size();
                 ++index) {
                snapshot.objects[index].object_id =
                    "obj-" +
                    std::to_string(snapshot.revision) + "-" +
                    std::to_string(index);
            }
            snapshot.objects.push_back(SnapshotObject{
                "obj-" +
                    std::to_string(snapshot.revision) + "-" +
                    std::to_string(snapshot.objects.size()),
                create_media_result.layer,
                create_media_result.frame_start,
                create_media_result.frame_end,
                false,
                {},
                "[Object]\r\n[Object.0]\r\neffect.name=Media\r\n",
            });
        }
        return create_media_result;
    }

    [[nodiscard]] ObjectInspectionResult inspect_object(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int sample_frame) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_frame = sample_frame;
        ++inspect_calls;
        return inspection_result;
    }

    [[nodiscard]] ObjectSectionsResult get_object_sections(
        const std::int64_t expected_revision,
        const std::size_t object_index) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        return sections_result;
    }

    [[nodiscard]] ObjectMutationResult create_object_section(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int frame) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_frame = frame;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult delete_object_section(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int section) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_frame = section;
        return mutation_result;
    }

    [[nodiscard]] ObjectMutationResult move_object_section(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int section,
        const int frame) noexcept override {
        last_revision = expected_revision;
        last_object_index = object_index;
        last_length = section;
        last_frame = frame;
        return mutation_result;
    }

    [[nodiscard]] StringCatalogResult
    get_font_catalog() noexcept override {
        return font_catalog_result;
    }

    [[nodiscard]] PaletteCatalogResult
    get_palette_catalog() noexcept override {
        return palette_catalog_result;
    }

    [[nodiscard]] ModuleCatalogResult
    get_module_catalog() noexcept override {
        return module_catalog_result;
    }

    [[nodiscard]] RenderedFrameResult render_frame(
        const int frame) noexcept override {
        ++render_calls;
        last_frame = frame;
        RenderedFrameResult result = render_result;
        result.frame = frame;
        return result;
    }

    [[nodiscard]] RenderedAudioResult render_audio(
        const int frame_start,
        const int frame_end) noexcept override {
        ++audio_render_calls;
        RenderedAudioResult result = audio_render_result;
        result.frame_start = frame_start;
        result.frame_end = frame_end;
        return result;
    }

    [[nodiscard]] RenderedFrameResult render_object_frame(
        const std::int64_t expected_revision,
        const std::size_t object_index,
        const int frame,
        const bool apply_effect) noexcept override {
        ++object_render_calls;
        last_object_index = object_index;
        last_apply_effect = apply_effect;
        RenderedFrameResult result = render_result;
        result.frame = frame;
        if (expected_revision != 123) {
            result.ok = false;
            result.error_code = "STALE_PROJECT_STATE";
            result.error_message = "The project changed.";
            return result;
        }
        if (object_render_fail_stale) {
            result.ok = false;
            result.error_code = "STALE_PROJECT_STATE";
            result.error_message = "The project changed.";
        }
        return result;
    }

    [[nodiscard]] RenderedAudioResult render_object_audio(
        const std::int64_t /*expected_revision*/,
        const std::size_t object_index,
        const int frame_start,
        const int frame_end,
        const bool apply_effect) noexcept override {
        ++object_audio_render_calls;
        last_object_index = object_index;
        last_apply_effect = apply_effect;
        RenderedAudioResult result = audio_render_result;
        result.frame_start = frame_start;
        result.frame_end = frame_end;
        return result;
    }


    [[nodiscard]] FrameMarksResult get_frame_marks() noexcept override {
        return frame_marks_result;
    }

    [[nodiscard]] MarkEditResult set_frame_mark(
        const std::int64_t expected_revision,
        const int frame,
        const std::wstring& memo) noexcept override {
        ++mark_edit_calls;
        MarkEditResult result = mark_edit_result;
        result.current_revision = expected_revision;
        result.frame = frame;
        if (result.ok && result.has_memo) {
            result.memo.clear();
            result.memo.reserve(memo.size());
            for (const wchar_t character : memo) {
                result.memo.push_back(static_cast<char>(character));
            }
        }
        return result;
    }

    [[nodiscard]] MarkEditResult clear_frame_mark(
        const std::int64_t expected_revision,
        const int frame) noexcept override {
        ++mark_edit_calls;
        MarkEditResult result = mark_edit_result;
        result.current_revision = expected_revision;
        result.frame = frame;
        return result;
    }

    [[nodiscard]] MarkEditResult move_frame_mark(
        const std::int64_t expected_revision,
        const int frame,
        const int frame_to) noexcept override {
        static_cast<void>(frame);
        ++mark_edit_calls;
        MarkEditResult result = mark_edit_result;
        result.current_revision = expected_revision;
        result.frame = frame_to;
        return result;
    }

    EditState state = EditState::edit;
    ProjectInfoResult project{
        true,
        ProjectInfo{
            7,
            1920,
            1080,
            30000,
            1001,
            48000,
            12,
            3,
            240,
            8,
            "C:\\Projects\\current.aup2"},
        {},
        {},
        false,
    };
    SceneInfoResult scene_result{
        true,
        123,
        {7, "Root", 1920, 1080, 30000, 1001, 48000},
    };
    EffectCatalogResult catalog_result{
        true,
        0U,
        1U,
        {
            CatalogEffect{
                "動画ファイル",
                2,
                3,
                {
                    CatalogItem{"再生速度", 2},
                    CatalogItem{"ファイル", 6},
                },
            },
        },
        {},
        {},
        false,
    };
    LayerSnapshotResult layers_result{
        true,
        123,
        7,
        8,
        0,
        10,
        0,
        {
            LayerInfo{0, true, "Video", true, false, 1U},
            LayerInfo{1, false, "", false, true, 0U},
        },
        {},
        {},
        false,
    };
    BatchEditResult validate_result{true};
    BatchEditResult apply_result{true};
    SnapshotResult snapshot{
        true,
        123,
        7,
        {
            SnapshotObject{
                "obj-123-0",
                2,
                10,
                39,
                true,
                "Title",
                "[Object]\r\n[Object.0]\r\neffect.name=Text\r\n",
            },
        },
        {},
        {},
        false,
    };
    ObjectMutationResult mutation_result{true, 124, 1U};
    SplitMediaResult split_result{
        true,
        124,
        2,
        10,
        24,
        25,
        39,
        0.0,
        15.0,
        1.0,
    };
    StructuralEditResult structural_result{
        true,
        124,
        2,
        10,
        39,
    };
    TimelineTransactionResult transaction_result{
        true,
        true,
        124,
    };
    EditPlanResult plan_result{true, true, 124, 2U};
    MediaProbeResult media_probe_result{
        true,
        MediaInfo{true, true, true, 1, 0, 2.5, 1280, 720},
    };
    CreateMediaResult create_media_result{true, 4, 20, 94};
    ObjectInspectionResult inspection_result{
        true,
        123,
        10,
        {
            InspectedEffect{
                0,
                0,
                "標準描画",
                "標準描画",
                true,
                false,
                {
                    InspectedItem{
                        "X",
                        2,
                        true,
                        "100.0",
                        InspectedTrack{
                            true,
                            true,
                            "直線移動",
                            {100.0, 200.0},
                            false,
                            false,
                            false,
                            false,
                            1,
                            0,
                            false,
                            {},
                            true,
                            100.0,
                        },
                    },
                },
            },
        },
    };
    RenderedFrameResult render_result{
        true,
        0,
        2,
        1,
        {
            255, 0, 0, 255,
            0, 255, 0, 255,
        },
    };
    RenderedAudioResult audio_render_result{
        true,
        0,
        0,
        48000,
        {0.25F, -0.25F, 0.5F, -0.5F},
    };
    ObjectSectionsResult sections_result{
        true,
        123,
        {{0, 10}},
    };
    StringCatalogResult font_catalog_result{
        true,
        {"Arial"},
    };
    PaletteCatalogResult palette_catalog_result{true};
    ModuleCatalogResult module_catalog_result{true};
    std::vector<CreateAliasCommand> last_commands;
    std::vector<ObjectItemUpdate> last_updates;
    std::vector<EditPlanCommand> last_plan_commands;
    std::int64_t last_revision = 0;
    FrameMarksResult frame_marks_result;
    MarkEditResult mark_edit_result;
    int mark_edit_calls = 0;
    std::size_t last_object_index = 0U;
    std::size_t last_page_start = 0U;
    std::size_t last_page_count = 0U;
    int last_layer = -1;
    int last_frame = -1;
    int last_length = -1;
    std::wstring last_file;
    std::wstring last_effect;
    int validate_calls = 0;
    int apply_calls = 0;
    int catalog_calls = 0;
    int layers_calls = 0;
    int scene_list_calls = 0;
    int scene_create_calls = 0;
    int project_create_calls = 0;
    int project_open_calls = 0;
    int project_save_calls = 0;
    int export_calls = 0;
    int snapshot_calls = 0;
    int set_items_calls = 0;
    int set_name_calls = 0;
    int move_calls = 0;
    int delete_calls = 0;
    int add_effect_calls = 0;
    int delete_effect_calls = 0;
    int split_calls = 0;
    int probe_media_calls = 0;
    int create_media_calls = 0;
    int inspect_calls = 0;
    int render_calls = 0;
    int audio_render_calls = 0;

    int object_render_calls = 0;
    int object_audio_render_calls = 0;
    bool last_apply_effect = false;
    bool object_render_fail_stale = false;

    int plan_calls = 0;
    bool last_plan_apply = false;
};

struct HostObjectFixture final {
    OBJECT_LAYER_FRAME range{};
    int occupied_layer_end = 0;
    const char* alias = nullptr;
};

std::array<HostObjectFixture, 3> host_object_fixtures{{
    {{0, 10, 39}, 1, "[Object]\r\n[Object.0]\r\neffect.name=Text\r\n"},
    {{1, 40, 49}, 1, "[Object]\r\n[Object.0]\r\neffect.name=Shape\r\n"},
    {{2, 50, 69}, 2, "[Object]\r\n[Object.0]\r\neffect.name=Audio\r\n"},
}};

EDIT_INFO host_edit_info{};
EDIT_SECTION host_edit_section{};

[[nodiscard]] OBJECT_HANDLE find_host_object(
    const int layer,
    const int frame) {
    for (HostObjectFixture& fixture : host_object_fixtures) {
        if (fixture.range.layer <= layer &&
            layer <= fixture.occupied_layer_end &&
            fixture.range.end >= frame) {
            return static_cast<OBJECT_HANDLE>(&fixture);
        }
    }
    return nullptr;
}

[[nodiscard]] OBJECT_LAYER_FRAME get_host_object_range(
    const OBJECT_HANDLE object) {
    return static_cast<HostObjectFixture*>(object)->range;
}

[[nodiscard]] LPCSTR get_host_object_alias(const OBJECT_HANDLE object) {
    return static_cast<HostObjectFixture*>(object)->alias;
}

void get_host_edit_info(EDIT_INFO* info, const int info_size) {
    require(
        info_size == static_cast<int>(sizeof(EDIT_INFO)),
        "host adapter should request the complete edit information");
    *info = host_edit_info;
}

[[nodiscard]] int get_host_edit_state() {
    return EDIT_HANDLE::EDIT_STATE_EDIT;
}

[[nodiscard]] bool call_host_read_section(
    void* parameter,
    void (*callback)(void*, EDIT_SECTION*)) {
    callback(parameter, &host_edit_section);
    return true;
}

void test_host_snapshot_with_multi_layer_object() {
    host_edit_info = {};
    host_edit_info.width = 1920;
    host_edit_info.height = 1080;
    host_edit_info.rate = 30;
    host_edit_info.scale = 1;
    host_edit_info.sample_rate = 44100;
    host_edit_info.frame_max = 69;
    host_edit_info.layer_max = 2;
    host_edit_info.scene_id = 7;

    host_edit_section = {};
    host_edit_section.find_object = find_host_object;
    host_edit_section.get_object_layer_frame = get_host_object_range;
    host_edit_section.get_object_alias = get_host_object_alias;

    EDIT_HANDLE edit_handle{};
    edit_handle.get_edit_info = get_host_edit_info;
    edit_handle.get_edit_state = get_host_edit_state;
    edit_handle.call_read_section_param = call_host_read_section;

    HostSdkAdapter adapter(&edit_handle);
    const SnapshotResult first = adapter.get_snapshot();
    require(first.ok, "multi-layer host snapshot should succeed");
    require(
        first.objects.size() == host_object_fixtures.size(),
        "multi-layer objects should be captured exactly once");
    require(
        first.objects[0].layer == 0 &&
            first.objects[0].frame_start == 10 &&
            first.objects[0].frame_end == 39,
        "multi-layer object should retain its base range");
    require(
        first.objects[1].layer == 1 && first.objects[2].layer == 2,
        "snapshot objects should remain in base-layer order");

    const SnapshotResult second = adapter.get_snapshot();
    require(second.ok, "repeated multi-layer snapshot should succeed");
    require(
        second.revision == first.revision,
        "unchanged multi-layer snapshots should have a stable revision");
}

void test_json() {
    const Json value = aviutl2::live::parse_json(
        R"({"emoji":"\ud83d\ude00","日本語":true,"values":[null,-2,1.25]})");
    require(value.is_object(), "JSON root should be an object");
    require(
        value.find("emoji") != nullptr &&
            value.find("emoji")->as_string() == "\xF0\x9F\x98\x80",
        "surrogate pair should decode as UTF-8");
    require(
        json_equal(
            aviutl2::live::serialize_json(value),
            R"({"emoji":"😀","values":[null,-2,1.25],"日本語":true})"),
        "serialized JSON should preserve values");

    bool duplicate_rejected = false;
    try {
        static_cast<void>(aviutl2::live::parse_json(R"({"a":1,"a":2})"));
    } catch (const aviutl2::live::JsonParseError&) {
        duplicate_rejected = true;
    }
    require(duplicate_rejected, "duplicate keys should be rejected");
    require(
        !aviutl2::live::is_valid_utf8(std::string("\xC0\xAF", 2U)),
        "overlong UTF-8 should be rejected");
    require(
        aviutl2::live::is_api_locked_name(L"\U0001F512") &&
            aviutl2::live::is_api_locked_name(
                L"\U0001F512 Custom title") &&
            aviutl2::live::is_api_locked_name(
                L"\U0001F512\u2009Derived title") &&
            aviutl2::live::is_derived_api_lock_name(
                L"\U0001F512\u2009Derived title") &&
            !aviutl2::live::is_derived_api_lock_name(
                L"\U0001F512 Custom title") &&
            !aviutl2::live::is_api_locked_name(L"Custom title"),
        "API lock marker and name provenance detection should be exact");

    const std::string media_alias =
        "[Object]\r\n"
        "frame=0,89\r\n"
        "[Object.0]\r\n"
        "effect.name=音声ファイル\r\n"
        "再生位置=0.000,0.000,再生範囲,0\r\n";
    require(
        aviutl2::live::strip_object_alias_frame_range(media_alias) ==
            "[Object]\r\n"
            "[Object.0]\r\n"
            "effect.name=音声ファイル\r\n"
            "再生位置=0.000,0.000,再生範囲,0\r\n",
        "split Alias normalization should remove only the top-level frame range");

    const std::string effect_alias =
        "[Object]\r\n"
        "frame=10,39\r\n"
        "[Object.0]\r\n"
        "effect.name=Input\r\n"
        "file=C:\\media\\clip.mp4\r\n"
        "[Object.1]\r\n"
        "effect.name=Blur\r\n"
        "amount=25.000,25.000,Linear,0\r\n"
        "enabled=1\r\n"
        "[Object.2]\r\n"
        "effect.name=Color\r\n"
        "color=ff00ff\r\n";
    const std::string reordered =
        aviutl2::live::reorder_object_alias_effects(
            effect_alias,
            {0U, 2U, 1U});
    require(
        reordered.find("frame=") == std::string::npos &&
            reordered.find(
                "[Object.1]\r\neffect.name=Color\r\n"
                "color=ff00ff\r\n") != std::string::npos &&
            reordered.find(
                "[Object.2]\r\neffect.name=Blur\r\n"
                "amount=25.000,25.000,Linear,0\r\n"
                "enabled=1\r\n") != std::string::npos &&
            reordered.find("file=C:\\media\\clip.mp4") !=
                std::string::npos,
        "effect reorder should preserve every non-order Alias value");
    const std::string replaced =
        aviutl2::live::replace_object_alias_effect_item(
            effect_alias,
            1U,
            "amount",
            "50.000,50.000,Linear,0");
    require(
        replaced.find(
            "amount=50.000,50.000,Linear,0") !=
                std::string::npos &&
            replaced.find("enabled=1") != std::string::npos &&
            replaced.find("color=ff00ff") != std::string::npos,
        "structural item replacement should preserve unrelated values");
}

struct HostEffectSlot {
    const wchar_t* name;
    int type;
};

std::array<HostEffectSlot, 4> host_effect_slots{{
    {L"Blur", 1},
    {L"Tone", 1},
    {L"Zoom", 1},
    {L"SceneChange", 3},
}};

HostObjectFixture host_reorder_fixture{
    {1, 0, 59},
    1,
    "[Object]\r\n[Object.0]\r\neffect.name=Text\r\n"};

EDIT_INFO host_reorder_edit_info{};
EDIT_SECTION host_reorder_edit_section{};
std::vector<EFFECT_HANDLE> host_live_effects;
int host_move_effect_calls = 0;
int host_move_effect_fail_on_call = 0;

[[nodiscard]] OBJECT_HANDLE find_host_reorder_object(
    const int layer, const int frame) {
    if (host_reorder_fixture.range.layer <= layer &&
        layer <= host_reorder_fixture.occupied_layer_end &&
        host_reorder_fixture.range.end >= frame) {
        return static_cast<OBJECT_HANDLE>(&host_reorder_fixture);
    }
    return nullptr;
}

void get_host_reorder_edit_info(EDIT_INFO* info, const int info_size) {
    require(
        info_size == static_cast<int>(sizeof(EDIT_INFO)),
        "reorder host adapter should request complete edit information");
    *info = host_reorder_edit_info;
}

[[nodiscard]] int get_host_reorder_edit_state() {
    return EDIT_HANDLE::EDIT_STATE_EDIT;
}

[[nodiscard]] bool call_host_reorder_section(
    void* parameter, void (*callback)(void*, EDIT_SECTION*)) {
    callback(parameter, &host_reorder_edit_section);
    return true;
}

[[nodiscard]] int get_host_reorder_section_num(OBJECT_HANDLE) {
    return 1;
}

[[nodiscard]] int host_get_effect_list_for_reorder(
    OBJECT_HANDLE, EFFECT_HANDLE* effect_list, const int effect_num) {
    const int count = static_cast<int>(host_live_effects.size());
    if (effect_list == nullptr) {
        return count;
    }
    if (effect_num < count) {
        return -1;
    }
    for (int index = 0; index < count; ++index) {
        effect_list[index] =
            host_live_effects[static_cast<std::size_t>(index)];
    }
    return count;
}

[[nodiscard]] LPCWSTR host_get_effect_name_for_reorder(
    const EFFECT_HANDLE effect) {
    return static_cast<const HostEffectSlot*>(effect)->name;
}

[[nodiscard]] int host_move_effect(
    OBJECT_HANDLE, EFFECT_HANDLE effect, const int index) {
    ++host_move_effect_calls;
    const auto found = std::find(
        host_live_effects.begin(), host_live_effects.end(), effect);
    if (found == host_live_effects.end() ||
        static_cast<const HostEffectSlot*>(effect)->type != 1 ||
        host_move_effect_calls == host_move_effect_fail_on_call ||
        index < 0 ||
        static_cast<std::size_t>(index) > host_live_effects.size()) {
        return -1;
    }
    host_live_effects.erase(found);
    host_live_effects.insert(host_live_effects.begin() + index, effect);
    return index;
}

void host_enum_effect_name_for_reorder(
    void* parameter,
    void (*callback)(void*, LPCWSTR, int, int)) {
    for (const HostEffectSlot& slot : host_effect_slots) {
        callback(parameter, slot.name, slot.type, 0);
    }
}

void host_reset_reorder_effects() {
    host_live_effects = {
        &host_effect_slots[0],
        &host_effect_slots[1],
        &host_effect_slots[2],
    };
}

void test_host_native_effect_reorder() {
    host_reset_reorder_effects();
    host_move_effect_calls = 0;
    host_move_effect_fail_on_call = 0;

    host_reorder_edit_info = {};
    host_reorder_edit_info.width = 1920;
    host_reorder_edit_info.height = 1080;
    host_reorder_edit_info.rate = 30;
    host_reorder_edit_info.scale = 1;
    host_reorder_edit_info.sample_rate = 44100;
    host_reorder_edit_info.frame_max = 59;
    host_reorder_edit_info.layer_max = 1;
    host_reorder_edit_info.scene_id = 9;

    host_reorder_edit_section = {};
    host_reorder_edit_section.find_object = find_host_reorder_object;
    host_reorder_edit_section.get_object_layer_frame =
        get_host_object_range;
    host_reorder_edit_section.get_object_alias = get_host_object_alias;
    host_reorder_edit_section.get_object_section_num =
        get_host_reorder_section_num;
    host_reorder_edit_section.get_effect_list =
        host_get_effect_list_for_reorder;
    host_reorder_edit_section.get_effect_name =
        host_get_effect_name_for_reorder;
    host_reorder_edit_section.move_effect = host_move_effect;

    EDIT_HANDLE edit_handle{};
    edit_handle.get_edit_info = get_host_reorder_edit_info;
    edit_handle.get_edit_state = get_host_reorder_edit_state;
    edit_handle.call_read_section_param = call_host_reorder_section;
    edit_handle.call_edit_section_param = call_host_reorder_section;
    edit_handle.enum_effect_name = host_enum_effect_name_for_reorder;

    HostSdkAdapter adapter(&edit_handle);
    const SnapshotResult snapshot = adapter.get_snapshot();
    require(
        snapshot.ok && snapshot.objects.size() == 1U,
        "the reorder host fixture should expose one object");

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionMoveEffect);
    {
        const StructuralEditResult moved = adapter.reorder_object_effects(
            snapshot.revision, 0U, {L"Tone", L"Zoom", L"Blur"});
        require(
            moved.ok && moved.native_backend,
            "a 2.1.3 host should reorder filter effects natively");
        require(
            moved.layer == 1 && moved.frame_start == 0 &&
                moved.frame_end == 59,
            "the native reorder should preserve the object placement");
        require(
            moved.effect_order.size() == 3U &&
                moved.effect_order[0] == "Tone" &&
                moved.effect_order[1] == "Zoom" &&
                moved.effect_order[2] == "Blur",
            "the native reorder should report the requested order");
        require(
            host_move_effect_calls == 2,
            "the native reorder should issue exactly two moves");
        require(
            host_live_effects.size() == 3U &&
                host_live_effects[0] == &host_effect_slots[1] &&
                host_live_effects[1] == &host_effect_slots[2] &&
                host_live_effects[2] == &host_effect_slots[0],
            "the host effect order should match the request");
    }

    host_reset_reorder_effects();
    host_move_effect_calls = 0;
    host_move_effect_fail_on_call = 2;
    {
        const StructuralEditResult moved = adapter.reorder_object_effects(
            snapshot.revision, 0U, {L"Tone", L"Zoom", L"Blur"});
        require(
            !moved.ok && moved.native_backend &&
                moved.error_code == "STRUCTURAL_EDIT_FAILED",
            "a rejected native move should fail closed");
        require(
            host_live_effects.size() == 3U &&
                host_live_effects[0] == &host_effect_slots[0] &&
                host_live_effects[1] == &host_effect_slots[1] &&
                host_live_effects[2] == &host_effect_slots[2],
            "a rejected native move should restore the original order");
    }

    host_reset_reorder_effects();
    host_move_effect_calls = 0;
    host_move_effect_fail_on_call = 0;
    aviutl2::live::store_host_version(0U);
    {
        const StructuralEditResult moved = adapter.reorder_object_effects(
            snapshot.revision, 0U, {L"Zoom", L"Blur", L"Tone"});
        require(
            !moved.ok &&
                moved.error_code == "EDIT_SECTION_UNAVAILABLE" &&
                host_move_effect_calls == 0,
            "an unrecorded host version should keep the Alias path");
    }

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionMoveEffect);
    host_live_effects = {
        &host_effect_slots[0],
        &host_effect_slots[1],
        &host_effect_slots[3],
    };
    host_move_effect_calls = 0;
    {
        const StructuralEditResult moved = adapter.reorder_object_effects(
            snapshot.revision, 0U, {L"SceneChange", L"Blur", L"Tone"});
        require(
            !moved.ok &&
                moved.error_code == "EDIT_SECTION_UNAVAILABLE" &&
                host_move_effect_calls == 0,
            "a non-filter mover should keep the Alias path");
    }

    aviutl2::live::store_host_version(0U);
}

HostObjectFixture host_trim_fixture{
    {1, 0, 99},
    1,
    "[Object]\r\n[Object.0]\r\neffect.name=動画ファイル\r\n"
    "file=C:\\media\\clip.mp4\r\n"
    "再生位置=10.000000\r\n"
    "再生速度=100.000000\r\n"};

HostEffectSlot host_trim_effect_slot{L"動画ファイル", 2};
EDIT_INFO host_trim_edit_info{};
EDIT_SECTION host_trim_edit_section{};
std::string host_trim_position = "10.000000";
std::string host_trim_speed = "100.000000";
std::vector<std::array<int, 2>> host_trim_moves;
int host_trim_move_calls = 0;
int host_trim_move_fail_on_call = 0;
int host_trim_set_calls = 0;
bool host_trim_set_fail = false;

[[nodiscard]] OBJECT_HANDLE find_host_trim_object(
    const int layer,
    const int frame) {
    if (host_trim_fixture.range.layer <= layer &&
        layer <= host_trim_fixture.occupied_layer_end &&
        host_trim_fixture.range.end >= frame) {
        return static_cast<OBJECT_HANDLE>(&host_trim_fixture);
    }
    return nullptr;
}

[[nodiscard]] OBJECT_LAYER_FRAME get_host_trim_object_range(
    const OBJECT_HANDLE object) {
    return static_cast<HostObjectFixture*>(object)->range;
}

[[nodiscard]] LPCSTR get_host_trim_object_alias(
    const OBJECT_HANDLE object) {
    return static_cast<HostObjectFixture*>(object)->alias;
}

[[nodiscard]] int get_host_trim_section_num(OBJECT_HANDLE) {
    return 1;
}

[[nodiscard]] int get_host_trim_effect_list(
    OBJECT_HANDLE,
    EFFECT_HANDLE* effect_list,
    const int effect_num) {
    if (effect_list == nullptr) {
        return 1;
    }
    if (effect_num < 1) {
        return -1;
    }
    effect_list[0] = &host_trim_effect_slot;
    return 1;
}

[[nodiscard]] LPCWSTR get_host_trim_effect_name(
    const EFFECT_HANDLE) {
    return host_trim_effect_slot.name;
}

[[nodiscard]] LPCSTR get_host_trim_item_value(
    OBJECT_HANDLE,
    const LPCWSTR effect,
    const LPCWSTR item) {
    if (effect == nullptr || item == nullptr) {
        return nullptr;
    }
    const std::wstring_view name(item);
    if (name == std::wstring_view(L"再生位置")) {
        return host_trim_position.c_str();
    }
    if (name == std::wstring_view(L"再生速度")) {
        return host_trim_speed.c_str();
    }
    return nullptr;
}

[[nodiscard]] bool set_host_trim_item_value(
    OBJECT_HANDLE,
    const LPCWSTR effect,
    const LPCWSTR item,
    const LPCSTR value) {
    if (effect == nullptr || item == nullptr || value == nullptr) {
        return false;
    }
    if (host_trim_set_fail) {
        return false;
    }
    const std::wstring_view name(item);
    if (name == std::wstring_view(L"再生位置")) {
        ++host_trim_set_calls;
        host_trim_position = value;
        return true;
    }
    return false;
}

[[nodiscard]] bool move_host_trim_section(
    OBJECT_HANDLE object,
    const int section,
    const int frame) {
    ++host_trim_move_calls;
    if (host_trim_move_calls == host_trim_move_fail_on_call) {
        return false;
    }
    HostObjectFixture* const fixture =
        static_cast<HostObjectFixture*>(object);
    if (section == 0) {
        if (frame > fixture->range.end) {
            return false;
        }
        fixture->range.start = frame;
    } else if (section == 1) {
        if (frame < fixture->range.start) {
            return false;
        }
        fixture->range.end = frame;
    } else {
        return false;
    }
    host_trim_moves.push_back({section, frame});
    return true;
}

void get_host_trim_edit_info(EDIT_INFO* info, const int info_size) {
    require(
        info_size == static_cast<int>(sizeof(EDIT_INFO)),
        "trim host adapter should request complete edit information");
    *info = host_trim_edit_info;
}

[[nodiscard]] bool call_host_trim_section(
    void* parameter,
    void (*callback)(void*, EDIT_SECTION*)) {
    callback(parameter, &host_trim_edit_section);
    return true;
}

void test_host_native_media_trim() {
    host_trim_edit_info = {};
    host_trim_edit_info.width = 1920;
    host_trim_edit_info.height = 1080;
    host_trim_edit_info.rate = 30;
    host_trim_edit_info.scale = 1;
    host_trim_edit_info.sample_rate = 44100;
    host_trim_edit_info.frame_max = 199;
    host_trim_edit_info.layer_max = 2;
    host_trim_edit_info.scene_id = 5;

    host_trim_edit_section = {};
    host_trim_edit_section.find_object = find_host_trim_object;
    host_trim_edit_section.get_object_layer_frame =
        get_host_trim_object_range;
    host_trim_edit_section.get_object_alias =
        get_host_trim_object_alias;
    host_trim_edit_section.get_object_section_num =
        get_host_trim_section_num;
    host_trim_edit_section.get_effect_list =
        get_host_trim_effect_list;
    host_trim_edit_section.get_effect_name =
        get_host_trim_effect_name;
    host_trim_edit_section.get_object_item_value =
        get_host_trim_item_value;
    host_trim_edit_section.set_object_item_value =
        set_host_trim_item_value;
    host_trim_edit_section.move_object_section =
        move_host_trim_section;

    EDIT_HANDLE edit_handle{};
    edit_handle.get_edit_info = get_host_trim_edit_info;
    edit_handle.get_edit_state = get_host_reorder_edit_state;
    edit_handle.call_read_section_param = call_host_trim_section;
    edit_handle.call_edit_section_param = call_host_trim_section;

    HostSdkAdapter adapter(&edit_handle);

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionSectionEndpoints);
    {
        host_trim_fixture.range = {1, 0, 99};
        host_trim_position = "10.000000";
        host_trim_moves.clear();
        host_trim_move_calls = 0;
        host_trim_move_fail_on_call = 0;
        host_trim_set_calls = 0;
        host_trim_set_fail = false;

        const SnapshotResult snapshot = adapter.get_snapshot();
        require(
            snapshot.ok && snapshot.objects.size() == 1U,
            "the trim host fixture should expose one object");

        const StructuralEditResult trimmed =
            adapter.trim_media_object(
                snapshot.revision, 0U, 20, 59, std::nullopt);
        require(
            trimmed.ok && trimmed.native_backend &&
                trimmed.backend == "sdk_move_object_section",
            "a 2.1.4 host should trim media natively");
        require(
            trimmed.layer == 1 && trimmed.frame_start == 20 &&
                trimmed.frame_end == 59,
            "the native trim should report the requested range");
        require(
            trimmed.has_source_position &&
                trimmed.source_position == 30.0,
            "the native trim should compensate the playback position");
        require(
            host_trim_fixture.range.start == 20 &&
                host_trim_fixture.range.end == 59,
            "the host range should match the trim request");
        require(
            host_trim_position == "30.000000",
            "the playback position item should be rewritten");
        require(
            host_trim_moves.size() == 2U &&
                host_trim_moves[0][0] == 0 &&
                host_trim_moves[0][1] == 20 &&
                host_trim_moves[1][0] == 1 &&
                host_trim_moves[1][1] == 59,
            "the native trim should move the start point first");
        require(
            host_trim_set_calls == 1,
            "the native trim should set the source position once");
    }

    {
        host_trim_fixture.range = {1, 0, 99};
        host_trim_position = "10.000000";
        host_trim_moves.clear();
        host_trim_move_calls = 0;
        host_trim_move_fail_on_call = 0;
        host_trim_set_calls = 0;
        host_trim_set_fail = false;

        const SnapshotResult snapshot = adapter.get_snapshot();
        const StructuralEditResult trimmed =
            adapter.trim_media_object(
                snapshot.revision, 0U, 20, 59, 55.5);
        require(
            trimmed.ok && trimmed.source_position == 55.5,
            "an explicit source position should override the delta");
        require(
            host_trim_position == "55.500000",
            "the explicit source position should be written");
    }

    {
        host_trim_fixture.range = {1, 0, 99};
        host_trim_position = "10.000000";
        host_trim_moves.clear();
        host_trim_move_calls = 0;
        host_trim_move_fail_on_call = 2;
        host_trim_set_calls = 0;
        host_trim_set_fail = false;

        const SnapshotResult snapshot = adapter.get_snapshot();
        const StructuralEditResult trimmed =
            adapter.trim_media_object(
                snapshot.revision, 0U, 20, 59, std::nullopt);
        require(
            !trimmed.ok && trimmed.native_backend &&
                trimmed.error_code == "STRUCTURAL_EDIT_FAILED",
            "a rejected endpoint move should fail closed");
        require(
            host_trim_fixture.range.start == 0 &&
                host_trim_fixture.range.end == 99,
            "a rejected endpoint move should restore the range");
        require(
            host_trim_position == "10.000000" &&
                host_trim_set_calls == 0,
            "a rejected endpoint move should not touch the source");
    }

    {
        host_trim_fixture.range = {1, 0, 99};
        host_trim_position = "10.000000";
        host_trim_moves.clear();
        host_trim_move_calls = 0;
        host_trim_move_fail_on_call = 0;
        host_trim_set_calls = 0;
        host_trim_set_fail = true;

        const SnapshotResult snapshot = adapter.get_snapshot();
        const StructuralEditResult trimmed =
            adapter.trim_media_object(
                snapshot.revision, 0U, 20, 59, std::nullopt);
        require(
            !trimmed.ok &&
                trimmed.error_code == "STRUCTURAL_EDIT_FAILED",
            "a rejected source update should fail closed");
        require(
            host_trim_fixture.range.start == 0 &&
                host_trim_fixture.range.end == 99 &&
                host_trim_position == "10.000000",
            "a rejected source update should restore the range");
    }

    aviutl2::live::store_host_version(0U);
    {
        host_trim_fixture.range = {1, 0, 99};
        host_trim_position = "10.000000";
        host_trim_moves.clear();
        host_trim_move_calls = 0;
        host_trim_move_fail_on_call = 0;
        host_trim_set_calls = 0;
        host_trim_set_fail = false;

        const SnapshotResult snapshot = adapter.get_snapshot();
        const StructuralEditResult trimmed =
            adapter.trim_media_object(
                snapshot.revision, 0U, 20, 59, std::nullopt);
        require(
            !trimmed.ok &&
                trimmed.error_code == "EDIT_SECTION_UNAVAILABLE" &&
                host_trim_move_calls == 0,
            "an unrecorded host version should keep the Alias path");
    }
}

void test_protocol_and_fixtures() {
    const std::filesystem::path fixture_dir(AVIUTL2_FIXTURE_DIR);
    FakeSdkAdapter sdk;
    CommandDispatcher dispatcher(sdk, 4242U);

    const std::string ping_request =
        read_file(fixture_dir / "system_ping.request.json");
    const std::string ping_response =
        read_file(fixture_dir / "system_ping.response.json");
    require(
        json_equal(dispatcher.handle_payload(ping_request), ping_response),
        "ping fixture response should match");

    const std::string hello_request =
        read_file(fixture_dir / "system_hello.request.json");
    const std::string hello_response =
        read_file(fixture_dir / "system_hello.response.json");
    require(
        json_equal(dispatcher.handle_payload(hello_request), hello_response),
        "hello fixture response should match");

    const Json capabilities = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"caps","protocol_version":1,"method":"system.get_capabilities","params":{}})"));
    const Json* capability_result = capabilities.find("result");
    require(
        capability_result->find("semantic_effect_profiles")->as_bool() &&
            capability_result->find("native_effect_fallback")->as_bool() &&
            capability_result
                    ->find("edit_plan_create_effect_stack")
                    ->as_bool() &&
            capability_result
                    ->find("media_group_effect_routing")
                    ->as_bool() &&
            capability_result->find("linear_effect_values")->as_bool() &&
            capability_result->find("local_project")->as_bool() &&
            capability_result->find("lossless_aup2_document")->as_bool() &&
            capability_result->find("guarded_checkpoint_save")->as_bool() &&
            capability_result->find("explicit_plan_sync")->as_bool() &&
            capability_result->find("project_path_observation")->as_bool() &&
            capability_result
                    ->find("project_lifecycle_notifications")
                    ->as_bool() &&
            capability_result
                    ->find("aup2_effect_manifest_version")
                    ->as_integer() == 2001901,
        "0.10.0 semantic Effect capabilities should be explicit");

    const std::string batch_validate_request =
        read_file(fixture_dir / "batch_validate.request.json");
    const std::string batch_validate_response =
        read_file(fixture_dir / "batch_validate.response.json");
    require(
        json_equal(
            dispatcher.handle_payload(batch_validate_request),
            batch_validate_response),
        "batch validation fixture response should match");

    const std::string invalid_request =
        read_file(fixture_dir / "invalid_json.request.json");
    const Json invalid_response =
        aviutl2::live::parse_json(dispatcher.handle_payload(invalid_request));
    require(
        invalid_response.find("ok") != nullptr &&
            !invalid_response.find("ok")->as_bool(),
        "invalid JSON should produce an error response");

    const std::string unsupported = dispatcher.handle_payload(
        R"({"id":"v2","protocol_version":2,"method":"system.ping","params":{}})");
    const Json unsupported_json = aviutl2::live::parse_json(unsupported);
    require(
        unsupported_json.find("error") != nullptr &&
            unsupported_json.find("error")->find("code")->as_string() ==
                "PROTOCOL_VERSION_UNSUPPORTED",
        "unsupported protocol should have a stable error code");

    const std::string project = dispatcher.handle_payload(
        R"({"id":"project","protocol_version":1,"method":"project.get_info","params":{}})");
    const Json project_json = aviutl2::live::parse_json(project);
    require(
        project_json.find("result")->find("scene_id")->as_integer() == 7 &&
            project_json.find("result")
                    ->find("project_file_path")
                    ->as_string() == "C:\\Projects\\current.aup2",
        "project info and observed path should come from SdkAdapter");

    const Json catalog_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"catalog","protocol_version":1,"method":"effect.catalog","params":{"start":0,"count":10}})"));
    require(
        catalog_json.find("result")
                    ->find("effects")
                    ->as_array()[0]
                    .find("name")
                    ->as_string() == "動画ファイル" &&
            catalog_json.find("result")
                    ->find("effects")
                    ->as_array()[0]
                    .find("items")
                    ->as_array()[0]
                    .find("type")
                    ->as_string() == "number" &&
            sdk.catalog_calls == 1 &&
            sdk.last_page_start == 0U &&
            sdk.last_page_count == 10U,
        "effect catalog should expose native effects and item types");

    const Json layers_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"layers","protocol_version":1,"method":"project.get_layers","params":{"start":0,"count":10}})"));
    require(
        layers_json.find("result")
                    ->find("revision")
                    ->as_integer() == 123 &&
            layers_json.find("result")
                    ->find("layers")
                    ->as_array()[0]
                    .find("name")
                    ->as_string() == "Video" &&
            layers_json.find("result")
                    ->find("layers")
                    ->as_array()[1]
                    .find("locked")
                    ->as_bool() &&
            sdk.layers_calls == 1,
        "layer page should expose names, locks, and revision");

    const Json snapshot_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"snapshot","protocol_version":1,"method":"project.get_snapshot","params":{}})"));
    require(
        snapshot_json.find("result")->find("revision")->as_integer() == 123 &&
            snapshot_json.find("result")
                    ->find("objects")
                    ->as_array()[0]
                    .find("object_id")
                    ->as_string() == "obj-123-0" &&
            !snapshot_json.find("result")
                 ->find("objects")
                 ->as_array()[0]
                 .find("api_locked")
                 ->as_bool(),
        "snapshot should expose revision-scoped object IDs");

    const Json add_effect_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"effect-add","protocol_version":1,"method":"object.effect.add","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"effect":"ぼかし"}})"));
    require(
        add_effect_json.find("result")
                    ->find("applied_count")
                    ->as_integer() == 1 &&
            sdk.add_effect_calls == 1 &&
            sdk.last_effect == L"ぼかし",
        "effect add should use the native SDK mutation");

    const Json delete_effect_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"effect-delete","protocol_version":1,"method":"object.effect.delete","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"selector":"ぼかし:1"}})"));
    require(
        delete_effect_json.find("result")
                    ->find("applied_count")
                    ->as_integer() == 1 &&
            sdk.delete_effect_calls == 1 &&
            sdk.last_effect == L"ぼかし:1",
        "effect delete should use the inspection selector");

    const Json split_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"split","protocol_version":1,"method":"object.split_media","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"frame":25}})"));
    require(
        split_json.find("result")
                    ->find("left")
                    ->find("frame_end")
                    ->as_integer() == 24 &&
            split_json.find("result")
                    ->find("right")
                    ->find("frame_start")
                    ->as_integer() == 25 &&
            split_json.find("result")
                    ->find("source_position")
                    ->find("right")
                    ->as_number() == 15.0 &&
            sdk.split_calls == 1 &&
            sdk.last_frame == 25,
        "media split should expose verified left/right ranges");

    const Json media_probe_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"probe","protocol_version":1,"method":"media.probe","params":{"file":"C:/media/clip.mp4"}})"));
    require(
        media_probe_json.find("result")
                    ->find("readable")
                    ->as_bool() &&
            media_probe_json.find("result")
                    ->find("kind")
                    ->as_string() == "video" &&
            sdk.probe_media_calls == 1 &&
            sdk.last_file == L"C:/media/clip.mp4",
        "media probe should return AviUtl2-native metadata");

    const std::string fixture_media_path =
        std::filesystem::absolute(
            fixture_dir / "system_ping.request.json")
            .generic_string();
    const Json create_media_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            std::string(
                R"({"id":"media","protocol_version":1,"method":"object.create_from_media_file","params":{"file":")") +
            fixture_media_path +
            R"(","layer":4,"frame":20,"length":0}})"));
    require(
        create_media_json.find("result")
                    ->find("created")
                    ->find("frame_end")
                    ->as_integer() == 94 &&
            sdk.create_media_calls == 1 &&
            sdk.last_layer == 4 && sdk.last_frame == 20 &&
            sdk.last_length == 0,
        "native media creation should expose the actual created range");

    const Json inspection_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"inspect","protocol_version":1,"method":"object.inspect","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"sample_frame":10}})"));
    require(
        inspection_json.find("result")
                    ->find("effect_count")
                    ->as_integer() == 1 &&
            inspection_json.find("result")
                    ->find("effects")
                    ->as_array()[0]
                    .find("items")
                    ->as_array()[0]
                    .find("track")
                    ->find("mode")
                    ->as_string() == "直線移動" &&
            sdk.inspect_calls == 1 && sdk.last_revision == 123 &&
            sdk.last_object_index == 0U && sdk.last_frame == 10,
        "structured inspection should preserve typed track metadata");

    const std::string render_response = dispatcher.handle_payload(
        R"({"id":"render","protocol_version":1,"method":"frame.render","params":{"frame":12}})");
    const Json render_json = aviutl2::live::parse_json(
        render_response);
    require(
        render_json.find("result")
                    ->find("capture_id")
                    ->as_string() == "cap-4242-1" &&
            render_json.find("result")
                    ->find("native_renderer")
                    ->as_bool() &&
            render_json.find("result")
                    ->find("width")
                    ->as_integer() == 2 &&
            sdk.render_calls == 1 && sdk.last_frame == 12,
        "native frame render should create a PNG capture");

    const Json chunk_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"chunk","protocol_version":1,"method":"frame.read_chunk","params":{"capture_id":"cap-4242-1","index":0}})"));
    require(
        chunk_json.find("result")
                    ->find("data_base64")
                    ->as_string()
                    .starts_with("iVBORw0KGgo") &&
            chunk_json.find("result")
                    ->find("eof")
                    ->as_bool(),
        "render capture chunk should contain a PNG signature");

    const Json release_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"release","protocol_version":1,"method":"frame.release","params":{"capture_id":"cap-4242-1"}})"));
    require(
        release_json.find("result")
            ->find("released")
            ->as_bool(),
        "frame capture should be explicitly releasable");

    const Json object_render_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"orender","protocol_version":1,"method":"object.render_frame","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"frame":12,"apply_effect":false}})"));
    require(
        object_render_json.find("result")
                    ->find("capture_id")
                    ->as_string() == "cap-4242-2" &&
            object_render_json.find("result")
                    ->find("object_index")
                    ->as_integer() == 0 &&
            object_render_json.find("result")
                    ->find("apply_effect")
                    ->as_bool() == false &&
            sdk.object_render_calls == 1 &&
            sdk.last_frame == 12 &&
            sdk.last_revision == 123 &&
            sdk.last_object_index == 0U &&
            sdk.last_apply_effect == false,
        "object frame render should create a capture scoped to the object");
    sdk.object_render_fail_stale = true;
    const Json object_render_stale_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"orender2","protocol_version":1,"method":"object.render_frame","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"frame":12}})"));
    require(
        object_render_stale_json.find("error")
                    ->find("code")
                    ->as_string() == "STALE_PROJECT_STATE" &&
            sdk.object_render_calls == 2,
        "object frame render should reject a stale revision before rendering");
    sdk.object_render_fail_stale = false;



    const Json set_items_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"set","protocol_version":1,"method":"object.set_items","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"items":[{"effect":"標準描画","item":"X","value":"100.00"},{"effect":"標準描画","item":"Y","value":"-20.00"}]}})"));
    require(
        set_items_json.find("result")
                    ->find("applied_count")
                    ->as_integer() == 2 &&
            sdk.set_items_calls == 1 && sdk.last_updates.size() == 2U &&
            sdk.last_revision == 123 && sdk.last_object_index == 0U,
        "multiple item updates should reach one SdkAdapter operation");

    const Json move_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"move","protocol_version":1,"method":"object.move","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"layer":4,"frame":50}})"));
    require(
        move_json.find("result")->find("undo_grouped")->as_bool() &&
            sdk.move_calls == 1 && sdk.last_layer == 4 &&
            sdk.last_frame == 50,
        "move should resolve a revision-scoped target");

    const Json delete_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"delete","protocol_version":1,"method":"object.delete","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"}}})"));
    require(
        delete_json.find("result")->find("applied_count")->as_integer() == 1 &&
            sdk.delete_calls == 1,
        "delete should resolve a revision-scoped target");

    sdk.mutation_result = ObjectMutationResult{
        false,
        125,
        0U,
        "STALE_PROJECT_STATE",
        "changed",
        false,
    };
    const Json stale_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"stale","protocol_version":1,"method":"object.set_item","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"effect":"テキスト","item":"テキスト","value":"new"}})"));
    require(
        stale_json.find("error")->find("code")->as_string() ==
                "STALE_PROJECT_STATE" &&
            stale_json.find("error")
                    ->find("details")
                    ->find("current_revision")
                    ->as_integer() == 125,
        "stale edits should expose the current revision");

    sdk.mutation_result = ObjectMutationResult{
        false,
        126,
        0U,
        "OBJECT_API_LOCKED",
        "locked",
        false,
    };
    const Json locked_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"locked","protocol_version":1,"method":"object.delete","params":{"expected_revision":126,"target":{"object_id":"obj-126-0"}}})"));
    require(
        locked_json.find("error")->find("code")->as_string() ==
                "OBJECT_API_LOCKED" &&
            locked_json.find("error")
                    ->find("details")
                    ->find("current_revision")
                    ->as_integer() == 126,
        "API-locked edits should return a stable structured error");
    sdk.mutation_result = ObjectMutationResult{true, 124, 1U};

    const std::string alias =
        "[Object]\\r\\n[Object.0]\\r\\neffect.name=Text\\r\\n";
    const std::string validate = dispatcher.handle_payload(
        std::string(
            R"({"id":"validate","protocol_version":1,"method":"batch.validate","params":{"commands":[{"op":"object.create_from_alias","client_id":"title","layer":2,"frame":10,"length":30,"alias":")") +
        alias +
        R"("}]}})");
    const Json validate_json = aviutl2::live::parse_json(validate);
    require(
        validate_json.find("result")->find("valid")->as_bool(),
        "valid batch should pass validation");
    require(
        sdk.validate_calls == 2 && sdk.last_commands.size() == 1U &&
            sdk.last_commands[0].frame == 10,
        "validated command should reach SdkAdapter");

    const std::string apply = dispatcher.handle_payload(
        std::string(
            R"({"id":"apply","protocol_version":1,"method":"batch.apply","params":{"commands":[{"op":"object.create_from_alias","client_id":"title","layer":2,"frame":10,"length":30,"alias":")") +
        alias +
        R"("}]}})");
    const Json apply_json = aviutl2::live::parse_json(apply);
    require(
        apply_json.find("result")->find("applied_count")->as_integer() == 1 &&
            apply_json.find("result")->find("undo_grouped")->as_bool(),
        "applied batch should report one Undo-grouped creation");

    const std::string direct = dispatcher.handle_payload(
        std::string(
            R"({"id":"direct","protocol_version":1,"method":"object.create_from_alias","params":{"layer":1,"frame":0,"length":5,"alias":")") +
        alias +
        R"("}})");
    const Json direct_json = aviutl2::live::parse_json(direct);
    require(
        direct_json.find("result")->find("applied_count")->as_integer() == 1,
        "direct Alias creation should use the batch application path");

    const Json invalid_batch = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"bad","protocol_version":1,"method":"batch.apply","params":{"commands":[{"op":"object.create_from_alias","layer":0,"frame":0,"length":0,"alias":"[Object]\neffect.name=Text"}]}})"));
    require(
        invalid_batch.find("error")->find("code")->as_string() ==
                "INVALID_ARGUMENT" &&
            sdk.apply_calls == 2,
        "invalid command should be rejected before SdkAdapter");

    const std::string plan_request =
        std::string(
            R"({"id":"plan","protocol_version":1,"method":"edit.plan.apply","params":{"expected_revision":123,"commands":[{"op":"object.create_from_alias","key":"title","layer":4,"frame":100,"length":10,"alias":")") +
        alias +
        R"(","effects":[{"effect":"グロー","profile":"glow","scope":"video","enabled":false,"items":[{"effect":"グロー","item":"強さ","value":"50.00"}]}]},{"op":"object.update","key":"existing","target":{"object_id":"obj-123-0"},"items":[{"effect":"標準描画","item":"X","value":"120.000000"}]}]}})";
    const Json plan_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(88U, plan_request));
    require(
        plan_json.find("result")->find("applied_count")->as_integer() == 2 &&
            plan_json.find("result")->find("undo_grouped")->as_bool() &&
            sdk.plan_calls == 1 && sdk.last_plan_apply &&
            sdk.last_plan_commands.size() == 2U &&
            sdk.last_plan_commands[0].effects.size() == 1U &&
            sdk.last_plan_commands[0].effects[0].effect == L"グロー" &&
            sdk.last_plan_commands[0].effects[0].profile == "glow" &&
            sdk.last_plan_commands[0].effects[0].scope ==
                EditPlanEffectScope::video &&
            !sdk.last_plan_commands[0].effects[0].enabled &&
            sdk.last_plan_commands[0].effects[0].items.size() == 1U &&
            sdk.last_plan_commands[0].effects[0].items[0].item == L"強さ" &&
            sdk.last_plan_commands[1].updates.size() == 1U,
        "mixed effect plan should preserve its ordered initial Effect data");

    const Json plan_validate = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            std::string(
                R"({"id":"plan-validate","protocol_version":1,"method":"edit.plan.validate","params":{"expected_revision":123,"commands":[{"op":"object.create_from_alias","key":"title","layer":4,"frame":100,"length":10,"alias":")") +
            alias + R"("}]}})"));
    require(
        plan_validate.find("result")->find("valid")->as_bool() &&
            sdk.plan_calls == 2 && !sdk.last_plan_apply,
        "edit plan validation should not request an applying SDK section");

    std::string excessive_effects =
        R"({"id":"too-many-effects","protocol_version":1,"method":"edit.plan.validate","params":{"expected_revision":123,"commands":[{"op":"object.create_from_alias","key":"title","layer":4,"frame":100,"length":10,"alias":")" +
        alias + R"(","effects":[)";
    for (int index = 0; index < 33; ++index) {
        if (index != 0) {
            excessive_effects += ',';
        }
        excessive_effects +=
            R"({"effect":"Glow","scope":"video","enabled":true,"items":[]})";
    }
    excessive_effects += R"(]}]}})";
    const Json excessive_effects_result = aviutl2::live::parse_json(
        dispatcher.handle_payload(excessive_effects));
    require(
        excessive_effects_result.find("error")
                    ->find("code")
                    ->as_string() == "INVALID_ARGUMENT" &&
            sdk.plan_calls == 2,
        "create-time Effect count should be bounded before the SDK call");

    sdk.plan_result = EditPlanResult{};
    sdk.plan_result.failed_command_index = 1U;
    sdk.plan_result.rollback_attempted = true;
    sdk.plan_result.rollback_complete = false;
    sdk.plan_result.restored_count = 1U;
    sdk.plan_result.gui_undo_required = true;
    sdk.plan_result.error_code = "PLAN_APPLY_FAILED";
    sdk.plan_result.error_message = "planned failure";
    const Json plan_failure = aviutl2::live::parse_json(
        dispatcher.handle_payload(88U, plan_request));
    const Json* plan_error = plan_failure.find("error");
    const Json* rollback = plan_error->find("details")->find("rollback");
    require(
        plan_error->find("code")->as_string() == "PLAN_APPLY_FAILED" &&
            rollback->find("attempted")->as_bool() &&
            !rollback->find("complete")->as_bool() &&
            rollback->find("restored_count")->as_integer() == 1 &&
            rollback->find("gui_undo_required")->as_bool(),
        "edit plan failure should expose explicit rollback status");
    sdk.plan_result = EditPlanResult{true, true, 124, 2U};

    sdk.validate_result = BatchEditResult{};
    sdk.validate_result.error_code = "PLACEMENT_COLLISION";
    sdk.validate_result.error_message = "occupied";
    sdk.validate_result.failed_command_index = 0U;
    sdk.validate_result.has_collision = true;
    sdk.validate_result.collision_layer = 2;
    sdk.validate_result.collision_start = 12;
    sdk.validate_result.collision_end = 20;
    const Json collision_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            std::string(
                R"({"id":"collision","protocol_version":1,"method":"batch.validate","params":{"commands":[{"op":"object.create_from_alias","layer":2,"frame":10,"length":30,"alias":")") +
            alias +
            R"("}]}})"));
    require(
        collision_json.find("error")->find("code")->as_string() ==
                "PLACEMENT_COLLISION" &&
            collision_json.find("error")
                    ->find("details")
                    ->find("collision")
                    ->find("start")
                    ->as_integer() == 12,
        "placement collision should include a structured range");
}

// Frame-mark host fixture.  The mark callbacks model AviUtl2's
// frame mark storage so that set/clear/move can be verified through
// the adapter read-back path.
EDIT_INFO host_marks_edit_info{};
EDIT_SECTION host_marks_edit_section{};
std::map<int, std::wstring> host_marks;
int host_mark_set_calls = 0;
int host_mark_clear_calls = 0;
int host_mark_move_calls = 0;
int host_mark_edited_calls = 0;
bool host_mark_set_fail = false;
bool host_mark_move_fail = false;

void get_host_marks_edit_info(EDIT_INFO* info, const int info_size) {
    require(
        info_size == static_cast<int>(sizeof(EDIT_INFO)),
        "marks host adapter should request complete edit information");
    *info = host_marks_edit_info;
}

[[nodiscard]] int get_host_marks_edit_state() {
    return EDIT_HANDLE::EDIT_STATE_EDIT;
}

[[nodiscard]] bool call_host_marks_section(
    void* parameter,
    void (*callback)(void*, EDIT_SECTION*)) {
    callback(parameter, &host_marks_edit_section);
    return true;
}

[[nodiscard]] int host_get_mark_frame_list(
    int* frame_list,
    const int frame_num) {
    const int count = static_cast<int>(host_marks.size());
    if (frame_list == nullptr) {
        return count;
    }
    if (frame_num < count) {
        return -1;
    }
    int index = 0;
    for (const auto& entry : host_marks) {
        frame_list[index++] = entry.first;
    }
    return count;
}

[[nodiscard]] LPCWSTR host_get_mark_frame_memo(const int frame) {
    const auto found = host_marks.find(frame);
    return found == host_marks.end() ? nullptr : found->second.c_str();
}

void host_set_mark_frame(const int frame, LPCWSTR memo) {
    ++host_mark_set_calls;
    if (host_mark_set_fail) {
        return;
    }
    if (memo == nullptr || *memo == L'\0') {
        host_marks.erase(frame);
        return;
    }
    host_marks[frame] = memo;
}

void host_clear_mark_frame(const int frame) {
    ++host_mark_clear_calls;
    host_marks.erase(frame);
}

[[nodiscard]] bool host_move_mark_frame(
    const int frame,
    const int frame_to) {
    ++host_mark_move_calls;
    if (host_mark_move_fail) {
        return false;
    }
    const auto found = host_marks.find(frame);
    if (found == host_marks.end() || host_marks.count(frame_to) != 0U) {
        return false;
    }
    host_marks[frame_to] = std::move(found->second);
    host_marks.erase(found);
    return true;
}

void host_set_marks_edited_state() {
    ++host_mark_edited_calls;
}

void test_host_frame_marks() {
    host_marks_edit_info = {};
    host_marks_edit_info.width = 1920;
    host_marks_edit_info.height = 1080;
    host_marks_edit_info.rate = 30;
    host_marks_edit_info.scale = 1;
    host_marks_edit_info.sample_rate = 44100;
    host_marks_edit_info.frame_max = 199;
    host_marks_edit_info.layer_max = 2;
    host_marks_edit_info.scene_id = 3;

    host_marks_edit_section = {};
    host_marks_edit_section.find_object = find_host_object;
    host_marks_edit_section.get_object_layer_frame =
        get_host_object_range;
    host_marks_edit_section.get_object_alias = get_host_object_alias;
    host_marks_edit_section.get_mark_frame_list =
        host_get_mark_frame_list;
    host_marks_edit_section.get_mark_frame_memo =
        host_get_mark_frame_memo;
    host_marks_edit_section.set_mark_frame = host_set_mark_frame;
    host_marks_edit_section.clear_mark_frame = host_clear_mark_frame;
    host_marks_edit_section.move_mark_frame = host_move_mark_frame;
    host_marks_edit_section.set_edited_state =
        host_set_marks_edited_state;

    EDIT_HANDLE edit_handle{};
    edit_handle.get_edit_info = get_host_marks_edit_info;
    edit_handle.get_edit_state = get_host_marks_edit_state;
    edit_handle.call_read_section_param = call_host_marks_section;
    edit_handle.call_edit_section_param = call_host_marks_section;

    HostSdkAdapter adapter(&edit_handle);

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionSectionEndpoints);
    {
        host_marks.clear();
        host_mark_set_calls = 0;
        host_mark_clear_calls = 0;
        host_mark_move_calls = 0;
        host_mark_edited_calls = 0;
        host_mark_set_fail = false;
        host_mark_move_fail = false;

        const SnapshotResult snapshot = adapter.get_snapshot();
        require(
            snapshot.ok,
            "the marks host fixture should capture a snapshot");

        const FrameMarksResult empty = adapter.get_frame_marks();
        require(
            empty.ok && empty.marks.empty(),
            "the marks fixture should start without marks");

        const MarkEditResult set_mark = adapter.set_frame_mark(
            snapshot.revision, 10, L"note");
        require(
            set_mark.ok && set_mark.frame == 10 &&
                set_mark.memo == "note",
            "mark.set should persist the frame mark");
        require(
            host_mark_edited_calls == 1,
            "mark.set should flag the host edit state");

        const FrameMarksResult listed = adapter.get_frame_marks();
        require(
            listed.ok && listed.marks.size() == 1U &&
                listed.marks[0].frame == 10 &&
                listed.marks[0].memo == "note",
            "mark.list should round trip the frame mark");

        const MarkEditResult stale = adapter.set_frame_mark(
            snapshot.revision + 1, 20, L"note2");
        require(
            !stale.ok &&
                stale.error_code == "STALE_PROJECT_STATE" &&
                host_mark_set_calls == 1,
            "mark.set should reject a stale revision before editing");

        const MarkEditResult missing = adapter.clear_frame_mark(
            snapshot.revision, 99);
        require(
            !missing.ok &&
                missing.error_code == "MARK_NOT_FOUND",
            "mark.clear should report an unmarked frame");

        const MarkEditResult second = adapter.set_frame_mark(
            snapshot.revision, 20, L"note2");
        require(
            second.ok && second.frame == 20 &&
                second.memo == "note2" && host_marks.size() == 2U,
            "a second mark.set should succeed");

        const MarkEditResult moved = adapter.move_frame_mark(
            snapshot.revision, 10, 30);
        require(
            moved.ok && moved.frame == 30 &&
                host_marks.count(10) == 0U &&
                host_marks.count(30) == 1U,
            "mark.move should relocate the frame mark");
        require(
            host_mark_edited_calls == 3,
            "mark.move should flag the host edit state");

        const MarkEditResult occupied = adapter.move_frame_mark(
            snapshot.revision, 30, 20);
        require(
            !occupied.ok &&
                occupied.error_code == "MARK_MOVE_REJECTED",
            "mark.move should reject an occupied destination");

        const MarkEditResult cleared = adapter.clear_frame_mark(
            snapshot.revision, 20);
        require(
            cleared.ok && host_marks.size() == 1U,
            "mark.clear should remove the frame mark");

        const MarkEditResult repeated = adapter.clear_frame_mark(
            snapshot.revision, 20);
        require(
            !repeated.ok &&
                repeated.error_code == "MARK_NOT_FOUND",
            "mark.clear should report a missing mark");

        host_mark_set_fail = true;
        const MarkEditResult failed_set = adapter.set_frame_mark(
            snapshot.revision, 30, L"note3");
        require(
            !failed_set.ok &&
                failed_set.error_code == "MARK_SET_FAILED",
            "mark.set should fail closed when the host ignores the write");
        require(
            host_marks.size() == 1U &&
                host_marks.begin()->first == 30,
            "a failed mark.set should preserve the previous state");
    }

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionMoveEffect);
    {
        const FrameMarksResult unavailable =
            adapter.get_frame_marks();
        require(
            !unavailable.ok &&
                unavailable.error_code == "MARKS_UNAVAILABLE",
            "frame marks should be unavailable on older hosts");
    }
    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionSectionEndpoints);
}

void test_host_mark_dispatch() {
    FakeSdkAdapter sdk;
    CommandDispatcher dispatcher(sdk, 4242U);

    sdk.frame_marks_result.ok = true;
    sdk.frame_marks_result.revision = 123;
    sdk.frame_marks_result.marks.push_back({10, "note"});

    const Json marks_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"marks","protocol_version":1,"method":"mark.list","params":{}})"));
    require(
        marks_json.find("result")->find("count")->as_integer() == 1 &&
            marks_json.find("result")
                    ->find("marks")
                    ->as_array()[0]
                    .find("frame")
                    ->as_integer() == 10 &&
            marks_json.find("result")
                    ->find("marks")
                    ->as_array()[0]
                    .find("memo")
                    ->as_string() == "note" &&
            marks_json.find("result")->find("revision")->as_integer() == 123,
        "mark.list should expose frame marks from the adapter");

    sdk.mark_edit_result.ok = true;
    sdk.mark_edit_result.has_memo = true;
    sdk.mark_edit_result.memo = "note2";
    const Json mark_set_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"mark-set","protocol_version":1,"method":"mark.set","params":{"expected_revision":123,"frame":15,"memo":"note2","confirm_non_undoable":true}})"));
    require(
        mark_set_json.find("result")->find("frame")->as_integer() == 15 &&
            mark_set_json.find("result")
                    ->find("non_undoable")
                    ->is_bool() &&
            mark_set_json.find("result")->find("memo")->as_string() == "note2",
        "mark.set should return the resulting mark");
    require(
        sdk.mark_edit_calls == 1,
        "mark.set should reach the adapter");

    const Json gate_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"mark-gate","protocol_version":1,"method":"mark.set","params":{"expected_revision":123,"frame":15,"memo":"note2"}})"));
    require(
        gate_json.find("error")->find("code")->as_string() ==
            "NON_UNDOABLE_CONFIRMATION_REQUIRED",
        "mark.set should require the non-undoable confirmation");

    sdk.mark_edit_result.ok = false;
    sdk.mark_edit_result.has_memo = false;
    sdk.mark_edit_result.error_code = "MARK_NOT_FOUND";
    sdk.mark_edit_result.error_message =
        "The requested frame does not carry a mark.";
    const Json missing_json = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            R"({"id":"mark-missing","protocol_version":1,"method":"mark.clear","params":{"expected_revision":123,"frame":20,"confirm_non_undoable":true}})"));
    require(
        missing_json.find("error")->find("code")->as_string() ==
            "MARK_NOT_FOUND",
        "mark.clear should forward adapter errors");
}

void test_sessions_events_and_audio() {
    FakeSdkAdapter sdk;
    CommandDispatcher dispatcher(sdk, 5151U);
    dispatcher.start();

    const Json session = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"session","protocol_version":1,"method":"session.open","params":{"client_name":"native-test"}})"));
    require(
        session.find("result")
                    ->find("connection_id")
                    ->as_integer() == 77 &&
            session.find("result")
                ->find("session_id")
                ->as_string() == "session-5151-77",
        "session.open should bind identity to one pipe connection");

    const std::string first_request =
        R"({"id":"first","protocol_version":1,"method":"object.set_name","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"name":"Title","operation_id":"same-op"}})";
    const Json first = aviutl2::live::parse_json(
        dispatcher.handle_payload(77U, first_request));
    const Json repeated = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"repeat","protocol_version":1,"method":"object.set_name","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"name":"Title","operation_id":"same-op"}})"));
    require(
        first.find("ok")->as_bool() &&
            repeated.find("ok")->as_bool() &&
            repeated.find("id")->as_string() == "repeat" &&
            sdk.set_name_calls == 1,
        "same-session mutation retry should reuse the first result");
    const Json reused = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"reused","protocol_version":1,"method":"object.set_name","params":{"expected_revision":123,"target":{"object_id":"obj-123-0"},"name":"Other","operation_id":"same-op"}})"));
    require(
        reused.find("error")->find("code")->as_string() ==
            "OPERATION_ID_REUSED",
        "operation_id reuse with another payload should be refused");

    dispatcher.record_event("object_updated");
    dispatcher.record_event("edit_frame_changed");
    dispatcher.record_event("project_saving");
    const Json watched = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"watch","protocol_version":1,"method":"event.watch","params":{"after_sequence":0,"timeout_ms":0,"types":["object_updated"]}})"));
    require(
        watched.find("result")
                    ->find("events")
                    ->as_array()
                    .size() == 1U &&
            watched.find("result")
                    ->find("events")
                    ->as_array()[0]
                    .find("sequence")
                    ->as_integer() == 1,
        "event.watch should filter the sequenced journal");
    const Json lifecycle = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"lifecycle","protocol_version":1,"method":"event.watch","params":{"after_sequence":0,"timeout_ms":0,"types":["project_saving"]}})"));
    require(
        lifecycle.find("result")
                    ->find("events")
                    ->as_array()
                    .size() == 1U &&
            lifecycle.find("result")
                    ->find("events")
                    ->as_array()[0]
                    .find("type")
                    ->as_string() == "project_saving",
        "project lifecycle observations should use the event journal");
    for (std::size_t index = 0U;
         index < aviutl2::live::kMaxEventJournalEntries + 1U;
         ++index) {
        dispatcher.record_event("object_updated");
    }
    const Json overflow = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"overflow","protocol_version":1,"method":"event.watch","params":{"after_sequence":1,"timeout_ms":0}})"));
    require(
        overflow.find("result")
            ->find("resync_required")
            ->as_bool(),
        "event journal overflow should require a fresh snapshot");

    const Json audio = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"audio","protocol_version":1,"method":"audio.render","params":{"frame_start":10,"frame_end":11,"expected_revision":123}})"));
    require(
        audio.find("result")
                    ->find("format")
                    ->as_string() == "f32le" &&
            audio.find("result")
                    ->find("channels")
                    ->as_integer() == 2 &&
            audio.find("result")
                    ->find("sample_count")
                    ->as_integer() == 2 &&
            sdk.audio_render_calls == 1,
        "native audio render should expose revision-bound stereo PCM");
    const std::string capture_id =
        audio.find("result")->find("capture_id")->as_string();
    const Json audio_chunk = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            std::string(
                R"({"id":"audio-chunk","protocol_version":1,"method":"audio.read_chunk","params":{"capture_id":")") +
                capture_id +
                R"(","index":0}})"));
    require(
        audio_chunk.find("result")
                    ->find("data_size")
                    ->as_integer() == 16 &&
            audio_chunk.find("result")->find("eof")->as_bool(),
        "audio.read_chunk should return the complete test PCM");
    const Json audio_release = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            std::string(
                R"({"id":"audio-release","protocol_version":1,"method":"audio.release","params":{"capture_id":")") +
                capture_id +
                R"("}})"));
    require(
        audio_release.find("result")
            ->find("released")
            ->as_bool(),
        "audio captures should be explicitly releasable");

    const Json history = aviutl2::live::parse_json(
        dispatcher.handle_payload(
            77U,
            R"({"id":"undo","protocol_version":1,"method":"history.undo","params":{}})"));
    require(
        history.find("error")->find("code")->as_string() ==
            "SDK_METHOD_UNAVAILABLE",
        "missing official SDK history must never be reported as success");
    dispatcher.stop();
}

[[nodiscard]] HANDLE connect_pipe(
    const std::wstring& pipe_name,
    const DWORD timeout_ms = 3000U) {
    const ULONGLONG deadline = GetTickCount64() + timeout_ms;
    while (GetTickCount64() < deadline) {
        HANDLE handle = CreateFileW(
            pipe_name.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            0U,
            nullptr,
            OPEN_EXISTING,
            0U,
            nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            return handle;
        }
        const DWORD error = GetLastError();
        if (error != ERROR_PIPE_BUSY && error != ERROR_FILE_NOT_FOUND) {
            return INVALID_HANDLE_VALUE;
        }
        WaitNamedPipeW(pipe_name.c_str(), 50U);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return INVALID_HANDLE_VALUE;
}

void write_all(
    const HANDLE pipe,
    const void* source,
    const std::size_t size) {
    const auto* data = static_cast<const std::byte*>(source);
    std::size_t offset = 0U;
    while (offset < size) {
        DWORD written = 0U;
        require(
            WriteFile(
                pipe,
                data + offset,
                static_cast<DWORD>(size - offset),
                &written,
                nullptr) != FALSE,
            "client WriteFile failed");
        require(written > 0U, "client WriteFile returned zero");
        offset += written;
    }
}

void read_all(HANDLE pipe, void* destination, const std::size_t size) {
    auto* data = static_cast<std::byte*>(destination);
    std::size_t offset = 0U;
    while (offset < size) {
        DWORD read = 0U;
        require(
            ReadFile(
                pipe,
                data + offset,
                static_cast<DWORD>(size - offset),
                &read,
                nullptr) != FALSE,
            "client ReadFile failed");
        require(read > 0U, "client ReadFile returned zero");
        offset += read;
    }
}

[[nodiscard]] std::string transact(
    const std::wstring& pipe_name,
    const std::string_view payload) {
    const HANDLE pipe = connect_pipe(pipe_name);
    require(pipe != INVALID_HANDLE_VALUE, "client could not connect to test pipe");
    const std::string frame = aviutl2::live::encode_frame(payload);
    write_all(pipe, frame.data(), frame.size());
    std::array<std::uint8_t, 4> header{};
    read_all(pipe, header.data(), header.size());
    const std::uint32_t length =
        static_cast<std::uint32_t>(header[0]) |
        (static_cast<std::uint32_t>(header[1]) << 8U) |
        (static_cast<std::uint32_t>(header[2]) << 16U) |
        (static_cast<std::uint32_t>(header[3]) << 24U);
    require(
        length > 0U && length <= aviutl2::live::kMaxPayloadBytes,
        "response frame length is invalid");
    std::string response(length, '\0');
    read_all(pipe, response.data(), response.size());
    CloseHandle(pipe);
    return response;
}

void test_pipe_server() {
    const std::wstring pipe_name =
        L"\\\\.\\pipe\\AviUtl2.LiveBridge.Test." +
        std::to_wstring(GetCurrentProcessId());
    PipeServer server(
        pipe_name,
        [](const std::string_view payload) {
            if (payload.starts_with("parallel-")) {
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(25));
            }
            return std::string(payload);
        });
    std::string error;
    require(server.start(error), error);

    {
        const HANDLE pipe = connect_pipe(pipe_name);
        require(pipe != INVALID_HANDLE_VALUE, "partial client could not connect");
        const std::array<std::uint8_t, 2> partial{5U, 0U};
        write_all(pipe, partial.data(), partial.size());
        CloseHandle(pipe);
    }
    require(
        transact(pipe_name, R"({"valid":true})") == R"({"valid":true})",
        "server should recover after a partial frame");

    {
        const HANDLE pipe = connect_pipe(pipe_name);
        require(pipe != INVALID_HANDLE_VALUE, "oversize client could not connect");
        const std::uint32_t length =
            static_cast<std::uint32_t>(
                aviutl2::live::kMaxPayloadBytes + 1U);
        const std::array<std::uint8_t, 4> header{
            static_cast<std::uint8_t>(length & 0xFFU),
            static_cast<std::uint8_t>((length >> 8U) & 0xFFU),
            static_cast<std::uint8_t>((length >> 16U) & 0xFFU),
            static_cast<std::uint8_t>((length >> 24U) & 0xFFU),
        };
        write_all(pipe, header.data(), header.size());
        CloseHandle(pipe);
    }
    require(
        transact(pipe_name, R"({"still":"alive"})") ==
            R"({"still":"alive"})",
        "server should recover after an oversized frame");

    std::atomic_bool begin_parallel = false;
    std::atomic_int parallel_successes = 0;
    std::array<std::thread, aviutl2::live::kMaxPipeClients>
        clients;
    for (std::size_t index = 0U;
         index < clients.size();
         ++index) {
        clients[index] = std::thread(
            [&, index] {
                while (!begin_parallel.load(
                    std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                try {
                    const std::string payload =
                        "parallel-" + std::to_string(index);
                    if (transact(pipe_name, payload) == payload) {
                        parallel_successes.fetch_add(
                            1,
                            std::memory_order_acq_rel);
                    }
                } catch (...) {
                    // The root test thread validates the success count.
                }
            });
    }
    begin_parallel.store(true, std::memory_order_release);
    for (std::thread& client : clients) {
        client.join();
    }
    require(
        parallel_successes.load(std::memory_order_acquire) ==
            static_cast<int>(aviutl2::live::kMaxPipeClients),
        "all eight concurrent pipe clients should complete");

    const HANDLE idle_pipe = connect_pipe(pipe_name);
    require(idle_pipe != INVALID_HANDLE_VALUE, "idle client could not connect");
    server.stop();
    CloseHandle(idle_pipe);
    require(!server.running(), "server should stop and join its worker");
}

void test_instance_registry() {
    const std::filesystem::path test_root =
        std::filesystem::current_path() / "registry_test";
    std::filesystem::create_directory(test_root);

    std::array<wchar_t, 32768> original{};
    const DWORD original_size = GetEnvironmentVariableW(
        L"LOCALAPPDATA",
        original.data(),
        static_cast<DWORD>(original.size()));
    require(
        SetEnvironmentVariableW(L"LOCALAPPDATA", test_root.c_str()) != FALSE,
        "LOCALAPPDATA test override failed");

    const std::uint32_t pid = GetCurrentProcessId();
    const std::wstring pipe_name =
        L"\\\\.\\pipe\\AviUtl2.LiveBridge." + std::to_wstring(pid);
    aviutl2::live::InstanceRegistry registry(pid, pipe_name);
    std::string error;
    require(registry.publish(9, error), error);
    require(
        std::filesystem::is_regular_file(registry.path()),
        "instance registry file should exist");
    const Json document =
        aviutl2::live::parse_json(read_file(registry.path()));
    require(
        document.find("pid")->as_integer() ==
            static_cast<std::int64_t>(pid),
        "instance registry PID should match");
    require(
        document.find("scene_id")->as_integer() == 9,
        "instance registry scene should match");
    registry.remove();
    require(
        !std::filesystem::exists(registry.path()),
        "instance registry file should be removed");

    if (original_size > 0U &&
        static_cast<std::size_t>(original_size) < original.size()) {
        SetEnvironmentVariableW(L"LOCALAPPDATA", original.data());
    } else {
        SetEnvironmentVariableW(L"LOCALAPPDATA", nullptr);
    }
    std::error_code ignored;
    std::filesystem::remove(
        test_root / "AviUtl2LiveBridge" / "instances",
        ignored);
    std::filesystem::remove(test_root / "AviUtl2LiveBridge", ignored);
    std::filesystem::remove(test_root, ignored);
}

void test_host_version_gates() {
    FakeSdkAdapter sdk;
    CommandDispatcher dispatcher(sdk, 4242U);

    const auto capabilities = [&dispatcher]() {
        return aviutl2::live::parse_json(dispatcher.handle_payload(
            R"({"id":"caps","protocol_version":1,"method":"system.get_capabilities","params":{}})"));
    };

    aviutl2::live::store_host_version(0U);
    {
        const Json document = capabilities();
        const Json* const host = document.find("result")->find("host");
        require(
            host->find("version")->as_integer() == 0 &&
                host->find("required")->as_integer() ==
                    static_cast<std::int64_t>(
                        aviutl2::live::kRequiredHostVersion) &&
                !host->find("sdk_frame_marks")->as_bool() &&
                !host->find("sdk_move_effect")->as_bool() &&
                !host->find("sdk_object_rendering")->as_bool() &&
                !host->find("sdk_scene_crud")->as_bool() &&
                !host->find("sdk_section_endpoints")->as_bool(),
            "an unrecorded host version should keep every SDK gate off");
    }

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionMoveEffect);
    {
        const Json document = capabilities();
        const Json* const host = document.find("result")->find("host");
        require(
            host->find("version")->as_integer() ==
                    static_cast<std::int64_t>(
                        aviutl2::live::kHostVersionMoveEffect) &&
                host->find("sdk_move_effect")->as_bool() &&
                host->find("sdk_object_rendering")->as_bool() &&
                !host->find("sdk_frame_marks")->as_bool() &&
                !host->find("sdk_section_endpoints")->as_bool(),
            "a 2.1.3 host should enable move_effect and object rendering only");
    }

    aviutl2::live::store_host_version(
        aviutl2::live::kHostVersionSectionEndpoints);
    {
        const Json document = capabilities();
        const Json* const host = document.find("result")->find("host");
        require(
            host->find("sdk_frame_marks")->as_bool() &&
                host->find("sdk_move_effect")->as_bool() &&
                host->find("sdk_object_rendering")->as_bool() &&
                host->find("sdk_section_endpoints")->as_bool(),
            "a 2.1.4 host should enable section endpoints and frame marks");
    }

    aviutl2::live::store_host_version(0U);
}

void test_scene_crud_dispatch() {
    FakeSdkAdapter sdk;
    CommandDispatcher dispatcher(sdk, 4242U);

    const auto call =
        [&dispatcher](const std::string_view payload) {
            return aviutl2::live::parse_json(
                dispatcher.handle_payload(payload));
        };

    {
        sdk.list_scenes_result = SceneListResult{};
        sdk.list_scenes_result.ok = true;
        sdk.list_scenes_result.scenes = {
            SceneListItem{0, "Scene 0"},
            SceneListItem{1, "Scene 1"},
        };
        const Json document = call(
            R"({"id":"sl1","protocol_version":1,"method":"scene.list","params":{}})");
        const Json* const result = document.find("result");
        require(
            document.find("ok")->as_bool() &&
                result->find("count")->as_integer() == 2 &&
                result->find("scenes")->as_array().at(0)
                        .find("name")
                        ->as_string() == "Scene 0" &&
                result->find("scenes")->as_array().at(1)
                        .find("scene_id")
                        ->as_integer() == 1,
            "scene.list should enumerate scene names");
    }

    {
        sdk.project_mutation_result = ProjectMutationResult{};
        sdk.project_mutation_result.ok = true;
        sdk.project_mutation_result.revision = 77;
        const Json document = call(
            R"({"id":"sc1","protocol_version":1,"method":"scene.create","params":{"name":"New Scene","width":1920,"height":1080,"rate":30,"scale":1,"sample_rate":48000,"background":{"r":16,"g":32,"b":48}}})");
        require(
            document.find("ok")->as_bool() &&
                document.find("result")
                    ->find("revision")
                    ->as_integer() == 77 &&
                document.find("result")
                    ->find("non_undoable")
                    ->as_bool(),
            "scene.create should report the post-create revision");
    }

    {
        const Json document = call(
            R"({"id":"sc2","protocol_version":1,"method":"scene.create","params":{"name":"New Scene","width":1920,"height":1080,"rate":30,"scale":1}})");
        require(
            !document.find("ok")->as_bool() &&
                document.find("error")
                    ->find("code")
                    ->as_string() == "INVALID_ARGUMENT",
            "scene.create without sample_rate should be rejected");
    }

    {
        const Json document = call(
            R"({"id":"sw1","protocol_version":1,"method":"scene.switch","params":{"scene_id":3}})");
        require(
            document.find("ok")->as_bool() &&
                sdk.last_scene_id == 3,
            "scene.switch should forward the scene id");
    }

    {
        const Json document = call(
            R"({"id":"pc1","protocol_version":1,"method":"project.create","params":{"width":1920,"height":1080,"rate":30,"scale":1,"sample_rate":48000,"show_confirm":false}})");
        require(
            document.find("ok")->as_bool() &&
                !sdk.last_show_confirm,
            "project.create should forward show_confirm");
    }

    {
        const Json document = call(
            R"({"id":"po1","protocol_version":1,"method":"project.open","params":{"file":"C:/video/test.aup2","show_confirm":false}})");
        require(
            document.find("ok")->as_bool() &&
                !sdk.last_show_confirm,
            "project.open should accept a project path");
    }

    {
        const Json document = call(
            R"({"id":"ps1","protocol_version":1,"method":"project.save","params":{"file":"C:/video/out.aup2"}})");
        require(
            document.find("ok")->as_bool(),
            "project.save should accept a project path");
    }

    {
        sdk.export_result = ExportStartResult{};
        sdk.export_result.ok = true;
        const Json document = call(
            R"({"id":"ex1","protocol_version":1,"method":"export.start","params":{"output_file":"C:/video/out.mp4","output_plugin":"拡張編集ファイル出力"}})")
            ;
        require(
            document.find("ok")->as_bool() &&
                document.find("result")
                    ->find("started")
                    ->as_bool(),
            "export.start should report the async start");
    }

    {
        const Json document = call(
            R"({"id":"ex2","protocol_version":1,"method":"export.start","params":{"output_file":"C:/video/out.mp4"}})");
        require(
            !document.find("ok")->as_bool() &&
                document.find("error")
                    ->find("code")
                    ->as_string() == "INVALID_ARGUMENT",
            "export.start without output_plugin should be rejected");
    }

    {
        sdk.flag_result = ObjectFlagResult{};
        sdk.flag_result.ok = true;
        sdk.flag_result.flag = true;
        const Json document = call(
            R"({"id":"fg1","protocol_version":1,"method":"object.flag.get","params":{"expected_revision":42,"target":{"object_id":"obj-42-3"},"kind":"clipping_object"}})");
        require(
            document.find("ok")->as_bool() &&
                document.find("result")
                    ->find("flag")
                    ->as_bool() &&
                sdk.last_revision == 42,
            "object.flag.get should read the requested flag");
    }

    {
        sdk.project_mutation_result = ProjectMutationResult{};
        sdk.project_mutation_result.ok = true;
        sdk.project_mutation_result.revision = 43;
        const Json document = call(
            R"({"id":"fs1","protocol_version":1,"method":"object.flag.set","params":{"expected_revision":42,"target":{"object_id":"obj-42-3"},"kind":"enable_camera","flag":false}})");
        require(
            document.find("ok")->as_bool() &&
                !sdk.last_flag_value &&
                sdk.last_flag_kind ==
                    aviutl2::live::ObjectFlagKind::enable_camera,
            "object.flag.set should forward the flag write");
    }

    {
        sdk.stable_id_result = StableIdResult{};
        sdk.stable_id_result.ok = true;
        sdk.stable_id_result.id = 9001;
        const Json document = call(
            R"({"id":"oi1","protocol_version":1,"method":"object.id.get","params":{"expected_revision":42,"target":{"object_id":"obj-42-3"}}})");
        require(
            document.find("ok")->as_bool() &&
                document.find("result")
                    ->find("id")
                    ->as_integer() == 9001,
            "object.id.get should return the stable object id");
    }

    {
        const Json document = call(
            R"({"id":"ei1","protocol_version":1,"method":"effect.id.get","params":{"expected_revision":42,"target":{"object_id":"obj-42-3"},"effect":"ドロップシャドウ"}})");
        require(
            document.find("ok")->as_bool() &&
                sdk.last_effect_name == L"ドロップシャドウ",
            "effect.id.get should resolve the named effect");
    }

    {
        const Json document = call(
            R"({"id":"du1","protocol_version":1,"method":"scene.duplicate","params":{}})");
        require(
            !document.find("ok")->as_bool() &&
                document.find("error")
                    ->find("code")
                    ->as_string() == "SDK_METHOD_UNAVAILABLE",
            "scene.duplicate should stay blocked without an SDK API");
    }

    {
        const Json document = call(
            R"({"id":"hu1","protocol_version":1,"method":"history.undo","params":{}})");
        require(
            !document.find("ok")->as_bool() &&
                document.find("error")
                    ->find("code")
                    ->as_string() == "SDK_METHOD_UNAVAILABLE",
            "history.undo should stay blocked without an SDK API");
    }
}

int run_echo_server(const std::wstring& pipe_name) {
    std::atomic_bool request_seen = false;
    PipeServer server(
        pipe_name,
        [&request_seen](const std::string_view payload) {
            request_seen.store(true, std::memory_order_release);
            return std::string(payload);
        });
    std::string error;
    if (!server.start(error)) {
        std::cerr << error << '\n';
        return 2;
    }
    std::cout << "READY\n" << std::flush;
    const ULONGLONG deadline = GetTickCount64() + 10000U;
    while (!request_seen.load(std::memory_order_acquire) &&
           GetTickCount64() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    const bool completed = request_seen.load(std::memory_order_acquire);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    server.stop();
    return completed ? 0 : 3;
}

}  // namespace

int main(const int argument_count, char** arguments) {
    try {
        if (argument_count == 3 &&
            std::string_view(arguments[1]) == "--echo-pipe") {
            const std::string pipe_utf8(arguments[2]);
            const std::wstring pipe_name(pipe_utf8.begin(), pipe_utf8.end());
            return run_echo_server(pipe_name);
        }
        test_json();
        test_host_snapshot_with_multi_layer_object();
        test_host_native_effect_reorder();
        test_host_native_media_trim();
        test_host_frame_marks();
        test_host_mark_dispatch();
        test_protocol_and_fixtures();
        test_host_version_gates();
        test_sessions_events_and_audio();
        test_pipe_server();
        test_instance_registry();
        test_scene_crud_dispatch();
        std::cout << "All AviUtl2LiveBridge native tests passed.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Test failure: " << error.what() << '\n';
        return 1;
    }
}
