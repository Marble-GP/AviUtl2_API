# AviUtl2 プロジェクトファイル (.aup2) フォーマット実装ノート

**文書バージョン**: 1.1

**最終更新**: 2026-08-02

---

この文書は公式SDK仕様書ではなく、`aviutl2-api`のparser/serializerと実機
Open/Save結果に基づく実装ノートである。AviUtl2や第三者pluginが追加する未知section・
itemを網羅するものではない。

- 新規生成の既定project versionは`2001901`。
- AviUtl2がOpen/Save後に`2010200`へ更新する場合がある。
- 高水準Effect生成の正本は`src/aviutl2_api/effect_profiles.py`のversion付きmanifest。
- raw Effect名・item・enumをこの文書だけから推測して生成しない。
- byte一致ではなく、parse/serializeとOpen/Save後の意味保持を互換性基準とする。

通常の編集では`.aup2`内部を直接組み立てず、公開Python model、高水準Effect API、
またはLive Bridgeを使用する。

## 1. ファイル形式

### 1.1 基本仕様

| 項目 | 仕様 |
|------|------|
| エンコーディング | UTF-8（BOMなし） |
| 改行コード | CRLF (`\r\n`) |
| 形式 | INI風テキストフォーマット |
| 構文 | `[セクション名]` + `キー=値` |

### 1.2 座標系

| 軸 | 方向 |
|----|------|
| X軸 | 右方向が正 |
| Y軸 | **上方向が正**（スクリーン座標とは逆） |
| Z軸 | **画面奥方向が正** |

### 1.3 レイヤーと奥行き

**レイヤー番号と描画順序**:
- レイヤー番号が大きいほど**上（手前）**に表示される
- Layer 0 → Layer 1 → Layer 2 の順で描画され、後から描画されたものが上に重なる

**典型的な配置**:
```
Layer 0: 背景、編集対象の動画
Layer 1: 画像、テキスト、アノテーション
Layer 2: さらに上に載せるコンテンツ
...
```

**カメラ制御との関係**:
- カメラ制御なし: Z座標の値は描画順序に影響しない。レイヤー番号のみで前後関係が決まる
- カメラ制御あり: カメラ制御オブジェクトより下のレイヤー（番号が大きいレイヤー）では、Z座標による3D的な奥行きが考慮される

### 1.4 全体構造

```
[project]           # プロジェクトメタデータ（1個）
[scene.N]           # シーン設定（N=0,1,2...）
[ObjectID]          # タイムラインオブジェクト（0,1,2...）
[ObjectID.EffectID] # エフェクト（0.0, 0.1, 1.0, 1.1, ...）
```

---

## 2. データ型

### 2.1 プロパティ値の型

プロパティ値には3種類の型がある：

| 型 | 説明 | 例 |
|----|------|-----|
| **StaticValue** | 静的な数値。小数点以下2桁で出力 | `100.00`, `0.00` |
| **AnimatedValue** | アニメーション値。開始値、終了値、移動タイプ、パラメータで構成 | `-100.00,100.00,直線移動,0` |
| **文字列** | 文字列値。そのまま出力 | `Yu Gothic UI`, `通常` |

### 2.2 StaticValue

単一の数値。出力時は小数点以下2桁に整形される。

```ini
拡大率=100.00
透明度=0.00
X=50.00
```

### 2.3 AnimatedValue

時間経過で値が変化するアニメーション値。

**フォーマット**: `<開始値>,<終了値>,<移動タイプ>,<パラメータ>`

```ini
X=-100.00,100.00,直線移動,0
拡大率=100.00,10.00,補間移動,0
透明度=0.00,100.00,反復移動,4|5
```

### 2.4 色値（重要）

**色は16進数文字列として格納される。数値ではない。**

```ini
色=ff0000          # 赤
文字色=ffffff      # 白
影色=000000        # 黒
```

**パーサー実装上の注意**: `"000000"` のような文字列が `float(0.0)` に変換されないよう、6桁の16進数文字列は必ず文字列として保持する。

**判定方法**: 値が6文字かつ全て `[0-9a-fA-F]` の場合は色値として扱う。

---

## 3. セクション詳細

### 3.1 [project] セクション

プロジェクト全体のメタデータ。ファイル先頭に1つだけ存在。

| キー | 型 | 説明 |
|------|-----|------|
| `version` | int | AviUtl2バージョン番号（例: `2001901`） |
| `ファイル` / `file` | string | project path。library生成とhost保存で表記が異なるためparserは両方を受理 |
| `display.scene` | int | 現在表示中のシーン番号 |

### 3.2 [scene.N] セクション

シーンごとの設定。N はシーン番号（0始まり）。

| キー | 型 | 説明 | 例 |
|------|-----|------|-----|
| `scene` | int | シーン番号 | `0` |
| `name` | string | シーン名 | `Root` |
| `video.width` | int | 横解像度（ピクセル） | `1920` |
| `video.height` | int | 縦解像度（ピクセル） | `1080` |
| `video.rate` | int | フレームレート（FPS） | `30` |
| `video.scale` | int | ビデオスケール | `1` |
| `audio.rate` | int | 音声サンプルレート（Hz） | `44100` |
| `cursor.frame` | int | カーソルのフレーム位置 | `0` |
| `cursor.layer` | int | カーソルのレイヤー位置 | `0` |
| `display.frame` | int | 表示開始フレーム | `0` |
| `display.layer` | int | 表示開始レイヤー | `0` |
| `display.zoom` | int | ズーム倍率（10000=100%） | `10000` |
| `display.order` | int | 表示順序 | `0` |
| `display.camera` | string | カメラ設定 | `` |
| `display.grid.x` | string | X軸グリッド設定 | `16,-16` |
| `display.grid.y` | string | Y軸グリッド設定 | `16,-16` |
| `display.grid.width` | int | グリッド幅 | `200` |
| `display.grid.height` | int | グリッド高さ | `200` |
| `display.grid.step` | float | グリッドステップ | `200.000000` |
| `display.grid.range` | float | グリッド範囲 | `10000.000000` |
| `display.tempo.bpm` | float | テンポ（BPM） | `120.000000` |
| `display.tempo.beat` | int | 拍子 | `4` |
| `display.tempo.offset` | float | テンポオフセット | `0.000000` |

