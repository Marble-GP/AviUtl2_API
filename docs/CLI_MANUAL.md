# AviUtl2 CLI マニュアル

**バージョン**: 0.2.0
**最終更新**: 2025-12-27

---

## 概要

`aviutl2` は AviUtl ver.2 プロジェクトファイル (.aup2) を操作するためのコマンドラインツールです。
AIエージェントによる動画編集自動化を目的として設計されています。

### 特徴

- **ステートレス設計**: 各コマンドがファイルを直接読み書き
- **レイヤー自動選択**: `--layer auto` で空きレイヤーを自動検出
- **衝突検知**: オブジェクト配置時にレイヤー衝突を自動チェック
- **重複警告**: 音声など同種オブジェクトの時間重複を警告
- **プリセットシステム**: アニメーション・エフェクトの組み合わせを保存・再利用

---

## インストール

### PyPIから（推奨）

```bash
pip install aviutl2-api
```

### 開発版（ソースから）

```bash
# 仮想環境作成
python -m venv .venv

# 仮想環境をアクティベート
# Linux/macOS/WSL:
source .venv/bin/activate

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# Windows Command Prompt:
.\.venv\Scripts\activate.bat

# 開発モードでインストール
pip install -e ".[dev]"
```

**重要**: `aviutl2` コマンドを実行する前に、必ず仮想環境をアクティベートしてください。
- アクティベート後、プロンプトに `(.venv)` が表示されます
- PyPIからグローバルインストールした場合は不要です

PowerShellで実行ポリシーエラーが出る場合:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## コマンド一覧

| コマンド | 説明 |
|---------|------|
| `new` | 新規プロジェクト作成 |
| `info` | プロジェクト情報表示 |
| `timeline` | ASCIIタイムライン表示 |
| `preview` | フレームをPNGでレンダリング（Vision AI用） |
| `layers` | レイヤー一覧表示 |
| `objects` | オブジェクト一覧表示 |
| `range` | 区間内オブジェクト列挙 |
| `search` | フレームでオブジェクト検索 |
| `check` | 配置可否チェック |
| `add` | オブジェクト追加 (サブコマンド) |
| `move` | オブジェクト移動 |
| `delete` | オブジェクト削除 |
| `copy` | オブジェクト複製 |
| `modify` | オブジェクトプロパティ変更 |
| `batch` | 一括編集（フィルタ機能付き） |
| `fix` | 干渉検出・自動解決 |
| `animate` | アニメーション設定 |
| `filter` | フィルタエフェクト操作 |
| `preset` | プリセット管理 |
| `export-json` | JSON出力 |
| `import-json` | JSONから変換 |

---

## プロジェクト操作

### new - 新規プロジェクト作成

```bash
aviutl2 new <output.aup2> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-w, --width` | 映像幅 | 1920 |
| `-h, --height` | 映像高さ | 1080 |
| `-f, --fps` | フレームレート | 30 |
| `-a, --audio-rate` | 音声サンプルレート | 44100 |

**例:**
```bash
# 1920x1080 30fps のプロジェクト作成
aviutl2 new project.aup2

# 1280x720 60fps のプロジェクト作成
aviutl2 new project.aup2 -w 1280 -h 720 -f 60
```

### info - プロジェクト情報表示

```bash
aviutl2 info <file.aup2>
```

**出力例:**
```
=== プロジェクト情報 ===
ファイル: project.aup2
バージョン: 2001901
シーン数: 1

--- シーン 0: Root ---
  解像度: 1920x1080
  フレームレート: 30fps
  音声レート: 44100Hz
  オブジェクト数: 5
  最大フレーム: 300 (10.00秒)
```

---

## タイムライン表示

### timeline - ASCIIタイムライン

```bash
aviutl2 timeline <file.aup2> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `--from` | 開始フレーム | 0 |
| `--to` | 終了フレーム | 最大フレーム |
| `-l, --layer` | レイヤー指定（例: 0, 0-5, 0,2,4） | 全レイヤー |
| `-s, --scene` | シーン番号 | 0 |
| `-w, --width` | 表示幅(文字数) | 80 |
| `-c, --compact` | 空き領域を省略 | OFF |

**出力例:**
```
タイムライン: フレーム 0 - 300
================================================================================
        |0         30        60        90        120
