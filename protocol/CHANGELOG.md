# Live Bridge protocol changelog


## Protocol v1 - plugin/client 0.10.0

- Adopted the 2026-09-19 official SDK mirror (`mirror-2026-09-19`) end to end:
  the 0.10.0 feature set builds on the 0.9.7 baseline update.
- Added scene CRUD on AviUtl2 2.1.10+ (`sdk_scene_crud` host flag, host version
  gate 2011000): `scene.list`, `scene.create`, and `scene.switch`. Scene
  delete and duplicate remain unsupported because the official SDK does not
  expose those operations; they answer `SDK_METHOD_UNAVAILABLE` and stay out
  of the MCP method catalog.
- Added scene background color: `scene.get_current` / `scene.update_current`
  now carry the scene background color in the 0-255 RGBA convention.
- Added project file commands on 2.1.10+: `project.create`, `project.open`,
  and `project.save` with explicit `show_confirm=false` automation handling.
- Added `export.start` on 2.1.10+: starts an asynchronous file export through
  a host output plugin (plugin name is caller-supplied; the SDK exposes no
  enumeration API). Export completion is observed via `edit_state_changed`
  events whose payload is null; the recommended completion check combines the
  event stream with `project.get_info` `edit_state` polling and an output file
  existence probe.
- Added object flags on 2.1.10+: `object.flag.get` / `object.flag.set` for
  `group`, `camera`, and `clipping` flags with revision-checked writes,
  apply-before-value rollback, and edit-section semantics.
- Added stable IDs on 2.1.10+: `object.id.get` and `effect.id.get` return the
  SDK int64 stable identifiers.
- Registered the sequenced `edit_state_changed` notification (edit/preview
  playback and file output transitions).
- Added MCP Phase 1: the `aviutl2-mcp` entry point serves nine hub-spoke tools
  (`bridge_card`, `bridge_status`, `bridge_call`, `bridge_help`, `bridge_find`,
  `bridge_snapshot`, `bridge_render_object`, `bridge_render_object_audio`,
  `bridge_watch_events`) over stdio on Windows-local hosts. Method exposure is
  gated by the running host capability manifest, fails closed for unsupported
  features, hides unsupported methods from the catalog, and requires explicit
  `confirm_non_undoable` acknowledgment for non-undoable calls.
- The Python client gained `LiveClient` methods for every new wire command
  (`list_scenes`, `create_scene`, `switch_scene`, `create_project`,
  `open_project`, `save_project`, `start_export`, `get_object_flag`,
  `set_object_flag`, `get_object_id`, `get_effect_id`) plus typed result
  models in `aviutl2_api.live.project_files`.
- Release gate update: `sdk_scene_crud` is now satisfied by the 2.1.10 host
  implementation; the remaining 1.0 blocker is `sdk_undo_redo`.

## Protocol v1 - plugin/client 0.9.7

- Adopted the 2026-09-05 official SDK mirror (`mirror-2026-09-05`). New SDK
  members are appended-only struct members; the bridge stores the host version
  reported by `InitializePlugin` and gates every new call on it.
- Added native effect reorder: `object.effect.reorder` uses `move_effect` on
  AviUtl2 2.1.3+ (`reorder_backend: "sdk_move_effect"`), keeping the verified
  alias replacement fallback on older hosts.
- Added native media trim: `media.trim` moves section endpoints directly on
  2.1.4+ (`backend: "native"`, `trim_fixed_speed: "sdk_move_object_section"`),
  with collision preflight, LIFO rollback, and read-back verification.
- Added frame marks API on 2.1.4+: `mark.list`, `mark.set`, `mark.clear`,
  `mark.move` with revision checks, occupancy validation, and explicit
  non-undoable confirmation. Marks are not part of the timeline revision hash.
- Added object-level rendering on 2.1.3+: `object.render_frame` (revision-bound
  PNG capture) and `object.render_audio` (stereo f32le PCM with frame-loop
  accumulation and SHA-256 integrity).
- Added capability sections `host` (`required`, `version`, `sdk_frame_marks`,
  `sdk_move_effect`, `sdk_object_rendering`, `sdk_section_endpoints`) and
  `marks` (limits, gating, `non_undoable: true`).
- Inspection now reports effect item types 17-19 as `number_group`, `group`,
  and `separator`.

## Protocol v1 - plugin/client 0.9.6

- Added nullable `project_file_path` to `project.get_info`. The value is copied
  from the official project lifecycle callback and may be null for an unsaved
  or not-yet-observed project.
- Added sequenced `project_loaded` and `project_saving` event types.
  `project_saving` is a pre-save observation and is explicitly not a success
  receipt. Callback-scoped metadata is copied; no SDK pointer is retained.
- Added capability flags `local_project`, `lossless_aup2_document`,
  `guarded_checkpoint_save`, `explicit_plan_sync`,
  `project_path_observation`, and `project_lifecycle_notifications`.
- Added Python `LocalProject` and `SyncSession`. They reuse additive protocol v1
  `EditPlan` methods; no project open/save wire command or automatic
  synchronization was introduced.
