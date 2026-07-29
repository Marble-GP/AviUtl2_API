# AviUtl2 Live Bridge protocol v1

Protocol v1 uses local-only request/response communication over a Windows named
pipe. Up to eight connections may be active; all SDK calls share a fair FIFO
queue, while event long-polls wait outside that queue.

## Endpoint and framing

Each AviUtl2 process listens on:

```text
\\.\pipe\AviUtl2.LiveBridge.<PID>
```

Each message contains:

```text
4-byte unsigned little-endian payload length
UTF-8 JSON payload
```

The payload must contain between 1 byte and 1 MiB. A zero, oversized, incomplete, or
invalid UTF-8/JSON payload is rejected without invoking the SDK. Each connection
has its own session and serial request stream.

The pipe DACL allows the object owner and Local System. Remote pipe clients are
rejected.

## User consent and visibility

External API access is disabled whenever an AviUtl2 process starts. The plugin
registers an `外部API連携設定...` entry in AviUtl2's supported configuration
menu. Selecting it opens an independent tool window with a per-process
permission checkbox and PID-bearing status text. The settings window is not
registered as an AviUtl2 workspace client, is hidden at startup, and therefore
does not consume preview or timeline space.

When disabled, no named pipe server is kept running and no instance discovery file
is published. Enabling access starts both for only that AviUtl2 process; disabling
access removes its discovery entry and stops accepting clients. Permission is not
persisted, so restarting AviUtl2 returns to Disabled.

Each AviUtl2 process has isolated plugin globals and a PID-specific pipe. When
multiple enabled instances are discovered, `LiveClient.connect()` raises
`AmbiguousInstanceError`; callers must use `LiveClient.connect(pid=...)`. The
bridge never guesses which open project should receive an edit.

The current SDK does not provide an API for modifying AviUtl2's built-in toolbar
or status bar. The bridge uses the supported configuration-menu callback and an
ordinary tool window rather than writing into undocumented host controls.

The plugin also registers `外部API編集ロック > ロック` and `解除` in AviUtl2's
object context menu. This is an API-only safety lock: normal GUI edits remain
available. A locked object's visible name is prefixed with `🔒`; if it previously
used AviUtl2's automatic label, the current text or primary effect name is copied
after the marker. The marker is stored with the project and therefore remains
visible after reopening it. Unlocking restores automatic-label mode rather than
leaving that copied label as a custom name.

## Envelopes

Request:

```json
{
  "id": "req-0001",
  "protocol_version": 1,
  "method": "system.hello",
  "params": {}
}
```

Success:

```json
{
  "id": "req-0001",
  "ok": true,
  "result": {}
}
```

Error:

```json
{
  "id": "req-0001",
  "ok": false,
  "error": {
    "code": "HOST_EXPORTING",
    "message": "AviUtl2 is currently exporting.",
    "details": {},
    "retryable": true
  }
}
```

Unknown fields are accepted. Duplicate JSON object keys, excessive nesting, invalid
surrogate pairs, non-finite numbers, and malformed number syntax are rejected.

## Methods

### `system.hello`

Returns `protocol_version`, `plugin_version`, `pid`, `sdk_baseline`, and the current
`edit_state`.

### `system.ping`

Returns `{"pong": true}`.

### `system.get_capabilities`

Returns the protocol version, maximum payload, supported method names, native
versus verified-Alias backends, release-gate blockers, and the four official SDK
notification names.

### `session.open` / `event.watch`

`session.open` binds a session ID to the current pipe connection. Mutations may
include a short `operation_id`; retrying the same session/payload reuses its
cached result, while reusing the ID for another payload returns
`OPERATION_ID_REUSED`.

`event.watch` long-polls the sequence-numbered journal. It accepts
`after_sequence`, `timeout_ms`, and an optional `types` filter. Overflow sets
`resync_required`; events are only change signals, so the client must then fetch
a fresh snapshot. SDK callbacks only append event metadata and never call back
into the SDK.

### `project.get_info`

Returns current scene resolution, frame rate numerator/scale, sample rate, cursor,
maximum frame/layer, scene ID, and edit state. It uses the SDK's locking
`EDIT_HANDLE::get_edit_info()` API through `HostSdkAdapter`.

It does not return or retain SDK handles or SDK-owned pointers.

### `effect.catalog`