--------------------------------------------------------------------------------
L00    |テテテテテテテテテテテテテテテテテテテテテテテテテテテテテテ
L01    |図図図図図図図図図図図図図図図
L02    |          音音音音音音音音音音音音音音音音音音音音
================================================================================
凡例: 動=動画 画=画像 音=音声 図=図形 テ=テキスト
```

**例:**
```bash
# レイヤー0-5のみ表示
aviutl2 timeline project.aup2 -l 0-5

# コンパクト表示
aviutl2 timeline project.aup2 --compact

# 複合指定
aviutl2 timeline project.aup2 -l 0,2,4 --from 0 --to 100
```

---

## フレームプレビュー（Vision AI連携）

### preview - フレームレンダリング

プロジェクトのフレームをPNG画像としてレンダリングします。Vision機能を持つLLMが配置確認を行うために使用します。

```bash
aviutl2 preview <file.aup2> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-f, --frame` | レンダリングするフレーム番号 | 0 |
| `-o, --output` | 出力ファイルパス | (必須) |
| `--strip` | フィルムストリップモード（複数フレームを横並び） | OFF |
| `--interval` | ストリップモードのフレーム間隔 | 30 |
| `-s, --scene` | シーン番号 | 0 |
| `--max-width` | 最大幅（ピクセル） | なし |
| `--max-height` | 最大高さ（ピクセル） | なし |
| `--scale` | スケール係数（例: 0.5で50%縮小） | なし |

**例:**
```bash
# 単一フレームをレンダリング
aviutl2 preview project.aup2 --frame 0 -o preview.png

# Vision AI向けに縮小（推奨）
aviutl2 preview project.aup2 --frame 0 -o small.png --max-width 800

# 50%スケールで出力
aviutl2 preview project.aup2 --frame 0 -o half.png --scale 0.5

# フィルムストリップ（30フレーム間隔）
aviutl2 preview project.aup2 --strip --interval 30 -o timeline.png

# 特定フレームを確認
aviutl2 preview project.aup2 --frame 120 -o frame120.png --max-width 800
```

### リサイズオプション

Vision AI APIはフルHD（1920x1080）画像でエラーになることがあります。`--max-width` 等で縮小することを推奨します。

| オプション | 説明 |
|-----------|------|
| `--max-width N` | 最大幅をNピクセルに制限（アスペクト比維持） |
| `--max-height N` | 最大高さをNピクセルに制限（アスペクト比維持） |
| `--scale X` | スケール係数（例: 0.5で50%縮小） |

**自動警告:**
- アスペクト比が変更される場合に警告
- 縮小率50%未満: テキストや細い線が見づらくなる可能性
- 縮小率25%未満: 細部が判別困難になる可能性

### Vision AI連携ワークフロー

AIエージェントが動画編集結果を自己検証するためのフロー：

1. **プロジェクト編集**: CLIでオブジェクト追加・移動・エフェクト適用
2. **フレームレンダリング**: `preview`コマンドでPNG出力（縮小推奨）
3. **Vision確認**: LLMがPNGを読み込み配置・アニメーションを確認
4. **修正ループ**: 問題があれば編集→レンダリング→確認を繰り返す

**例:**
```bash
# オブジェクトを追加
aviutl2 add text project.aup2 "タイトル" --from 0 --to 90 --size 72

# プレビューで確認
aviutl2 preview project.aup2 --frame 45 -o check.png --max-width 800

# LLMがcheck.pngを確認し、修正が必要なら...
aviutl2 modify project.aup2 0 --x 100 --y -50

# 再度プレビュー
aviutl2 preview project.aup2 --frame 45 -o check2.png --max-width 800
```

---

### layers - レイヤー一覧

```bash
aviutl2 layers <file.aup2> [-s SCENE]
```

**出力例:**
```
レイヤー一覧 (使用中: 3レイヤー)
--------------------------------------------------
  レイヤー   0: 2個 (テキスト:2)
  レイヤー   1: 1個 (図形:1)
  レイヤー   2: 1個 (音声ファイル:1)
