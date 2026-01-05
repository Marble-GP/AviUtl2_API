# CLAUDE.md

このファイルはClaude Code (claude.ai/code) がこのリポジトリで作業する際のガイダンスを提供します。

## プロジェクト概要

AviUtl ver.2のプロジェクトファイル(.aup2)を操作するためのPython APIライブラリ。
.aup2はINI風のテキストフォーマットであり、パースと生成が可能。これにより、AIエージェントによる自動動画編集を実現する。

**Vision対応LLM連携**: フレームをPNG画像としてレンダリングし、Vision機能を持つLLMが配置確認を自律的に実行できる。

## インストール

### PyPIから（推奨）

```bash
pip install aviutl2-api
```

### 開発版

```bash
git clone https://github.com/Marble-GP/AviUtl2_API.git
cd AviUtl2_API
python -m venv .venv

# 仮想環境をアクティベート（重要！）
# Linux/macOS/WSL:
source .venv/bin/activate

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# Windows Command Prompt:
.\.venv\Scripts\activate.bat

# 開発モードでインストール
pip install -e ".[dev]"
```

**重要**: `aviutl2`コマンドを実行する前に、必ず仮想環境をアクティベートしてください。アクティベート後、プロンプトに`(.venv)`が表示されます。

PowerShellで実行ポリシーエラーが出る場合:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## コマンド

**前提**: すべてのコマンドは仮想環境をアクティベートした状態で実行します。

```bash
# テスト実行
pytest                                          # 全テスト
pytest tests/test_parser.py                     # 特定ファイル
pytest tests/test_parser.py::TestParserBasic    # 特定クラス
pytest tests/test_parser.py::TestParserBasic::test_parse_empty_project  # 単一テスト
pytest -v                                       # 詳細出力

# 型チェック
mypy src/

# リント
ruff check src/

# CLIテスト
aviutl2 --help
aviutl2 preset init
aviutl2 new test.aup2 --width 1920 --height 1080 --fps 30

# フレームプレビュー（Vision AI連携用）
aviutl2 preview project.aup2 --frame 0 -o preview.png
aviutl2 preview project.aup2 --frame 0 -o small.png --max-width 800   # Vision AI向け縮小
aviutl2 preview project.aup2 --frame 0 -o half.png --scale 0.5        # 50%縮小
aviutl2 preview project.aup2 --strip --interval 30 -o timeline.png    # フィルムストリップ
```

## Vision AI連携ワークフロー

AIエージェントが動画編集結果を自己検証するためのフロー：

1. **プロジェクト編集**: CLIでオブジェクト追加・移動・エフェクト適用
2. **フレームレンダリング**: `preview`コマンドでPNG出力（縮小推奨）
3. **Vision確認**: LLMがPNGを読み込み配置・アニメーションを確認
4. **修正ループ**: 問題があれば編集→レンダリング→確認を繰り返す

**重要**: フルHD(1920x1080)画像はAPIエラーの原因となるため、`--max-width 800`等で縮小すること。

### リサイズオプション

| オプション | 説明 |
|-----------|------|
| `--max-width N` | 最大幅をNピクセルに制限（アスペクト比維持） |
| `--max-height N` | 最大高さをNピクセルに制限（アスペクト比維持） |
| `--scale X` | スケール係数（例: 0.5で50%縮小） |

**警告出力**:
- アスペクト比が変更される場合は警告を表示
- 縮小率25%未満: 細部判別困難の警告
- 縮小率50%未満: テキスト/細線の視認性低下の注意

## アーキテクチャ

### データフロー
```
.aup2 file → parser.py → Project/Scene/TimelineObject/Effect → serializer.py → .aup2 file
                                       ↓
                            json_converter.py ↔ JSON (LLM用)
                                       ↓
                              renderer/core.py → PNG frames (Vision AI用)
```

### 主要コンポーネント

- **Models** (`models/`): データ構造
  - `Project`: プロジェクト全体（複数Scene含む）
  - `Scene`: シーン（width, height, fps、複数TimelineObject）
  - `TimelineObject`: タイムライン上のオブジェクト（layer, frame範囲、複数Effect）
  - `Effect`: エフェクト（name, properties辞書）
  - `StaticValue` / `AnimatedValue`: プロパティ値の型

- **Parser** (`parser.py`): .aup2テキストをPythonオブジェクトに変換
- **Serializer** (`serializer.py`): PythonオブジェクトをUTF-8/CRLF形式で出力
- **JSON Converter** (`json_converter.py`): LLM連携用JSON変換
- **Presets** (`presets.py`): アニメーション/エフェクトのプリセット管理
- **CLI** (`cli.py`): Clickベースのコマンドラインツール

