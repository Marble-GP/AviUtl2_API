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
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m ruff check src tests examples
```

To run the opt-in cross-language named-pipe test:

```powershell
$env:AVIUTL2_NATIVE_TEST_SERVER = (
  Resolve-Path "build/vs2022-x64/native/AviUtl2LiveBridge/Release/AviUtl2LiveBridgeTests.exe"
)
.\.venv\Scripts\python.exe -m pytest tests/integration/test_native_pipe.py -v
```

## AviUtl2 manual integration

1. Install or drag-and-drop the Release `.aux2` into AviUtl2.
2. Start AviUtl2 and open a project.
3. Open the `AviUtl2 Live Bridge` panel. Confirm the initial status is Disabled,
   then check `外部API連携を許可`.
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

## SDK baseline

`third_party/aviutl2_sdk` is pinned to mirror commit
`2fd86528293c32a2da105fdb87060221ed91754b`. See
`third_party/aviutl2_sdk.BASELINE.md` for provenance and hashes. Never edit the SDK
headers locally.