```

### objects - オブジェクト一覧

```bash
aviutl2 objects <file.aup2> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `-l, --layer` | レイヤー番号でフィルタ |
| `--at` | 指定フレームに存在するオブジェクト |
| `-s, --scene` | シーン番号 |
| `-v, --verbose` | 詳細表示 |

**例:**
```bash
# 全オブジェクト一覧
aviutl2 objects project.aup2

# レイヤー0のオブジェクトのみ
aviutl2 objects project.aup2 -l 0

# フレーム100に存在するオブジェクト（詳細）
aviutl2 objects project.aup2 --at 100 -v
```

---

## 検索・チェック

### range - 区間内オブジェクト列挙

```bash
aviutl2 range <file.aup2> --from <start> --to <end> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `--from` | 開始フレーム (必須) |
| `--to` | 終了フレーム (必須) |
| `--type` | オブジェクトタイプでフィルタ |
| `-s, --scene` | シーン番号 |
| `-v, --verbose` | 詳細表示 |

**例:**
```bash
# フレーム50-150の区間にあるオブジェクト
aviutl2 range project.aup2 --from 50 --to 150

# 音声ファイルのみ
aviutl2 range project.aup2 --from 0 --to 300 --type 音声ファイル
```

### search - フレーム検索

```bash
aviutl2 search <file.aup2> --at <frame> [-s SCENE]
```

### check - 配置可否チェック

```bash
aviutl2 check <file.aup2> --at <start> --to <end> -l <layer> [-s SCENE]
```

**出力例:**
```bash
$ aviutl2 check project.aup2 --at 0 --to 100 -l 0
配置不可: レイヤー 0 フレーム 0-100 には衝突があります:
  - ID 0: テキスト (フレーム 0-150)

$ aviutl2 check project.aup2 --at 0 --to 100 -l 5
配置可能: レイヤー 5 フレーム 0-100
```

---

## オブジェクト追加

### add text - テキスト追加

```bash
aviutl2 add text <file.aup2> <content> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-l, --layer` | レイヤー番号 | auto |
| `--from` | 開始フレーム | (必須) |
| `--to` | 終了フレーム | (必須) |
| `--x` | X座標 | 0.0 |
| `--y` | Y座標 | 0.0 |
| `--size` | フォントサイズ | 34 |
| `-s, --scene` | シーン番号 | 0 |
| `-o, --output` | 出力先 | 入力ファイル |
| `--warn-overlap` | 重複警告ON | OFF |

**例:**
```bash
# 自動レイヤー選択でテキスト追加
aviutl2 add text project.aup2 "Hello World" --from 0 --to 100

# レイヤー指定
aviutl2 add text project.aup2 "固定レイヤー" -l 3 --from 0 --to 100

# 座標とサイズ指定
aviutl2 add text project.aup2 "大きなテキスト" --from 0 --to 100 --x 100 --y -50 --size 72
```

### add shape - 図形追加

```bash
aviutl2 add shape <file.aup2> <type> [OPTIONS]
```

**図形タイプ:** `circle`, `rectangle`, `triangle`, `pentagon`, `hexagon`

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-l, --layer` | レイヤー番号 | auto |
| `--from` | 開始フレーム | (必須) |
| `--to` | 終了フレーム | (必須) |
| `--x` | X座標 | 0.0 |
| `--y` | Y座標 | 0.0 |
| `--size` | サイズ | 100 |
| `--color` | 色 (16進数) | ffffff |
| `-o, --output` | 出力先 | 入力ファイル |

**例:**
```bash
# 赤い円を追加
aviutl2 add shape project.aup2 circle --from 0 --to 100 --color ff0000

# 四角形を右上に配置
aviutl2 add shape project.aup2 rectangle --from 50 --to 150 --x 200 --y -100
```

### add audio - 音声追加

```bash
aviutl2 add audio <file.aup2> <audio_path> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-l, --layer` | レイヤー番号 | auto |
| `--from` | 開始フレーム | (必須) |
| `--to` | 終了フレーム | (必須) |
| `--volume` | 音量 | 100.0 |
| `-o, --output` | 出力先 | 入力ファイル |
| `--warn-overlap` | 重複警告 | **ON** |

