# AviUtl2 Project Coding API

## Project Overview

AviUtl ver.2のプロジェクトファイル(.aup2)を操作するためのPython APIライブラリ。
.aup2はINI風のテキストフォーマットであり、パースと生成が可能。これにより、AIエージェントによる自動動画編集を実現する。

## Goals

1. **.aup2ファイルのパース** - テキストファイルをPythonオブジェクトに変換
2. **.aup2ファイルの生成** - Pythonオブジェクトからテキストファイルを出力
3. **JSON変換** - LLMが扱いやすい形式への相互変換
4. **バリデーション** - タイムライン整合性チェック（衝突検出、フレーム計算）

## .aup2 File Format

### 構造

```ini
[project]
version=2001901
file=<filepath>
display.scene=0

[scene.N]
scene=N
name=<scene_name>
video.width=1920
video.height=1080
video.rate=30           # FPS
audio.rate=44100
cursor.frame=0
...

[ObjectID]
layer=<layer_number>
frame=<start>,<end>

[ObjectID.EffectID]
effect.name=<effect_type>
<property>=<value>
...
```

### オブジェクトの種類 (effect.name)

- **動画ファイル** - 動画メディア参照
- **画像ファイル** - 画像メディア参照
- **音声ファイル** - 音声メディア参照
- **図形** - 円、四角形、三角形など
- **テキスト** - テキストオブジェクト
- **標準描画** / **映像再生** - 描画設定（座標、回転、透明度など）
- **フィルタ効果** - グラデーション、ブラー、ライトなど

### 重要なルール

- レイヤー番号が大きいほど上に表示される
- 同一レイヤー・同一時刻に複数オブジェクトは配置不可
- フレーム = 秒数 × video.rate

## Development Stack

- **Language**: Python 3.10+
- **Package Manager**: pip / uv
- **Testing**: pytest
- **Type Checking**: mypy
- **Linting**: ruff

## Directory Structure

```
aviutl2_api/
├── src/
│   └── aviutl2_api/
│       ├── __init__.py
│       ├── models/          # データモデル
│       │   ├── project.py   # AviUtl2Project
│       │   ├── scene.py     # Scene
│       │   ├── timeline.py  # TimelineObject
│       │   └── effects.py   # Effect, Text, Shape, etc.
│       ├── parser.py        # .aup2パーサー
│       ├── serializer.py    # .aup2シリアライザ
│       ├── validator.py     # バリデーションロジック
│       └── json_converter.py # JSON変換
├── tests/
├── samples/                  # サンプル.aup2ファイル
├── pyproject.toml
└── README.md
```

## Development Phases

### Phase 1: Core Parser/Serializer
- [ ] .aup2ファイルの読み込み・パース
- [ ] Pythonオブジェクトから.aup2への出力
- [ ] ラウンドトリップテスト（読み込み→出力→再読み込みで一致）

### Phase 2: Data Models
- [ ] Project, Scene, TimelineObject, Effect クラス設計
- [ ] 型ヒントの完備
- [ ] エフェクトごとのプロパティ定義

### Phase 3: Validation & Logic
- [ ] レイヤー衝突検出
- [ ] フレーム/秒変換ユーティリティ
- [ ] 自動レイヤー割り当て

### Phase 4: JSON Interop
- [ ] JSON エクスポート/インポート
- [ ] スキーマ定義

## Commands

```bash
# 開発環境セットアップ
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# テスト実行
pytest

# 型チェック
mypy src/

# リント
ruff check src/
```

## Notes

- サンプルファイル: `samples/AviUtl2_sample_project_file_1.aup2`
- 元の仕様書: `docs/AgentZero_ProjectDescription.md`