- Unified the Python Local/Live/Sync query surface, structured high-level sync
  recovery errors, and documented the safe root-import workflow in a compact
  Agent API Card. These are Python workflow additions and add no wire method.
- Protocol v1 remains backward compatible. Old clients ignore the added
  `project.get_info` field and event names.

## Protocol v1 - plugin/client 0.9.5

- Added the backend-neutral `effect(profile, ...)` and `native_effect()`
  specifications. Twenty versioned standard profiles share one project-format
  `2001901` compatibility manifest between Live Bridge and `.aup2` generation.
- Added ordered create-time `effects[]` to Alias and native-media commands.
  The host validates catalog name, item schema, domain and limits before the
  edit section, then verifies every value, enabled state and stack order by
  SDK readback. A mismatch is rolled back and never reported as success.
- Added `apply_effects()` and `validate_standard_effects()` for `.aup2` models.
  Standard Effects are emitted as complete templates after standard
  drawing/playback Effects, matching AviUtl2's Open/Save canonical order, and
  IDs are renumbered so serialization preserves that order.
- Manifest `2001901` explicitly accepts both generated project version
  `2001901` and AviUtl2 Open/Save version `2010200`; unknown future versions
  still fail closed. The canonical `effect.disable=1` metadata is preserved
  and validated for disabled standard Effects.
- Added a semantic Open/Save comparator which ignores known default additions,
  numeric formatting, IDs, property order and `Group2`/`Group3`, while treating
  Effect fallback, removal, reorder and explicit-value changes as failures.
- General text is no longer inferred to be subtitles. Live subtitle overlap is
  opt-in with `subtitle_overlap="warn"` or `"error"`; legacy CLI
  `--warn-overlap` remains an explicit diagnostic.
- Added capability keys for semantic profiles, native fallback, create-time
  stacks, media-group routing, linear values and the `.aup2` manifest version.
- Media-group routing accepts both separate video/audio objects and AviUtl2's
  combined `動画ファイル` + `映像再生` object. Native video transforms target
  `映像再生`; combined audio Effects may intentionally share the video object.
- Protocol v1 remains additive and backward compatible. A 0.9.4 plugin only
  receives the documented single-effect fallback; create-time stacks fail
  closed rather than being split into multiple Undo units.

## Protocol v1 - plugin/client 0.9.4

- Added backend-neutral Python `EditPlan` intents and the stateful `LiveProject`
  facade. Cursor placement, serial/parallel sequencing, native media duration,
  free-layer selection, compact search, revision refresh, and operation IDs are
  managed by the facade.
- Added high-level `linear(start, end)` transforms for animated text/shape
  creation and semantic `star`, `heart`, and `background` basic shapes. They
  compile to native Alias tracks without exposing localized item names.
- Added additive `edit.plan.validate` and `edit.plan.apply` methods for mixed
  Alias/media creation, existing-object item/name/placement updates, deletion,
  effect creation, and effect enable state.
- Unified plans preflight every target, revision, lock, item, media path, and
  final placement. Move/delete targets can be held at scratch positions so a
  creation may safely occupy a range freed by the same plan.
- Successful unified plans use one AviUtl2 edit section and report
  `undo_grouped: true`, `atomic: false`, per-command status, and fresh revision.
  Failures expose best-effort rollback status and whether GUI Undo is required;
  partial application is never hidden.
- Added legacy Python fallback: Alias-only plans use `batch.apply`, supported
  existing-object plans use `timeline.transaction.apply`, and mixed plans are
  refused against older plugins.
- Protocol v1 remains backward compatible. Project save/export/playback and API
  lock removal remain intentionally absent.

## Protocol v1 - plugin/client 0.9.3

- Fixed `project.get_snapshot` for objects that occupy multiple timeline
  layers. AviUtl2 reports such an object while each occupied layer is searched,
  but reports its base layer in the returned range; the Bridge now captures it
  once on that base layer instead of rejecting the snapshot with
  `INVALID_HOST_OBJECT_RANGE`.
- Improved invalid host-range diagnostics with the queried layer/frame and the
  range returned by AviUtl2.
- Python `media.inventory` now uses a 120-second default timeout because native
  validation of every file item can legitimately exceed the general 5-second
  request timeout in edited projects with many assets.
- Timeline overlap remains visible in preflight but is advisory rather than a
  release-blocking error because same-layer overlap is valid for transitions.
- Protocol v1 remains backward compatible.

## Protocol v1 - plugin/client 0.9.2

- Raised the pipe limit to eight simultaneous clients. SDK work uses one FIFO
  scheduler; `event.watch` long-polling does not occupy it.
- Added connection-bound `session.open`, idempotent mutation `operation_id`
  receipts, payload-conflict rejection, and a bounded result cache.
- Added a sequence-numbered ring buffer over the four official SDK events and
  filtered `event.watch` with overflow/resync reporting.
- Added filtered/paged snapshots, layer updates, object names, effect
  add/enable/delete/reorder, section operations, and font/palette/module/track
  group inspection.