**重要:** 音声オブジェクトはデフォルトで重複警告がONです。

**例:**
```bash
# BGMを追加
aviutl2 add audio project.aup2 "C:/music/bgm.mp3" --from 0 --to 300

# 警告を無効化
aviutl2 add audio project.aup2 "se.wav" --from 100 --to 130 --no-warn-overlap
```

### add video - 動画追加

```bash
aviutl2 add video <file.aup2> <video_path> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-l, --layer` | レイヤー番号 | auto |
| `--from` | 開始フレーム | (必須) |
| `--to` | 終了フレーム | (必須) |
| `--x` | X座標 | 0.0 |
| `--y` | Y座標 | 0.0 |
| `-o, --output` | 出力先 | 入力ファイル |
| `--warn-overlap` | 重複警告 | OFF |

### add image - 画像追加

```bash
aviutl2 add image <file.aup2> <image_path> [OPTIONS]
```

**オプション:** `add video` と同様

---

## オブジェクト編集

### move - オブジェクト移動

```bash
aviutl2 move <file.aup2> <object_id> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `-l, --layer` | 移動先レイヤー |
| `--from` | 新しい開始フレーム |
| `--to` | 新しい終了フレーム |
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**例:**
```bash
# レイヤー変更
aviutl2 move project.aup2 0 -l 5

# 時間位置変更
aviutl2 move project.aup2 0 --from 100 --to 200

# 両方変更
aviutl2 move project.aup2 0 -l 3 --from 50 --to 150
```

### delete - オブジェクト削除

```bash
aviutl2 delete <file.aup2> <object_id> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `-s, --scene` | シーン番号 |
| `-y, --yes` | 確認なしで削除 |
| `-o, --output` | 出力先 |

**例:**
```bash
# 確認ダイアログあり
aviutl2 delete project.aup2 3

# 確認なしで削除
aviutl2 delete project.aup2 3 -y
```

### copy - オブジェクト複製

```bash
aviutl2 copy <file.aup2> <object_id> [OPTIONS]
```

**オプション:**
| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-l, --layer` | コピー先レイヤー | auto |
| `--from` | コピー先開始フレーム | 元と同じ |
| `--to` | コピー先終了フレーム | 元と同じ |
| `--offset` | 時間オフセット | なし |
| `-s, --scene` | シーン番号 | 0 |
| `-o, --output` | 出力先 | 入力ファイル |

**例:**
```bash
# 同じ位置に複製（自動レイヤー）
aviutl2 copy project.aup2 0

# 100フレーム後に複製
aviutl2 copy project.aup2 0 --offset 100

# レイヤー5, フレーム200から
aviutl2 copy project.aup2 0 -l 5 --from 200
```

### modify - プロパティ変更

```bash
aviutl2 modify <file.aup2> <object_id> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `--text` | テキスト内容を変更 |
| `--x` | X座標を変更 |
| `--y` | Y座標を変更 |
| `--z` | Z座標を変更 |
| `--scale` | 拡大率を変更 |
| `--opacity` | 透明度を変更（0-100） |
| `--rotation` | 回転角度を変更 |
| `--size` | サイズを変更（テキスト/図形） |
| `--color` | 色を変更（16進数） |
| `--font` | フォントを変更（テキスト） |
| `--volume` | 音量を変更（音声） |
| `--layer` | レイヤー位置を変更（干渉時は警告） |
| `--from` | 開始フレームを変更（干渉時は警告） |
| `--to` | 終了フレームを変更（干渉時は警告） |
| `--effect-name` | メインエフェクト名を変更（画像ファイル、動画ファイルなど） |
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**例:**
```bash
# テキスト内容を変更
aviutl2 modify project.aup2 0 --text "新しいテキスト"

# 座標を変更
aviutl2 modify project.aup2 0 --x 100 --y -50

# 拡大率と透明度を変更
aviutl2 modify project.aup2 0 --scale 150 --opacity 50

# 色を赤に変更
aviutl2 modify project.aup2 0 --color ff0000

# フォントを変更
aviutl2 modify project.aup2 0 --font "MS Gothic"

# レイヤー位置を変更（干渉があれば警告）
aviutl2 modify project.aup2 0 --layer 3

# フレーム範囲を変更
aviutl2 modify project.aup2 0 --from 30 --to 120

# エフェクト名を変更
aviutl2 modify project.aup2 0 --effect-name "画像ファイル"
```

