# AviUtl2 SDK baseline

- Source: <https://github.com/sevenc-nanashi/aviutl2_sdk_mirror>
- Type: unofficial mirror of the official AviUtl2 SDK
- Pinned submodule commit: `640ab829d7915247b233f9de6a936d2a20ad0ffc`
- Commit timestamp: `2026-09-19T09:09:16Z`
- Mirror changelog date: `2026-09-19`
- `plugin2.h` SHA-256:
  `6162542CA98CEACC36FCA5A055094D2B6300CF224F3044AC050A03E649B495E5`
- Live Bridge minimum host version: `2003300`

The SDK headers are consumed without local modification. Update the submodule and this
record in an isolated change after reviewing header/API/ABI differences and rerunning
the native and AviUtl2 integration tests.

The mirror 2026-09-19 changelog entry corresponds to the official 2026-09-19 SDK release
notes, so this baseline is recorded as the mirror 2026-09-19 snapshot of the official
2026-09-19 SDK. The entry adds the auf2 blend-mode-force and group-control members, the
second FILTER_ITEM_BUTTON callback form, and FLAG_HIDEMENU; and the aux2 scene
enum/select/create, project create/open/save, output_file(), object flag get/set,
object/effect stable IDs, EDIT_INFO background color, and CHANGE_EDIT_STATE event type.