Returns a paged catalog from AviUtl2's `enum_effect_name()` and
`enum_effect_item()` APIs. Each effect contains its native category and
video/audio/filter-object/camera flags; each setting item contains its native
type name and numeric type code. `start` defaults to zero and `count` defaults
to 64 (maximum 128). The response includes `total` and `next_start`.

### `project.get_layers`

Returns a revision-scoped page of layer names, built-in enable/lock state,
visible-range membership, and object counts. `start` defaults to zero and
`count` defaults to 128 (maximum 256). The response also includes AviUtl2's
display layer range and current scene ID.

### `media.probe`

Accepts an absolute Windows `file` path. Relative paths are rejected so behavior
does not depend on the Python or AviUtl2 process working directory. The bridge
reports whether the path exists and is a regular file, then calls AviUtl2's
`is_support_media_file(file, false/true)` and `get_media_info()` APIs.

The result includes `extension_supported`, strict `readable`,
`video_track_count`, `audio_track_count`, `duration_seconds`, `width`, `height`,
and a derived `kind` (`image`, `video`, `audio`, `unknown`, or `unsupported`).
The SDK remains the authority on formats and input plugins.

### `object.create_from_media_file`

Creates a media object using AviUtl2's native input pipeline:

```json
{
  "file": "C:\\media\\clip.mp4",
  "layer": 2,
  "frame": 120,
  "length": 0
}
```

`layer` and `frame` are zero-based. `length: 0` delegates duration and any
placement adjustment to AviUtl2. The result returns the actual
`layer`/`frame_start`/`frame_end` and a fresh `created_objects` array containing
every video/audio object generated by the SDK, with the new revision-scoped
object IDs. Locked destination layers return `LAYER_LOCKED`.

### 0.9.2 additive editing/review methods

- Query/catalog: filtered/paged `project.get_snapshot`, `font.catalog`,
  `palette.catalog`, `module.catalog`, `media.inventory`.
- Direct SDK editing: `layer.update`, `object.set_name`,
  `object.effect.add/delete/set_enabled`, and
  `object.section.list/create/delete/move`.
- Verified structural editing: `object.set_duration`, `media.trim`,
  `object.effect.reorder`, and the existing guarded `object.split_media`.
- Timeline: `timeline.transaction.validate/apply`, `timeline.shift_after`,
  `timeline.ripple_insert/delete`, and `timeline.close_gap`.
- Assets/review: `media.relink`, `audio.render/read_chunk/release`, and
  `frame.render/read_chunk/release`.
- Current scene: `scene.get_current` and non-Undo
  `scene.update_current` (requires `confirm_non_undoable=true`).

Every mutation validates `expected_revision` where it targets existing state,
plus API/object/layer/effect locks. Unsafe trim/reorder/split cases return
`STRUCTURAL_EDIT_UNSAFE` rather than guessing.

Successful mutation receipts report the resulting `revision`,
`snapshot_required`, the relevant `updated_object` reference when it can be
identified unambiguously, `undo_unit`, `undo_grouped`, and `warnings`.
Structural split receipts return both replacement object references. Legacy
batch creation cannot obtain a post-edit revision from the current SDK, so it
truthfully returns `revision: null`, `snapshot_required: true`, and a warning.

`scene.list/create/duplicate/switch` and `history.undo/redo` return
`SDK_METHOD_UNAVAILABLE` and are not advertised as supported methods because
the current official SDK cannot execute them. They remain the explicit 1.0
release blockers. Project open/save/save-as, playback, encoder/export/upload,
and scene deletion are intentionally absent.

### `object.create_from_alias`

Creates one timeline object through AviUtl2's
`EDIT_SECTION::create_object_from_alias()` implementation. `layer` and `frame`
are zero-based and `length` is the inclusive object duration in frames.

```json
{
  "alias": "[Object]\r\n[Object.0]\r\neffect.name=テキスト\r\n...",
  "layer": 0,
  "frame": 0,
  "length": 90,
  "client_id": "title"
}
```

The optional `client_id` is returned for client-side correlation. SDK handles and
SDK-owned pointers are never returned.

### `batch.validate`

Accepts one to 128 commands in `params.commands`. Phase 2 supports only:

```json
{
  "op": "object.create_from_alias",
  "alias": "[Object]\r\n...",
  "layer": 0,
  "frame": 0,
  "length": 90,
  "client_id": "title"
}
```

It validates types and bounds, a minimal Alias structure, duplicate `client_id`
values, overlap between commands, and overlap with objects currently on the
requested timeline ranges. Alias data is limited to 256 KiB per command.