---

### batch - 一括編集（フィルタ機能付き）

```bash
aviutl2 batch <file.aup2> [FILTER_OPTIONS] [MODIFY_OPTIONS]
```

フィルタ条件に一致する複数のオブジェクトを一括で編集します。正規表現による柔軟なフィルタリングが可能です。

**フィルタオプション:**
| オプション | 説明 |
|-----------|------|
| `--filter-type` | オブジェクトタイプでフィルタ（正規表現） |
| `--filter-text` | テキスト内容でフィルタ（正規表現） |
| `--filter-layer` | レイヤー範囲でフィルタ（例: `1-5` または `3`） |
| `--dry-run` | 実行せずマッチするオブジェクトのみ表示 |

**変更オプション (modifyコマンドと同じ):**
| オプション | 説明 |
|-----------|------|
| `--x` | X座標を変更 |
| `--y` | Y座標を変更 |
| `--z` | Z座標を変更 |
| `--scale` | 拡大率を変更 |
| `--opacity` | 透明度を変更（0-100） |
| `--rotation` | 回転角度を変更 |
| `--size` | サイズを変更（テキスト/図形） |
| `--color` | 色を変更（16進数） |
| `--font` | フォントを変更（テキスト） |
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**例:**
```bash
# すべてのテキストオブジェクトの色を赤に変更
aviutl2 batch project.aup2 --filter-type "テキスト" --color ff0000

# "Hello"を含むテキストのフォントを変更
aviutl2 batch project.aup2 --filter-text "Hello.*" --font "MS Gothic"

# レイヤー1-3のすべてのオブジェクトを移動
aviutl2 batch project.aup2 --filter-layer "1-3" --x 100 --y 50

# すべての図形の透明度を50%に設定
aviutl2 batch project.aup2 --filter-type "図形" --opacity 50

# 複数条件を組み合わせ: レイヤー2-5のテキストのみ
aviutl2 batch project.aup2 --filter-type "テキスト" --filter-layer "2-5" --size 40

# ドライラン（マッチするオブジェクトを確認）
aviutl2 batch project.aup2 --filter-text "World" --dry-run
```

**正規表現の例:**
```bash
# "Hello"で始まるテキスト
--filter-text "^Hello"

# "World"で終わるテキスト
--filter-text "World$"

# "test"または"demo"を含むテキスト
--filter-text "(test|demo)"

# 数字を含むテキスト
--filter-text ".*[0-9].*"
```

---

### fix - 干渉検出・自動解決

```bash
aviutl2 fix <file.aup2> [OPTIONS]
```

プロジェクト内の全干渉（同一レイヤー・同一時刻に複数オブジェクトが存在）を検出し、自動的に解決します。

**オプション:**
| オプション | 説明 |
|-----------|------|
| `--dry-run` | 実行せずに干渉のみ表示 |
| `-v, --verbose` | 詳細表示 |
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**動作:**
- 同一レイヤー・同一時刻に複数のオブジェクトが存在する場合、後のオブジェクト（IDが大きい）を下のレイヤーに自動的に押し下げます
- 移動先でも衝突する場合は、さらに下のレイヤーを探します

**例:**
```bash
# 干渉を検出（確認のみ）
aviutl2 fix project.aup2 --dry-run

# 干渉を自動解決
aviutl2 fix project.aup2

# 詳細情報を表示しながら解決
aviutl2 fix project.aup2 --verbose

# 別ファイルに保存
aviutl2 fix project.aup2 -o fixed.aup2
```