- **Renderer** (`renderer/`): フレームプレビュー生成（OpenCV/Pillow使用）
  - `core.py`: FrameRendererがオブジェクトをレイヤー順にレンダリング
  - `canvas.py`: FrameBuffer（リサイズ機能付き）
  - `content/`: 図形、テキスト、画像、動画のレンダラー
  - `filters/`: ぼかし、縁取り、影、グローのフィルタ
  - `transform.py`: 座標変換、回転、拡大縮小
  - `blend.py`: 13種類の合成モード処理
  - `interpolation.py`: アニメーション補間（直線、補間、瞬間、反復、回転）

## ディレクトリ構造

```
src/aviutl2_api/
├── models/
│   ├── project.py    # Project, Scene, TimelineObject, Effect
│   └── values.py     # StaticValue, AnimatedValue, AnimationParams
├── parser.py         # .aup2パーサー
├── serializer.py     # .aup2シリアライザ
├── json_converter.py # JSON変換
├── presets.py        # プリセットシステム
├── cli.py            # CLIツール
└── renderer/         # フレームレンダラー
    ├── core.py       # FrameRenderer
    ├── canvas.py     # FrameBuffer
    ├── interpolation.py  # アニメーション補間
    ├── transform.py  # 座標変換
    ├── blend.py      # 合成モード
    ├── content/      # コンテンツレンダラー
    │   ├── shape.py, text.py, image.py, video.py
    └── filters/      # フィルタエフェクト
        ├── blur.py, border.py, shadow.py, glow.py
```

## .aup2 File Format

### ファイル仕様
- **エンコーディング**: UTF-8 (BOMなし)
- **改行コード**: CRLF (`\r\n`)
- **フォーマット**: INI風テキスト形式

### 座標系
- **X軸**: 右方向が正
- **Y軸**: 上方向が正（スクリーン座標とは逆）
- **Z軸**: 画面奥方向が正

### レイヤーと奥行き
- レイヤー番号が大きいほど**上（手前）**に表示される
- カメラ制御なしの場合、Z座標は描画順序に影響しない（レイヤー番号のみで決まる）
- 典型的な配置: Layer 0に背景/動画、Layer 1以降にアノテーション等

### 構造

```ini
[project]
version=2001901
ファイル=<filepath>
display.scene=0

[scene.N]
scene=N
name=<scene_name>
video.width=1920
video.height=1080
video.rate=30           # FPS
video.scale=1
audio.rate=44100
cursor.frame=0
cursor.layer=0
display.frame=0
display.layer=0
display.zoom=10000
display.order=0
display.camera=
display.grid.x=16,-16
display.grid.y=16,-16
display.grid.width=200
display.grid.height=200
display.grid.step=200.000000
display.grid.range=10000.000000
display.tempo.bpm=120.000000
display.tempo.beat=4
display.tempo.offset=0.000000

[ObjectID]
layer=<layer_number>
focus=1                 # (オプション) 選択状態
frame=<start>,<end>

[ObjectID.EffectID]
effect.name=<effect_type>
<property>=<value>
...
```

### 重要なルール

- レイヤー番号が大きいほど上に表示される
- 同一レイヤー・同一時刻に複数オブジェクトは配置不可
- フレーム = 秒数 × video.rate

---

## プロパティ値の型

### 静的値 (StaticValue)
単一の数値。小数点以下2桁で出力される。
```
拡大率=100.00
透明度=0.00
```

### アニメーション値 (AnimatedValue)
開始値、終了値、移動タイプ、パラメータで構成。
```
<開始値>,<終了値>,<移動タイプ>,<パラメータ>
```

例:
```
X=-1000.00,0.00,補間移動,0
拡大率=100.00,10.00,直線移動,0
X=-100.00,100.00,反復移動,4|5
```

### 文字列値
そのまま出力。
```
フォント=Yu Gothic UI
合成モード=通常
```

### 色値 (重要)
**16進数文字列**として格納。数値ではない。
```
色=ff0000          # 赤
文字色=ffffff      # 白
影色=000000        # 黒
```

**注意**: パーサーで "000000" のような文字列が float(0.0) に変換されないよう、6桁の16進数文字列は文字列として保持する必要がある。

---

## 移動タイプ (Motion Types)

| 日本語名 | 説明 |
|---------|------|
| 直線移動 | 線形補間 |
| 補間移動 | イージング付き補間 |
| 瞬間移動 | 即座に値が変化 |
| 反復移動 | 往復運動 (パラメータ: `回数\|周期`) |
| 回転 | 円周上の移動 (詳細は後述) |
| ランダム移動 | ランダムな移動 |
| 加減速移動 | 加速・減速付き |
| 曲線移動 | ベジエ曲線に沿った移動 |