The result explicitly reports
`"validation_scope": "structure_and_requested_placement"` and
`"alias_semantics": "verified_on_apply"`. AviUtl2 is the canonical Alias parser,
so complete semantic validation is intentionally deferred until application.

### `batch.apply`

Performs the same placement preflight inside one
`EDIT_HANDLE::call_edit_section_param()` callback, then calls
`create_object_from_alias()` for each command. All successful changes in the call
form one AviUtl2 Undo unit.

Successful results include `applied_count`, correlated `created` entries,
`undo_grouped: true`, and `atomic: false`. The operation is not advertised as
atomic because the SDK forbids deleting an object created in the same edit
section. If AviUtl2 rejects a later Alias, the error reports `applied_count` and
`failed_command_index`; one Undo removes the already-created portion.

Stable Phase 2 validation/edit error codes include:

- `INVALID_ARGUMENT`
- `PLACEMENT_COLLISION`
- `ALIAS_INVALID_OR_PLACEMENT_COLLISION`
- `READ_SECTION_UNAVAILABLE`
- `EDIT_SECTION_UNAVAILABLE`
- `HOST_EXPORTING`

### `project.get_snapshot`

Returns the current scene's objects in layer/start-frame order. SDK-owned handles
and strings are copied inside one read section; handles are never retained.

```json
{
  "revision": 482091,
  "scene_id": 0,
  "object_count": 1,
  "objects": [
    {
      "object_id": "obj-482091-0",
      "layer": 0,
      "frame_start": 0,
      "frame_end": 89,
      "name": null,
      "alias": "[Object]\r\n...",
      "api_locked": false
    }
  ]
}
```

`revision` is a 53-bit content fingerprint of the scene ID, layer
name/enable/lock state, and every object's placement, name, and Alias.
`object_id` is temporary and valid only with that revision. `api_locked`
reports whether the visible object name begins with the bridge's lock marker.

### `object.inspect`

Accepts the same `expected_revision` and temporary `target.object_id` as editing
methods. An optional non-negative `sample_frame` selects where track values are
evaluated; otherwise the object's first frame is used.

The result contains every effect in native order with a stable per-inspection
`index`, duplicate-aware `occurrence` and `selector`, `enabled`/`locked` state,
and every SDK-enumerated item. Items contain the SDK type name and code, their
raw Alias-form value, and track metadata when applicable:

- movement mode and parameters;
- sampled numeric value;
- acceleration/deceleration, midpoint and time-control flags;
- track group count, index, and name.

No SDK handle or pointer leaves the read callback. The bridge caps effects,
items, copied values, and final payload size. If the project changed since the
snapshot, inspection returns `STALE_PROJECT_STATE`.

### `object.effect.add` and `object.effect.delete`

Both methods use the standard revision-scoped target. `object.effect.add`
accepts an `effect` name from `effect.catalog` and appends it through
`EDIT_SECTION::create_effect()`. `object.effect.delete` accepts the
duplicate-aware `selector` returned by `object.inspect` and calls the native
effect deletion API. Object API locks, layer locks, and effect locks are
enforced. Reordering is not advertised because this SDK baseline has no
documented effect move API.

### `frame.render`

Queues the requested non-negative `frame` through AviUtl2's official
`EDIT_HANDLE::rendering_scene_video()` API. The pipe worker waits outside every
read/edit section; the SDK render-thread callback immediately copies the
temporary RGBA buffer. WIC then encodes that copied AviUtl2 result as PNG.

The bridge captures snapshots before and after rendering. If the scene changes
during rendering, the PNG is discarded and `STALE_PROJECT_STATE` is returned.
On success, the result includes:

- `capture_id`, PNG `byte_size`, `chunk_count`, and `ttl_seconds`;
- `frame`, `width`, `height`, `scene_id`, and `revision`;
- `format: "png"`, `native_renderer: true`, and SHA-256.

This is the current scene composite produced by AviUtl2, including its loaded
input plugins, scripts, and effects. It is not an OpenCV/Pillow recreation and
does not include desktop chrome, the timeline, mouse cursor, or editor guides.

### `frame.read_chunk`

Accepts `capture_id` and zero-based `index`. Each decoded chunk contains at most
512 KiB, keeping its base64 JSON response below the 1 MiB protocol limit.
Sequential reads extend the 60-second capture TTL.