**出力例:**
```
干渉検出: 3件

レイヤー 1:
  ID 0 (テキスト): フレーム 0-30
  ID 1 (テキスト): フレーム 0-30
  重複区間: 0-30

干渉を自動解決中...

  ID 1 (テキスト): レイヤー 1 → 2
  ID 2 (図形): レイヤー 1 → 3

2個のオブジェクトを移動しました。
保存先: project.aup2
```

---

## アニメーション

### animate - アニメーション設定

```bash
aviutl2 animate <file.aup2> <object_id> <property> --start <val> --end <val> [OPTIONS]
```

**プロパティ:**
| プロパティ | 説明 |
|-----------|------|
| `x` | X座標 |
| `y` | Y座標 |
| `z` | Z座標 |
| `scale` | 拡大率 |
| `opacity` | 透明度 |
| `rotation` | 回転 |

**移動タイプ (`--motion`):**
| タイプ | 説明 |
|--------|------|
| `linear` | 直線移動（デフォルト） |
| `smooth` | 補間移動（なめらか） |
| `instant` | 瞬間移動 |
| `bounce` | 反復移動 |
| `repeat` | 回転 |

**例:**
```bash
# X座標をアニメーション
aviutl2 animate project.aup2 0 x --start -100 --end 100

# なめらかにフェードイン
aviutl2 animate project.aup2 0 opacity --start 100 --end 0 --motion smooth

# 360度回転
aviutl2 animate project.aup2 0 rotation --start 0 --end 360

# ズームイン
aviutl2 animate project.aup2 0 scale --start 0 --end 100 --motion smooth
```

---

## フィルタエフェクト

### filter add - フィルタ追加

```bash
aviutl2 filter add <file.aup2> <object_id> <filter_type> [OPTIONS]
```

**フィルタタイプ:**
| タイプ | 説明 |
|--------|------|
| `blur` | ぼかし |
| `glow` | グロー（発光） |
| `fade` | フェード |
| `gradient` | グラデーション |
| `shadow` | ドロップシャドウ |
| `border` | 縁取り |
| `mosaic` | モザイク |
| `sharpen` | シャープ |
| `chromakey` | クロマキー |
| `shake` | 振動 |

**オプション:**
| オプション | 説明 |
|-----------|------|
| `--strength` | 強度/範囲 |
| `--color` | 色（16進数） |
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**例:**
```bash
# ぼかしを追加
aviutl2 filter add project.aup2 0 blur --strength 10

# 黄色いグローを追加
aviutl2 filter add project.aup2 0 glow --strength 50 --color ffff00

# フェードを追加
aviutl2 filter add project.aup2 0 fade

# ドロップシャドウを追加
aviutl2 filter add project.aup2 0 shadow --strength 60

# 白い縁取りを追加
aviutl2 filter add project.aup2 0 border --strength 3 --color ffffff

# 振動を追加
aviutl2 filter add project.aup2 0 shake --strength 15
```

---

## プリセット

プリセットはアニメーションとエフェクトの組み合わせを保存・再利用するための機能です。

### preset init - サンプルプリセット初期化

```bash
aviutl2 preset init
```

17種類のサンプルプリセットを登録します。

### preset list - プリセット一覧

```bash
aviutl2 preset list
```

**出力例:**
```
登録済みプリセット (17件):
------------------------------------------------------------
  spin-fade-out        回転フェードアウト        [アニメーション:3]
  fade-in              フェードイン           [アニメーション:1]
  fade-out             フェードアウト          [アニメーション:1]
  slide-in-left        左からスライドイン        [アニメーション:1]
  ...
```

### preset show - プリセット詳細

```bash
aviutl2 preset show <preset_id>
```

**出力例:**
```
=== プリセット: 回転フェードアウト ===
ID: spin-fade-out
説明: 10回転しながら縮小してフェードアウトする定番エフェクト

アニメーション (3件):
  回転: 0 → 3600 (直線移動)
  拡大率: 100 → 10 (直線移動)
  透明度: 0 → 100 (直線移動)
```

### preset apply - プリセット適用

```bash
aviutl2 preset apply <file.aup2> <object_id> <preset_id> [OPTIONS]
```

**オプション:**
| オプション | 説明 |
|-----------|------|
| `-s, --scene` | シーン番号 |
| `-o, --output` | 出力先 |

