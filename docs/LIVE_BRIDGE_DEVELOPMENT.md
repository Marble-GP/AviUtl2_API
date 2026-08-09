# AviUtl2 Live Bridge development

## Source layout

```text
src/aviutl2_api/live/       Python discovery, transport, protocol, and client
native/AviUtl2LiveBridge/   Thin C++ `.aux2` bridge
protocol/                   Schemas, fixtures, and protocol changelog
third_party/aviutl2_sdk/    Pinned SDK mirror submodule
```

Initialize dependencies after cloning:

```powershell
git submodule update --init --recursive
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Native build

Visual Studio 2022 with the x64 C++ workload and Windows SDK is required.

```powershell
cmake --preset vs2022-x64
cmake --build --preset vs2022-x64-release -j 8
ctest --preset vs2022-x64-release
```

The plugin is generated at:

```text
build/vs2022-x64/native/AviUtl2LiveBridge/Release/AviUtl2LiveBridge.aux2
```

The required exports are:

```text
GetCommonPluginTable
InitializeLogger
InitializePlugin
RegisterPlugin
RequiredVersion
UninitializePlugin
```

No service or thread is started from `DllMain`; the implementation does not define
`DllMain`. `RegisterPlugin()` creates the SDK edit handle, then posts a private window
message. The message is processed after the registration callback returns and starts
the service. This delay is required because the SDK prohibits using `EDIT_HANDLE`
methods other than `get_host_app_window()` from inside `RegisterPlugin()`.
`UninitializePlugin()` cancels a pending deferred start, removes discovery, signals
pending overlapped I/O, and joins the worker before returning.

The external API is fail-closed every time an AviUtl2 process starts. Permission
is process-local and is not persisted; the pipe and instance discovery entry are
created only while that process is enabled. The registered window client shows the
permission, service state, and PID. Runtime Enable/Disable transitions run off the
AviUtl2 UI thread so stopping a pipe request that is waiting for an SDK edit
callback cannot deadlock the main window.

## Python checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff format --check src tests examples
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe -m mypy --strict src/aviutl2_api
```

0.9.6 removes the legacy CLI/preview-renderer lint and strict-type baseline.
Do not add broad ignores or exclude those modules from the release gate.

To run the opt-in cross-language named-pipe test:

```powershell
$env:AVIUTL2_NATIVE_TEST_SERVER = (
  Resolve-Path "build/vs2022-x64/native/AviUtl2LiveBridge/Release/AviUtl2LiveBridgeTests.exe"
)
.\.venv\Scripts\python.exe -m pytest tests/integration/test_native_pipe.py -v
```

## Security boundary and regression

External access starts disabled for every AviUtl2 process. The Named Pipe rejects
remote clients and restricts its DACL to Local System and the pipe owner, but after
the user enables access, any local process running as the same Windows user may
connect. A session ID provides idempotency, not client authentication.

API/object/layer/effect locks are rechecked with the current revision inside SDK
edit callbacks. They protect only Live Bridge mutations; they do not prevent GUI
editing, another plugin, process injection, direct `.aup2` modification, reads of
Alias/media paths, or an overlay created on another layer.

For an adversarial host regression, lock a disposable object in AviUtl2 and run:

```powershell
$env:PYTHONPATH = "src"
python tests/manual/live_bridge_lock_security_probe.py --pid <PID> --delete-probe
```

The probe covers stale and malformed object IDs, ignored unlock-like fields,
set/move/delete refusal, placement collision, duplicate JSON keys, invalid UTF-8,
oversized/deep frames, interrupted connections, and post-test state equality.
Native tests additionally cover lock marker parsing and dispatch refusal. Never run
the destructive delete probe against a project that has not been saved separately.

## AviUtl2 manual integration

1. Install or drag-and-drop the Release `.aux2` into AviUtl2.
2. Start AviUtl2 and open a project.
3. Open `設定 > 外部API連携設定...`. Confirm the independent settings window
   initially reports Disabled, then check `このウィンドウの外部API連携を許可`.
4. Confirm `<PID>.json` appears below the per-user instance directory.
5. Run:

   ```powershell
   .\.venv\Scripts\python.exe examples/live_hello.py
   ```

6. Confirm `system.hello` and `project.get_info` return the current process/scene.
7. Disable permission and confirm discovery disappears and new connections fail.
   Re-enable it and confirm discovery and connections recover.
8. Start a second AviUtl2 process, enable only one window, and confirm only that PID
   is discoverable. Enable both and confirm a PID-less client raises
   `AmbiguousInstanceError`.
9. Restart AviUtl2 and confirm permission returns to Disabled.
10. Send malformed JSON, an oversized header, and disconnect during a payload; confirm
   AviUtl2 remains alive and a subsequent valid client can connect.
11. Close AviUtl2; confirm the instance file disappears and no worker remains.
12. Repeat launch/close several times and test while previewing and exporting.
13. Select a timeline object and choose `外部API編集ロック > ロック`. Confirm its
    label starts with `🔒`, a fresh snapshot reports `api_locked=true`, and
    set/move/delete return `OBJECT_API_LOCKED`.
14. Confirm normal GUI editing still works while locked, then choose
    `外部API編集ロック > 解除` and confirm API editing works again after taking a
    fresh snapshot.

Actual AviUtl2 load/lifecycle verification cannot be replaced by the SDK-free native
tests and must be recorded before distributing the plugin.

For the 0.9.6 Local/Live synchronization gate, open the same source project in
AviUtl2, enable the target window, and run:

```powershell
.\.venv\Scripts\python.exe tests/manual/sync_096_host_acceptance.py `
  project.aup2 `
  --pid <PID> `
  --video-with-audio short-video.mp4
```

The script applies one text/shape/A/V Effect plan, records native PNG and PCM,
creates a new checkpoint, and waits for one manual Ctrl+Z. It must report
`diverged` after Undo with no Live-only/changed objects. Finally open the emitted
`sync-checkpoint.aup2` manually and compare its image, audio, A/V group, and Effect
stack with the pre-Undo Live result. The original source must remain unchanged.

## SDK baseline

`third_party/aviutl2_sdk` is pinned to mirror commit
`2fd86528293c32a2da105fdb87060221ed91754b`. See
`third_party/aviutl2_sdk.BASELINE.md` for provenance and hashes. Never edit the SDK
headers locally.