### `frame.release`

Releases capture memory. Releasing an already missing capture succeeds with
`released: false`. At most four captures and 64 MiB total capture data are held;
each PNG is limited to 32 MiB. Python `render_frame()` always releases in a
`finally` block and validates chunk offsets, sizes, PNG signature, total size,
and SHA-256 before optionally writing the file.

### `object.set_item` and `object.set_items`

Every edit identifies its target with both fields:

```json
{
  "expected_revision": 482091,
  "target": {"object_id": "obj-482091-0"}
}
```

`object.set_item` adds one `effect`, `item`, and string `value`.
`object.set_items` accepts one to 128 entries:

```json
{
  "expected_revision": 482091,
  "target": {"object_id": "obj-482091-0"},
  "items": [
    {"effect": "標準描画", "item": "X", "value": "120.00"},
    {"effect": "標準描画", "item": "Y", "value": "-45.00"}
  ]
}
```

All entries run in one SDK edit section and form one Undo unit.

### `object.move`

Adds zero-based destination `layer` and `frame` to the revision-scoped target.
The duration and effects are preserved. Occupied destinations return
`PLACEMENT_COLLISION`.

### `object.delete`

Deletes the revision-scoped existing object in one SDK edit section.

### `object.split_media`

Splits a basic `動画ファイル` or `音声ファイル` object at a frame strictly inside
its range. The plugin performs the replacement in one AviUtl2 edit/Undo
section: it preserves the Alias and effects, creates adjacent left/right
objects, and advances the right object's native `再生位置` by
`left_length * playback_rate`. The result reports both actual ranges, source
positions, playback-rate multiplier, and the refreshed revision.

For safety, the method returns `SPLIT_UNSAFE` without changing the project when
the playback position/speed is animated or malformed, the object has multiple
sections, or the primary effect is not a basic video/audio input. If AviUtl2
rejects either replacement, the plugin removes partial replacements and
restores the original Alias. `SPLIT_ROLLBACK_FAILED` is reserved for the
exceptional case where the host also rejects restoration.

Before every mutation, the plugin re-captures the timeline inside the edit
section. If its content no longer matches `expected_revision`, no mutation is
performed and `STALE_PROJECT_STATE` returns `details.current_revision`. Clients
must capture a new snapshot after every successful edit because object IDs are
revision-scoped.

After the revision check, `object.set_item(s)`, `object.move`, and
`object.delete` reject a locked target with `OBJECT_API_LOCKED`. Locking or
unlocking through the GUI changes the object name and revision. Consequently, a
request made from a pre-lock snapshot first receives `STALE_PROJECT_STATE`; a
request made from a fresh locked snapshot receives `OBJECT_API_LOCKED`.

The same mutations reject an AviUtl2 built-in locked source layer with
`LAYER_LOCKED`. Moves and object creation also check the destination layer.
Setting an item on a built-in locked effect returns `EFFECT_LOCKED`.

The Python client builds safer high-level operations on inspection:

- `set_property()` verifies the native item type before using `object.set_item`;
- `set_animation()` also requires native track metadata and serializes
  `AnimatedValue`;
- `set_media_file()` strictly probes the replacement, resolves exactly one
  native `file` item, then performs the stale-safe update.
- `set_playback_rate(rate)` treats `2.0` as 200% and leaves timeline length
  unchanged (`duration_mode="keep_timeline"`).
- `duplicate_object()` clones the host Alias to an empty destination and
  verifies the fresh snapshot.
- `split_media()` exposes the guarded native split described above.

Phase 3 error codes additionally include:

- `STALE_PROJECT_STATE`
- `OBJECT_API_LOCKED`
- `OBJECT_NOT_FOUND`
- `OBJECT_ITEM_NOT_FOUND`
- `ITEM_UPDATE_FAILED`
- `SNAPSHOT_TOO_LARGE`
- `ALIAS_UNAVAILABLE`
- `LAYER_LOCKED`
- `EFFECT_LOCKED`

## Discovery

The plugin atomically publishes:

```text
%LOCALAPPDATA%\AviUtl2LiveBridge\instances\<PID>.json
```

The file is removed during normal plugin shutdown. The Python client validates that
the filename, PID, and expected pipe name agree, then checks that the PID is alive.
Malformed and stale files are ignored.

Schemas and contract fixtures live in `protocol/schema/` and `protocol/fixtures/`.