- Added verified Alias-replacement duration/trim/reorder, multi-object timeline
  transactions, explicit object groups, shift/ripple/gap operations, and
  fail-closed rejection of unsafe structural edits.
- Added current-scene get/update. Scene settings require
  `confirm_non_undoable=true`; official-SDK scene CRUD and Undo/Redo remain
  unavailable and block the 1.0 release gate.
- Added media inventory/relink and fresh-snapshot diff reporting of every object
  generated by native media placement.
- Mutation receipts now report the resulting revision, snapshot requirement,
  Undo unit, warnings, and unambiguous updated-object references where
  available.
- Added native stereo float PCM render/chunk/release with revision checks,
  SHA-256, TTL, and memory limits.
- Added Python SRT/WebVTT placement, common-effect catalog verification, audio
  peak/RMS/clipping/silence/EBU R128 analysis, native multi-frame contact
  sheets, preflight, and `EditingSession`.
- Frame/contact-sheet/PCM file writes refuse an existing destination unless
  `overwrite=True`.
- Project open/save/save-as, playback controls, encoder/export/upload, and scene
  deletion are intentionally not exposed.
- Protocol v1 remains backward compatible. Package, CMake, plugin, changelog,
  and capability manifest use version 0.9.2.

## Protocol v1 - plugin/client 0.7.1

- Fixed basic media split for host Aliases that contain a top-level
  `frame=start,end` range.
- Fixed split of static playback-position tracks encoded by AviUtl2 as equal
  endpoints plus mode/flags.
- The source is moved to a verified empty scratch range before adjacent
  left/right clips are created, because deletion does not free its range until
  an edit section ends.
- Added failed-stage diagnostics and a regression test that strips only the
  top-level Alias frame range.
- Protocol v1 remains backward compatible.

## Protocol v1 - plugin/client 0.7.0

- Added paged `effect.catalog` and revision-scoped `project.get_layers`.
- Layer name, enable, and lock state now contribute to the project revision.
- Added native `object.effect.add` and duplicate-aware
  `object.effect.delete`.
- Added guarded `object.split_media` with playback-rate source-position
  compensation and in-section rollback.
- Added Python `set_playback_rate()`, verified `duplicate_object()`, typed
  catalog/layer results, and typed media split results.
- Synchronized package, CLI, CMake project, and plugin versions.
- Protocol v1 remains backward compatible.

## Protocol v1 - plugin 0.6.0

- Added `frame.render`, backed by AviUtl2's native
  `rendering_scene_video()` API.
- Added bounded `frame.read_chunk` and idempotent `frame.release`.
- PNG captures include frame, dimensions, scene ID, revision, byte size, and
  SHA-256 metadata.
- Added Python `render_frame()` with chunk, PNG signature, byte-size, and
  SHA-256 verification.
- Protocol v1 remains backward compatible.

## Protocol v1 - plugin 0.5.1

- Removed the always-visible registered window client that consumed AviUtl2
  workspace space.
- `設定 > 外部API連携設定...` now opens an independent, non-docked tool window
  only when requested.
- External access still starts disabled per process; protocol v1 is unchanged.

## Protocol v1 - plugin 0.5.0

- Added `media.probe` using AviUtl2's native strict media support check.
- Added `object.create_from_media_file` with native auto-length support.
- Added stale-safe `object.inspect` for effects, typed items, raw values,
  effect locks, and track movement metadata.
- Added Python `set_property`, `set_animation`, and `set_media_file` helpers.
- Mutations and creation now enforce AviUtl2's built-in layer/effect locks.
- Existing protocol v1 methods remain wire compatible.

## Protocol v1 - plugin 0.4.1

- Preserve the current text/effect label next to `🔒` when locking an object
  that previously used AviUtl2's automatic timeline label.
- Restore automatic-label mode when that object is unlocked.

## Protocol v1 - plugin 0.4.0

- Added the AviUtl2 object-menu API edit lock and visible `🔒` name marker.
- Added `api_locked` to snapshot objects.
- Added the stable `OBJECT_API_LOCKED` mutation error.

## Version 1 — 2026-07-25

- Added 4-byte unsigned little-endian length framing for UTF-8 JSON payloads.
- Added `system.hello`, `system.ping`, and `system.get_capabilities`.
- Added `project.get_info`.
- Added structured success/error envelopes.
- Set the maximum request and response payload to 1 MiB.
## Protocol v1 - Phase 3

- Added `project.get_snapshot` with revision-scoped temporary object IDs.
- Added `object.set_item` and one-Undo `object.set_items`.
- Added `object.move` and `object.delete`.
- Added content-revision validation and `STALE_PROJECT_STATE`.

## Protocol v1 - Phase 2

- Added `object.create_from_alias`.
- Added `batch.validate` and `batch.apply`.
- Added structured placement collision details and partial-application counts.
- Added capability metadata for Alias size, batch size, atomicity, and Undo grouping.
- Added fail-closed, per-process external API permission and a PID-bearing
  AviUtl2 status panel.