### 回転モード (重要)

回転モードは X, Y, Z すべてに同時に適用される。

**フォーマット**:
```
X=<中心X>,<初期X>,回転,<パラメータ>
Y=<中心Y>,<初期Y>,回転,<パラメータ>
Z=<中心Z>,<初期Z>,回転,<パラメータ>
```

**回転半径の計算**:
```
radius = sqrt((初期X - 中心X)^2 + (初期Y - 中心Y)^2 + (初期Z - 中心Z)^2)
```

**パラメータ形式**:
```
4|360|<ベジエ曲線プロファイル>
```
- `4`: 制御点タイプ
- `360`: 回転角度（度）
- ベジエ曲線プロファイル: 回転速度/イージングの制御

**例** (中心(0,100,0)、初期位置(0,200,0)、半径100で公転):
```
X=0.00,0.00,回転,4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481
Y=100.00,200.00,回転,4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481
Z=0.00,0.00,回転,4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481
```

---

## ベジエ曲線フォーマット

AviUtl2でベジエ曲線による挙動制御を行う場合の共通フォーマット。

**形式**: `左端点X,左端点Y,左接線X,左接線Y,右端点X,右端点Y,右接線X,右接線Y`

**座標系**:
- X軸: 右向きが正 (0=開始, 1=終了)
- Y軸: 下向きが正 (プロットエリア上端=0°, 下端=360°)

**例**: `0,1,0.485532,-0.475783,1,0,0.484522,-0.481481`

| 要素 | 値 | 意味 |
|------|-----|------|
| 左端点 | (0, 1) | 開始時点、0°位置 |
| 左接線 | (0.485532, -0.475783) | 左端点での微分係数 |
| 右端点 | (1, 0) | 終了時点、360°位置 |
| 右接線 | (0.484522, -0.481481) | 右端点での微分係数 |

---

## エフェクト詳細定義

### 標準描画 (effect.name=標準描画)

すべてのオブジェクトに付与される描画設定。

```ini
effect.name=標準描画
X=0.00                    # X座標
Y=0.00                    # Y座標
Z=0.00                    # Z座標
Group=1                   # グループ番号
中心X=0.00                # 回転中心X
中心Y=0.00                # 回転中心Y
中心Z=0.00                # 回転中心Z
X軸回転=0.00              # X軸周りの回転（カード回転風）
Y軸回転=0.00              # Y軸周りの回転（カード回転風）
Z軸回転=0.00              # Z軸周りの回転（2D平面回転）
拡大率=100.000            # 拡大率 (%)
縦横比=0.000              # アスペクト比
透明度=0.00               # 透明度 (0=不透明, 100=完全透明)
合成モード=通常           # 合成モード（通常、加算、乗算など）
```

**回転軸の違い**:
- **Z軸回転**: 2D平面上での回転（時計/反時計回り）
- **X軸回転/Y軸回転**: 3D回転（遊戯王のカードが回転するようなエフェクト）

### テキスト (effect.name=テキスト)

```ini
effect.name=テキスト
サイズ=34.00              # フォントサイズ
字間=0.00                 # 文字間隔
行間=0.00                 # 行間隔
表示速度=0.00             # 表示速度（0=即座に全表示）
フォント=Yu Gothic UI     # フォント名
文字色=ffffff             # 文字色（16進数文字列）
影・縁色=000000           # 影/縁の色（16進数文字列）
文字装飾=標準文字         # 装飾タイプ（標準文字、影付き文字、縁取り文字など）
文字揃え=左寄せ[上]       # 揃え位置
B=0                       # ボールド (0/1)
I=0                       # イタリック (0/1)
テキスト=内容             # 表示テキスト
文字毎に個別オブジェクト=0
自動スクロール=0
移動座標上に表示=0
オブジェクトの長さを自動調節=0
```

### 図形 (effect.name=図形)

```ini
effect.name=図形
図形の種類=円             # 円、四角形、三角形、五角形、六角形、星形
サイズ=100                # サイズ
縦横比=0.00               # アスペクト比
ライン幅=4000             # 線の太さ（4000=塗りつぶし）
色=ff0000                 # 色（16進数文字列）
角を丸くする=0            # 角丸 (0/1)
```

**図形の種類**:
- 円
- 四角形
- 三角形
- 五角形
- 六角形
- 星形

---

## フィルタエフェクト詳細

### ドロップシャドウ