**例:**
```bash
# 回転フェードアウトを適用
aviutl2 preset apply project.aup2 0 spin-fade-out

# フェードインを適用
aviutl2 preset apply project.aup2 0 fade-in

# スライドインを適用
aviutl2 preset apply project.aup2 0 slide-in-left

# グローパルスを適用
aviutl2 preset apply project.aup2 0 glow-pulse
```

### preset save - プリセット保存

```bash
aviutl2 preset save <file.aup2> <object_id> <preset_id> [OPTIONS]
```

オブジェクトに設定されたアニメーションやエフェクトをプリセットとして保存します。

**オプション:**
| オプション | 説明 |
|-----------|------|
| `-n, --name` | プリセット表示名 |
| `-d, --description` | 説明 |
| `-s, --scene` | シーン番号 |

**例:**
```bash
# オブジェクト0の設定をプリセットとして保存
aviutl2 preset save project.aup2 0 my-cool-effect

# 名前と説明を指定
aviutl2 preset save project.aup2 0 epic-intro -n "エピックイントロ" -d "壮大な登場演出"
```

### preset delete - プリセット削除

```bash
aviutl2 preset delete <preset_id> [-y]
```

**例:**
```bash
# 確認あり
aviutl2 preset delete my-cool-effect

# 確認なし
aviutl2 preset delete my-cool-effect -y
```

### サンプルプリセット一覧

**アニメーション:**
| ID | 名前 | 説明 |
|----|------|------|
| `spin-fade-out` | 回転フェードアウト | 10回転しながら縮小してフェードアウト |
| `fade-in` | フェードイン | 透明から不透明へ |
| `fade-out` | フェードアウト | 不透明から透明へ |
| `slide-in-left` | 左からスライドイン | 画面左から中央へ |
| `slide-in-right` | 右からスライドイン | 画面右から中央へ |
| `slide-out-right` | 右へスライドアウト | 中央から画面右へ |
| `bounce-vertical` | 縦バウンス | Y軸で上下に反復 |
| `bounce-horizontal` | 横バウンス | X軸で左右に反復 |
| `zoom-in` | ズームイン | 小さい状態から拡大 |
| `zoom-out` | ズームアウト | 通常サイズから縮小 |
| `spin-once` | 1回転 | 360度回転 |
| `orbit` | 公転 | 中心を軸に円周上を移動 |

**エフェクト:**
| ID | 名前 | 説明 |
|----|------|------|
| `shake` | 振動 | 振動エフェクト |
| `glow-pulse` | グローパルス | グローエフェクト |
| `blur-soft` | ソフトぼかし | 軽いぼかし |
| `text-shadow` | テキストシャドウ | ドロップシャドウ |
| `border-white` | 白縁取り | 白い縁取り |

---

## JSON変換

### export-json - JSON出力

```bash
aviutl2 export-json <file.aup2> <output.json> [-i INDENT]
```

### import-json - JSONから変換

```bash
aviutl2 import-json <input.json> <output.aup2>
```

---

## レイヤー自動選択

`--layer auto` (または `-l auto`) を指定すると、指定した時間範囲で空いているレイヤーを自動的に選択します。

### 動作例

```bash
$ aviutl2 add text project.aup2 "Text1" --from 0 --to 100
テキスト追加: ID 0, レイヤー 0 (auto), フレーム 0-100

$ aviutl2 add text project.aup2 "Text2" --from 50 --to 150
テキスト追加: ID 1, レイヤー 1 (auto), フレーム 50-150
# ↑ レイヤー0はフレーム0-100で使用中なので、レイヤー1が自動選択

$ aviutl2 add text project.aup2 "Text3" --from 200 --to 300
テキスト追加: ID 2, レイヤー 0 (auto), フレーム 200-300
# ↑ レイヤー0はフレーム200-300で空いているので、レイヤー0が選択
```

---

## 重複警告システム

同種のオブジェクトが時間的に重複する場合に警告を表示します。

### デフォルト設定

| オブジェクトタイプ | `--warn-overlap` デフォルト |
|-------------------|---------------------------|
| 音声ファイル | **ON** |
| 動画ファイル | OFF |
| 画像ファイル | OFF |
| テキスト | OFF |
| 図形 | OFF |