### 3.3 [ObjectID] セクション

タイムライン上のオブジェクト。

| キー | 型 | 説明 |
|------|-----|------|
| `layer` | int | レイヤー番号（0始まり、大きいほど上に表示） |
| `frame` | string | フレーム範囲（`開始,終了` 形式） |
| `focus` | int | 選択状態（1=選択中、省略可） |

**重要なルール**:
- 同一layer・同一時刻のoverlapはtransition等でhostが受理する場合がある。libraryの
  collision検査結果だけをproject構文errorとは扱わない
- フレーム計算: `秒数 = (終了フレーム - 開始フレーム + 1) / video.rate`

### 3.4 [ObjectID.EffectID] セクション

オブジェクトに付属するエフェクト。

- 通常は先頭に入力Effect（動画ファイル、テキスト、図形など）が置かれる。
- 描画・再生Effectと追加Effectの正規順はobject domainとhost versionに依存する。
- Effect IDはOpen/Save時に再採番され得るため、IDだけで意味を判定しない。

---

## 4. Effectとanimationの互換境界

Effect sectionは`effect.name=<native name>`と、そのEffectが所有するitemで構成される。
入力、描画・再生、追加Effectの順序と完全なitem集合はAviUtl2 versionに依存する。
この文書では個別Effectのraw item一覧を固定仕様として掲載しない。

標準Effectを生成・検証するときは次を正本とする:

- `src/aviutl2_api/effect_profiles.py`: 主要20 profileのnative名、順序、型、既定値、enum、単位
- `src/aviutl2_api/aup2_effects.py`: 安全な挿入、domain/order検証、Open/Save意味比較
- `aviutl2_api.editing.effect()`: backend共通のsemantic入力
- `aviutl2_api.apply_effects()`: modelへの高水準適用

第三者Effectと未知itemはparser/serializerで可能な限り保持するが、意味・型・単位は
`unverified`であり、高水準APIが推測して補完しない。AviUtl2による既定item追加、
float表記、ID再採番、property順変更は、明示値とEffectの意味が保たれる場合だけ
Open/Save normalizationとして扱う。

`StaticValue`と`AnimatedValue`の具体的なserialize/parse規則は
`src/aviutl2_api/models/values.py`を正本とする。motion名やparameterを新規生成する場合も、
実機または検証済みtemplateに存在しない値をこの文書から推測しない。

## 5. エスケープ規則

### 5.1 テキスト内のエスケープシーケンス

| シーケンス | 意味 |
|-----------|------|
| `\n` | 改行 |
| `\\` | バックスラッシュ（リテラル） |

### 5.2 エスケープ不要な文字

以下の文字はテキスト値内でそのまま使用可能:
- `[` `]` - 角括弧
- `=` - 等号
- `/` - スラッシュ
- `!"#$%&'()@`+;:*{}<>?_` - その他の記号

### 5.3 パーサー実装上の注意

- `=` は最初の出現で key/value を分割（value内の`=`はそのまま）
- `[` `]` は行頭でセクションヘッダーとして解釈、それ以外は通常文字
- 複数行テキストは `\n` で表現（実際の改行ではない）

---

## 6. 最小プロジェクト構造

```ini
[project]
version=2001901
ファイル=C:\path\to\project.aup2
display.scene=0
[scene.0]
scene=0
name=Root
video.width=1920
video.height=1080
video.rate=30
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
```

---

## 7. 実装上の注意点

### 7.1 色値の取り扱い

色は必ず**16進数文字列**として扱う。パーサーで `"000000"` が `float(0.0)` に変換されないよう注意。

**判定ロジック**:
```python
if len(value_str) == 6 and all(c in "0123456789abcdefABCDEF" for c in value_str):
    return value_str  # 文字列として返す
```

### 7.2 浮動小数点数の出力

`StaticValue`と`AnimatedValue`はmodelのformat規則、scene metadataはserializerの
format規則を使う。AviUtl2 Open/Saveは`200`、`200.00`、`200.000000`のように表記を
正規化し得るため、比較時は数値として同値かを判定する。

### 7.3 プロパティ名

AviUtl2は日本語のプロパティ名を使用する。内部名（type, color, blend等）ではなく、日本語名（図形の種類, 色, 合成モード等）を使用すること。

### 7.4 改行コード

出力時は必ずCRLF (`\r\n`) を使用すること。

---

## 8. 変更履歴

| バージョン | 日付 | 内容 |
|-----------|------|------|
| 0.1 | 2025-12-25 | 初版作成 |
| 0.2 | 2025-12-25 | アニメーション構文、エスケープ規則を追加 |
| 0.3 | 2025-12-26 | フィルタ一覧、移動タイプ一覧を追加 |
| 1.0 | 2025-12-27 | データ型仕様を詳細化、座標系・回転モード・実装注意点を追加 |
| 1.1 | 2026-08-02 | 実装ノートとしての非公式境界、対応project version、Effect manifest正本を明記 |