```ini
effect.name=ドロップシャドウ
X=5                       # X方向オフセット
Y=5                       # Y方向オフセット
濃さ=60.0                 # 影の濃さ
拡散=5                    # 拡散範囲
影色=000000               # 影の色（16進数文字列）
影を別オブジェクトで描画=0
```

### 縁取り

```ini
effect.name=縁取り
サイズ=5                  # 縁の太さ
ぼかし=0                  # ぼかし量
縁色=ffffff               # 縁の色（16進数文字列）
パターン画像=             # パターン画像パス（空=単色）
```

### グロー

```ini
effect.name=グロー
強さ=50.00                # グローの強さ
拡散=60                   # 拡散範囲
角度=25.00                # 光の角度
しきい値=50.0             # しきい値
比率=100.0                # 比率
ぼかし=1                  # ぼかしタイプ
形状=クロス(4本)          # 形状（クロス(4本)、クロス(6本)、ライン等）
光色=                     # 光の色（空=自動、16進数文字列で指定）
光成分のみ=0              # 光成分のみ表示
サイズ固定=0              # サイズ固定
```

### ぼかし

```ini
effect.name=ぼかし
範囲=5.00                 # ぼかし範囲
縦横比=0.00               # 縦横比
光の強さ=0.0              # 光の強さ
サイズ固定=0              # サイズ固定
```

### 振動

```ini
effect.name=振動
X=10.0                    # X方向振幅
Y=10.0                    # Y方向振幅
Z=0.0                     # Z方向振幅
周期=1.0                  # 振動周期
ランダム=0.0              # ランダム度
複雑さ=0.0                # 複雑さ
```

---

## 実装上の注意点

### 色値の取り扱い
色は必ず**16進数文字列**として扱う。パーサーで `"000000"` が `float(0.0)` に変換されないよう注意。
`models/values.py` の `parse_property_value()` で6桁の16進数文字列を検出し、文字列として保持する。

### プロパティ名
AviUtl2は日本語のプロパティ名を使用する。内部名（type, color, blend等）ではなく、日本語名（図形の種類, 色, 合成モード等）を使用すること。

### アニメーションパラメータ
回転モード等でベジエ曲線を使用する場合、パラメータは `|` で区切られた形式で指定する。

## 参考資料

- リファレンスファイル: `samples/PresetDemo_fix.aup2`
- CLI詳細: `docs/CLI_MANUAL.md`
- フォーマット仕様: `docs/aup2_format_specification.md`

## 開発状況

### 完了済み
- [x] .aup2ファイルの読み込み・パース
- [x] Pythonオブジェクトから.aup2への出力（UTF-8, CRLF）
- [x] ラウンドトリップテスト
- [x] Project, Scene, TimelineObject, Effect クラス
- [x] レイヤー衝突検出・自動解決（`--force`フラグ）
- [x] フレーム/秒変換ユーティリティ
- [x] JSON エクスポート/インポート
- [x] CLIツール実装
- [x] プリセットシステム（17種類のサンプルプリセット）
- [x] フレームレンダラー（Vision AI連携用）
- [x] 画像リサイズ機能（アスペクト比警告、品質警告付き）
- [x] オブジェクト編集機能（レイヤー、フレーム範囲、エフェクト名、フォント変更）
- [x] 一括編集機能（正規表現フィルタ、複数オブジェクト同時変更）
- [x] 干渉検出・自動解決機能（レイヤー衝突の自動修正）
- [x] スマート自動化（フレーム範囲自動選択、メディアファイル長さ自動検出）
- [x] プラットフォーム対応コンソール出力（Windows CP932/Unix UTF-8）
- [x] PyPI公開（`pip install aviutl2-api`）

### CLI コマンド一覧

| コマンド | 説明 |
|---------|------|
| `new` | 新規プロジェクト作成 |
| `info` | プロジェクト情報表示 |
| `timeline` | ASCIIタイムライン表示 |
| `preview` | フレームをPNGでレンダリング（Vision AI用） |
| `add text/shape/video/image/audio` | オブジェクト追加 |
| `move` | オブジェクト移動 |
| `delete` | オブジェクト削除 |
| `copy` | オブジェクト複製 |
| `modify` | プロパティ変更（座標、レイヤー、フレーム範囲、エフェクト名等）`--force`で衝突自動解決 |
| `batch` | 一括編集（正規表現フィルタで複数オブジェクトを同時変更） |
| `fix` | 干渉検出・自動解決（プロジェクト全体のレイヤー衝突を修正） |
| `animate` | アニメーション設定 |
| `filter add` | フィルタ追加 |
| `preset list/show/apply/save/delete/init` | プリセット管理 |
| `export-json` / `import-json` | JSON変換 |
