# CLAUDE.md

Claude Codeがこのリポジトリで作業するときの、現在の最小ガイダンスです。
API一覧や`.aup2` item定義をこのファイルへ複製しません。必要な文書だけを段階的に
参照してください。

## プロジェクトの二つのbackend

- `.aup2` backend: project fileをPython modelとしてparse・編集・serializeする。
- Live Bridge backend: ユーザーが開いているAviUtl2を公式SDK経由で編集・検査する。

通常のlive編集は`aviutl2_api.live.LiveProject`を使います。低水準`LiveClient`は、
raw item、完全Alias、明示revision、未ラップendpointが必要な場合だけ使います。

## 文書の選び方

最初から全docをコンテキストへ読み込まないでください。

| 目的 | 読む文書 |
|---|---|
| LLMへ最小API契約を渡す | `docs/AGENT_API_CARD.md` |
| Live Bridgeを使って編集する | `docs/LIVE_BRIDGE_AGENT_QUICK_START.md` |
| Python型、全method、error、制約を調べる | `docs/LIVE_BRIDGE_AGENT_API_MANUAL.md`の該当節 |
| Named Pipe client/pluginを実装する | `docs/LIVE_BRIDGE_PROTOCOL.md` |
| native pluginをbuild・試験する | `docs/LIVE_BRIDGE_DEVELOPMENT.md` |
| `.aup2` parser/serializerを変更する | `docs/aup2_format_specification.md` |
| 0.9.6の変更・移行を説明する | `docs/releases/v0.9.6.md` |
| CLIを使う | `aviutl2 --help`と各subcommandの`--help` |

## Live Bridgeの必須ルール

1. 対象ウィンドウの外部API連携はユーザーがGUIで明示的にEnableする。APIから
   Enableやlock解除を行わない。
2. 複数instanceがあればPIDを明示し、`hello()`のPID/versionとcapabilityを確認する。
3. mutationはfresh revisionから実行し、返されたfresh object参照を使う。
4. 複数操作は可能なら`EditPlan`でvalidateしてからapplyする。
5. `atomic=False`とpartial rollbackを隠さない。`gui_undo_required=True`なら停止して
   ユーザーへ報告する。
6. Effect名・item名・enum・第三者Effectの意味を推測しない。semantic profile、
   catalog、inspection、readbackを使う。
7. native PNG/PCMで確認できない結果を、見た目確認済みと報告しない。
8. project open/save/save-as、export、playback、API lock解除はLive Bridgeの対象外。

外部連携はウィンドウごとに既定OFFです。Enable後は同一Windowsユーザーのlocal
processがclientになり得ます。session IDは認証tokenではなく、API lockはGUIや別plugin、
process injection、直接file編集を防ぐsecurity sandboxではありません。

## `.aup2`編集の必須ルール

- parser/serializerと公開modelを使い、INI風textを独自に継ぎ足さない。
- 新規生成の既定versionは`2001901`。AviUtl2 Open/Save後の`2010200`は、検証済みの
  Effect manifest互換versionとして明示的に扱う。
- 標準Effectは`effect(...)`と`apply_effects()`を使う。raw Effect構築は、正確な
  native schemaをcallerが所有する場合だけにする。
- Open/Save互換はbyte一致ではなく`compare_aup2_roundtrip()`による意味比較を使う。
- 未知version、unknown item、fallback、明示値変更を推測で補正しない。
- file保存はユーザーが指定した出力先だけに行い、既存file上書き条件を明示する。

## Source layout

```text
src/aviutl2_api/              .aup2 model、CLI、共通editing model
src/aviutl2_api/live/         高水準LiveProjectと低水準client
native/AviUtl2LiveBridge/     C++ .aux2 plugin
protocol/                     capability manifest、fixtures、changelog
tests/                        Python/native/manual acceptance
third_party/aviutl2_sdk/      pinned SDK submodule（編集禁止）
```

## Setupと検証

```powershell
git submodule update --init --recursive
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

release前にPython package全体、tests、examplesへ次のquality gateを通します。

```powershell
.\.venv\Scripts\python.exe -m ruff format --check src tests examples
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe -m mypy --strict src/aviutl2_api
```

Native Release:

```powershell
cmake --preset vs2022-x64
cmake --build --preset vs2022-x64-release --parallel 8
ctest --preset vs2022-x64-release --output-on-failure
```

実AviUtl2のload、GUI Undo、native render、media routing、Open/Save roundtripはfake SDK
testでは代替できません。release前に`docs/LIVE_BRIDGE_DEVELOPMENT.md`のmanual integration
を実施します。

## Versionとrelease

package、plugin、CMake、hello fixture、capability manifest、protocol changelogのversionを
同期します。tag `vX.Y.Z`は`pyproject.toml`のversionと一致させます。

`docs/releases/<tag>.md`があればGitHub Actionsはその本文をGitHub Releaseへ使用します。
README、Quick Start、完全APIマニュアル、release noteの例が同じ推奨APIを示すことを
確認してからtagをpushします。