### 動作例

```bash
$ aviutl2 add audio project.aup2 "bgm.mp3" --from 0 --to 300
音声追加: ID 0, レイヤー 0 (auto), フレーム 0-300

$ aviutl2 add audio project.aup2 "se.wav" --from 100 --to 150
警告: レイヤー 0 に同種オブジェクト (ID 0: 音声ファイル) がフレーム 0-300 で重複
音声追加: ID 1, レイヤー 1 (auto), フレーム 100-150
```

---

## AIエージェント向け使用例

### 字幕付き動画の作成

```bash
# 新規プロジェクト作成
aviutl2 new subtitle_video.aup2 -w 1920 -h 1080 -f 30

# 動画を追加
aviutl2 add video subtitle_video.aup2 "main_video.mp4" --from 0 --to 900

# 字幕を順番に追加（自動レイヤー選択）
aviutl2 add text subtitle_video.aup2 "オープニング" --from 0 --to 90 --y 400
aviutl2 add text subtitle_video.aup2 "本編スタート" --from 90 --to 180 --y 400
aviutl2 add text subtitle_video.aup2 "ありがとうございました" --from 810 --to 900 --y 400

# フェードインを適用
aviutl2 preset apply subtitle_video.aup2 1 fade-in
aviutl2 preset apply subtitle_video.aup2 2 fade-in
aviutl2 preset apply subtitle_video.aup2 3 fade-in

# BGMを追加
aviutl2 add audio subtitle_video.aup2 "bgm.mp3" --from 0 --to 900

# タイムラインを確認
aviutl2 timeline subtitle_video.aup2 --to 900
```

### プリセットを活用した演出

```bash
# 新規プロジェクト作成
aviutl2 new demo.aup2

# タイトルテキストを追加
aviutl2 add text demo.aup2 "TITLE" --from 0 --to 90 --size 72

# 回転しながらフェードアウト
aviutl2 preset apply demo.aup2 0 spin-fade-out

# グロー効果を追加
aviutl2 preset apply demo.aup2 0 glow-pulse

# 影を追加
aviutl2 filter add demo.aup2 0 shadow --strength 60

# オブジェクトの詳細を確認
aviutl2 objects demo.aup2 -v
```

### 既存プロジェクトの編集

```bash
# プロジェクト情報を確認
aviutl2 info existing_project.aup2

# 特定区間のオブジェクトを確認
aviutl2 range existing_project.aup2 --from 100 --to 200

# プロパティを変更
aviutl2 modify existing_project.aup2 0 --text "変更後のテキスト" --color ff0000

# アニメーションを追加
aviutl2 animate existing_project.aup2 0 opacity --start 0 --end 100 --motion smooth

# オブジェクトを複製
aviutl2 copy existing_project.aup2 0 --offset 100

# オブジェクトを移動
aviutl2 move existing_project.aup2 5 --from 150 --to 250

# 不要なオブジェクトを削除
aviutl2 delete existing_project.aup2 3 -y
```

---

## トラブルシューティング

### 「配置不可: レイヤーN フレームX-Yには既にオブジェクトが存在」

同じレイヤー・同じ時間帯に複数のオブジェクトは配置できません。

**解決策:**
- `--layer auto` を使用して自動的に空きレイヤーを選択
- `aviutl2 check` で事前に配置可否を確認
- 別のレイヤーを指定

### AviUtlでファイルが開けない

- ファイルがUTF-8（BOMなし）で保存されているか確認
- 改行コードがCRLF（Windows形式）であるか確認
- プロジェクトのバージョン番号が正しいか確認（2001901）

### プリセットが見つからない

- `aviutl2 preset init` でサンプルプリセットを初期化
- `aviutl2 preset list` でプリセット一覧を確認
- プリセットファイル `~/.aviutl2/presets.json` を確認

---

## 関連ドキュメント

- [フォーマット仕様書](aup2_format_specification.md) - .aup2ファイル形式の詳細
- [CLAUDE.md](../CLAUDE.md) - プロジェクト概要
