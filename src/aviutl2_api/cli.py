"""Command-line interface for AviUtl2 Project API.

Stateless CLI for AI agent automation. Each command reads/writes files directly.
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from typing import Any

import click

from aviutl2_api import (
    Project,
    Scene,
    TimelineObject,
    from_json,
    parse_file,
    serialize_to_file,
    to_json,
)
from aviutl2_api.models import Effect
from aviutl2_api.models.values import AnimatedValue, StaticValue


def safe_echo(message: str, err: bool = False) -> None:
    """Platform-aware echo that handles console encoding properly.

    Windows environments often use CP932 (Shift_JIS) encoding in console,
    while Unix-like systems use UTF-8. This function detects the platform
    and uses appropriate encoding to prevent mojibake (文字化け).

    Args:
        message: Message to print
        err: If True, write to stderr instead of stdout
    """
    if platform.system() == "Windows":
        # Windows: Use console's native encoding (typically cp932)
        try:
            encoding = sys.stdout.encoding if not err else sys.stderr.encoding
            if encoding is None:
                encoding = 'cp932'  # Fallback to cp932

            stream = sys.stderr if err else sys.stdout
            stream.buffer.write((message + '\n').encode(encoding, errors='replace'))
            stream.buffer.flush()
        except Exception:
            # Fallback to click.echo if something goes wrong
            click.echo(message, err=err)
    else:
        # Unix/Linux/Mac: UTF-8 (click handles this well)
        click.echo(message, err=err)


@click.group()
@click.version_option(version="0.1.1", prog_name="aviutl2")
def main() -> None:
    """AviUtl2 Project API - .aup2ファイル操作ツール

    AIエージェントによる動画編集自動化のためのステートレスCLIツールです。
    各コマンドはファイルを直接読み書きします。
    """
    pass


# =============================================================================
# プロジェクト操作コマンド
# =============================================================================


@main.command("new")
@click.argument("output_file", type=click.Path(path_type=Path))
@click.option("--width", "-w", default=1920, help="映像幅（デフォルト: 1920）")
@click.option("--height", "-h", default=1080, help="映像高さ（デフォルト: 1080）")
@click.option("--fps", "-f", default=30, help="フレームレート（デフォルト: 30）")
@click.option("--audio-rate", "-a", default=44100, help="音声サンプルレート（デフォルト: 44100）")
def new_project(output_file: Path, width: int, height: int, fps: int, audio_rate: int) -> None:
    """新規プロジェクトを作成して保存する。"""
    # Create empty scene
    scene = Scene(
        scene_id=0,
        name="Root",
        width=width,
        height=height,
        fps=fps,
        audio_rate=audio_rate,
        objects=[],
    )

    # Create project
    project = Project(
        version=2001901,
        file_path="",
        display_scene=0,
        scenes=[scene],
    )

    # Save
    serialize_to_file(project, output_file)

    safe_echo(f"新規プロジェクト作成: {width}x{height} @ {fps}fps")
    safe_echo(f"  保存先: {output_file}")


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
def info(file: Path) -> None:
    """プロジェクト情報を表示する。"""
    project = parse_file(file)
    _print_project_info(project, file, modified=False)


# =============================================================================
# タイムライン表示コマンド
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--from", "from_frame", type=int, default=0, help="開始フレーム")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム")
@click.option("--layer", "-l", type=str, default=None, help="レイヤー指定（例: 0, 0-5, 0,2,4）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--width", "-w", type=int, default=80, help="表示幅（文字数）")
@click.option("--compact", "-c", is_flag=True, help="空き領域を省略してコンパクト表示")
def timeline(file: Path, from_frame: int, to_frame: int | None, layer: str | None, scene: int, width: int, compact: bool) -> None:
    """タイムラインをASCII表示する。

    \b
    例:
      aviutl2 timeline project.aup2                    # 全体表示
      aviutl2 timeline project.aup2 --from 0 --to 100  # 範囲指定
      aviutl2 timeline project.aup2 -l 0-5             # レイヤー0〜5のみ
      aviutl2 timeline project.aup2 -l 0,2,4           # レイヤー0,2,4のみ
      aviutl2 timeline project.aup2 --compact          # コンパクト表示
    """
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]
    max_frame = to_frame or sc.max_frame or 100

    # Parse layer filter
    layer_filter = _parse_layer_filter(layer)

    _print_timeline(sc, from_frame, max_frame, width, layer_filter, compact)


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
def layers(file: Path, scene: int) -> None:
    """レイヤー一覧を表示する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]
    _print_layers(sc)


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--layer", "-l", type=int, default=None, help="レイヤー番号でフィルタ")
@click.option("--at", "at_frame", type=int, default=None, help="指定フレームに存在するオブジェクト")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--verbose", "-v", is_flag=True, help="詳細表示")
def objects(file: Path, layer: int | None, at_frame: int | None, scene: int, verbose: bool) -> None:
    """オブジェクト一覧を表示する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]
    objs = sc.objects

    # Filter by layer
    if layer is not None:
        objs = [o for o in objs if o.layer == layer]

    # Filter by frame
    if at_frame is not None:
        objs = [o for o in objs if o.frame_start <= at_frame <= o.frame_end]

    _print_objects(objs, verbose)


# =============================================================================
# 検索・チェックコマンド
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--at", "at_frame", type=int, required=True, help="検索するフレーム")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
def search(file: Path, at_frame: int, scene: int) -> None:
    """指定フレームに存在するオブジェクトを検索する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]
    objs = sc.get_objects_at_frame(at_frame)

    safe_echo(f"フレーム {at_frame} のオブジェクト ({len(objs)}件):")
    _print_objects(objs, verbose=False)


@main.command("range")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--from", "from_frame", type=int, required=True, help="開始フレーム")
@click.option("--to", "to_frame", type=int, required=True, help="終了フレーム")
@click.option("--type", "obj_type", type=str, default=None, help="オブジェクトタイプでフィルタ（テキスト,図形,音声ファイル,動画ファイル,画像ファイル）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--verbose", "-v", is_flag=True, help="詳細表示")
def range_search(file: Path, from_frame: int, to_frame: int, obj_type: str | None, scene: int, verbose: bool) -> None:
    """指定区間に存在するオブジェクトを列挙する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find objects that overlap with the range
    objs = []
    for obj in sc.objects:
        if obj.frame_start <= to_frame and obj.frame_end >= from_frame:
            if obj_type is None or obj.object_type == obj_type:
                objs.append(obj)

    # Sort by start frame
    objs.sort(key=lambda o: (o.frame_start, o.layer))

    safe_echo(f"区間 {from_frame}-{to_frame} のオブジェクト ({len(objs)}件):")
    _print_objects(objs, verbose=verbose)


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--at", "at_frame", type=int, required=True, help="開始フレーム")
@click.option("--to", "to_frame", type=int, required=True, help="終了フレーム")
@click.option("--layer", "-l", type=int, required=True, help="レイヤー番号")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
def check(file: Path, at_frame: int, to_frame: int, layer: int, scene: int) -> None:
    """指定範囲にオブジェクトを配置可能か確認する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Check for collisions
    conflicts = []
    for obj in sc.objects:
        if obj.layer != layer:
            continue
        # Check overlap
        if obj.frame_start <= to_frame and obj.frame_end >= at_frame:
            conflicts.append(obj)

    if conflicts:
        safe_echo(f"配置不可: レイヤー {layer} フレーム {at_frame}-{to_frame} には衝突があります:")
        for obj in conflicts:
            safe_echo(f"  - ID {obj.object_id}: {obj.object_type} (フレーム {obj.frame_start}-{obj.frame_end})")
    else:
        safe_echo(f"配置可能: レイヤー {layer} フレーム {at_frame}-{to_frame}")


# =============================================================================
# 編集コマンド
# =============================================================================


@main.group()
def add() -> None:
    """オブジェクトを追加する。"""
    pass


@add.command("text")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("content")
@click.option("--layer", "-l", type=str, default="auto", help="レイヤー番号 (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="開始フレーム (省略時は末尾またはフレーム0)")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム (省略時は--durationまたは60フレーム)")
@click.option("--duration", "-d", type=int, default=None, help="期間（フレーム数）")
@click.option("--x", type=float, default=0.0, help="X座標")
@click.option("--y", type=float, default=0.0, help="Y座標")
@click.option("--size", type=int, default=34, help="フォントサイズ")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
@click.option("--warn-overlap/--no-warn-overlap", default=False, help="同種オブジェクト重複時の警告（デフォルト: OFF）")
def add_text(
    file: Path,
    content: str,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    x: float,
    y: float,
    size: int,
    scene: int,
    output: Path | None,
    warn_overlap: bool,
) -> None:
    """テキストオブジェクトを追加する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Calculate frame range with auto-detection
    from_frame, to_frame = _calculate_frame_range(sc, from_frame, to_frame, duration)

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, from_frame, to_frame)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= to_frame and obj.frame_end >= from_frame:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {from_frame}-{to_frame} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Check for overlap warnings
    if warn_overlap:
        warnings = _check_overlap_warnings(sc, from_frame, to_frame, "テキスト", {"テキスト"})
        for w in warnings:
            safe_echo(w, err=True)

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Create text effect - using AviUtl2 property names
    text_effect = Effect(
        effect_id=0,
        name="テキスト",
        properties={
            "サイズ": StaticValue(value=float(size)),
            "字間": StaticValue(value=0.0),
            "行間": StaticValue(value=0.0),
            "表示速度": StaticValue(value=0.0),
            "フォント": "Yu Gothic UI",
            "文字色": "ffffff",
            "影・縁色": "000000",
            "文字装飾": "標準文字",
            "文字揃え": "左寄せ[上]",
            "B": StaticValue(value=0),
            "I": StaticValue(value=0),
            "テキスト": content,
            "文字毎に個別オブジェクト": StaticValue(value=0),
            "自動スクロール": StaticValue(value=0),
            "移動座標上に表示": StaticValue(value=0),
            "オブジェクトの長さを自動調節": StaticValue(value=0),
        },
    )

    # Create standard draw effect
    draw_effect = Effect(
        effect_id=1,
        name="標準描画",
        properties={
            "X": StaticValue(value=x),
            "Y": StaticValue(value=y),
            "Z": StaticValue(value=0.0),
            "Group": StaticValue(value=1.0),
            "中心X": StaticValue(value=0.0),
            "中心Y": StaticValue(value=0.0),
            "中心Z": StaticValue(value=0.0),
            "X軸回転": StaticValue(value=0.0),
            "Y軸回転": StaticValue(value=0.0),
            "Z軸回転": StaticValue(value=0.0),
            "拡大率": StaticValue(value=100.0),
            "縦横比": StaticValue(value=0.0),
            "透明度": StaticValue(value=0.0),
            "合成モード": "通常",
        },
    )

    # Create timeline object
    obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=from_frame,
        frame_end=to_frame,
        effects=[text_effect, draw_effect],
    )

    sc.objects.append(obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"テキスト追加: ID {new_id}, {layer_info}, フレーム {from_frame}-{to_frame}")
    safe_echo(f"  内容: {content}")
    safe_echo(f"  保存先: {save_path}")


@add.command("shape")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("shape_type", type=click.Choice(["circle", "rectangle", "triangle", "pentagon", "hexagon"]))
@click.option("--layer", "-l", type=str, default="auto", help="レイヤー番号 (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="開始フレーム (省略時は末尾またはフレーム0)")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム (省略時は--durationまたは60フレーム)")
@click.option("--duration", "-d", type=int, default=None, help="期間（フレーム数）")
@click.option("--x", type=float, default=0.0, help="X座標")
@click.option("--y", type=float, default=0.0, help="Y座標")
@click.option("--size", type=int, default=100, help="サイズ")
@click.option("--color", type=str, default="ffffff", help="色（16進数）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
@click.option("--warn-overlap/--no-warn-overlap", default=False, help="同種オブジェクト重複時の警告（デフォルト: OFF）")
def add_shape(
    file: Path,
    shape_type: str,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    x: float,
    y: float,
    size: int,
    color: str,
    scene: int,
    output: Path | None,
    warn_overlap: bool,
) -> None:
    """図形オブジェクトを追加する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Calculate frame range with auto-detection
    from_frame, to_frame = _calculate_frame_range(sc, from_frame, to_frame, duration)

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, from_frame, to_frame)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= to_frame and obj.frame_end >= from_frame:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {from_frame}-{to_frame} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Check for overlap warnings
    if warn_overlap:
        warnings = _check_overlap_warnings(sc, from_frame, to_frame, "図形", {"図形"})
        for w in warnings:
            safe_echo(w, err=True)

    # Map shape type to Japanese name and type value
    shape_map = {
        "circle": ("円", 0),
        "rectangle": ("四角形", 1),
        "triangle": ("三角形", 2),
        "pentagon": ("五角形", 3),
        "hexagon": ("六角形", 4),
    }
    shape_name, shape_type_value = shape_map[shape_type]

    # Parse color
    try:
        color_value = int(color, 16)
    except ValueError:
        raise click.ClickException(f"無効な色指定: {color}")

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Create shape effect
    shape_effect = Effect(
        effect_id=0,
        name="図形",
        properties={
            "図形の種類": shape_name,
            "サイズ": StaticValue(value=float(size)),
            "縦横比": StaticValue(value=0.0),
            "ライン幅": StaticValue(value=4000.0),
            "色": color,  # 16進数文字列
            "角を丸くする": StaticValue(value=0.0),
        },
    )

    # Create standard draw effect
    draw_effect = Effect(
        effect_id=1,
        name="標準描画",
        properties={
            "X": StaticValue(value=x),
            "Y": StaticValue(value=y),
            "Z": StaticValue(value=0.0),
            "Group": StaticValue(value=1.0),
            "中心X": StaticValue(value=0.0),
            "中心Y": StaticValue(value=0.0),
            "中心Z": StaticValue(value=0.0),
            "X軸回転": StaticValue(value=0.0),
            "Y軸回転": StaticValue(value=0.0),
            "Z軸回転": StaticValue(value=0.0),
            "拡大率": StaticValue(value=100.0),
            "縦横比": StaticValue(value=0.0),
            "透明度": StaticValue(value=0.0),
            "合成モード": "通常",
        },
    )

    # Create timeline object
    obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=from_frame,
        frame_end=to_frame,
        effects=[shape_effect, draw_effect],
    )

    sc.objects.append(obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"図形追加: ID {new_id}, {shape_name}, {layer_info}, フレーム {from_frame}-{to_frame}")
    safe_echo(f"  保存先: {save_path}")


@add.command("audio")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("audio_path", type=str)
@click.option("--layer", "-l", type=str, default="auto", help="レイヤー番号 (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="開始フレーム (省略時は末尾またはフレーム0)")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム (省略時はファイルの長さを自動検出)")
@click.option("--duration", "-d", type=int, default=None, help="期間（フレーム数）")
@click.option("--volume", type=float, default=100.0, help="音量（デフォルト: 100）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
@click.option("--warn-overlap/--no-warn-overlap", default=True, help="同種オブジェクト重複時の警告（デフォルト: ON）")
def add_audio(
    file: Path,
    audio_path: str,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    volume: float,
    scene: int,
    output: Path | None,
    warn_overlap: bool,
) -> None:
    """音声ファイルオブジェクトを追加する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Calculate frame range with auto-detection from media file
    from_frame, to_frame = _calculate_frame_range(sc, from_frame, to_frame, duration, media_path=audio_path)

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, from_frame, to_frame)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= to_frame and obj.frame_end >= from_frame:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {from_frame}-{to_frame} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Check for overlap warnings (default ON for audio)
    if warn_overlap:
        warnings = _check_overlap_warnings(sc, from_frame, to_frame, "音声ファイル", {"音声ファイル"})
        for w in warnings:
            safe_echo(w, err=True)

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Create audio effect
    audio_effect = Effect(
        effect_id=0,
        name="音声ファイル",
        properties={
            "再生位置": StaticValue(value=0.0),
            "再生速度": StaticValue(value=100.0),
            "ループ再生": StaticValue(value=0.0),
            "動画ファイルと連携": StaticValue(value=0.0),
            "ファイル": audio_path,
        },
    )

    # Create standard playback effect
    playback_effect = Effect(
        effect_id=1,
        name="音声再生",
        properties={
            "音量": StaticValue(value=volume),
            "左右": StaticValue(value=0.0),
        },
    )

    # Create timeline object
    obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=from_frame,
        frame_end=to_frame,
        effects=[audio_effect, playback_effect],
    )

    sc.objects.append(obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"音声追加: ID {new_id}, {layer_info}, フレーム {from_frame}-{to_frame}")
    safe_echo(f"  ファイル: {audio_path}")
    safe_echo(f"  保存先: {save_path}")


@add.command("video")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("video_path", type=str)
@click.option("--layer", "-l", type=str, default="auto", help="レイヤー番号 (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="開始フレーム (省略時は末尾またはフレーム0)")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム (省略時はファイルの長さを自動検出)")
@click.option("--duration", "-d", type=int, default=None, help="期間（フレーム数）")
@click.option("--x", type=float, default=0.0, help="X座標")
@click.option("--y", type=float, default=0.0, help="Y座標")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
@click.option("--warn-overlap/--no-warn-overlap", default=False, help="同種オブジェクト重複時の警告（デフォルト: OFF）")
def add_video(
    file: Path,
    video_path: str,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    x: float,
    y: float,
    scene: int,
    output: Path | None,
    warn_overlap: bool,
) -> None:
    """動画ファイルオブジェクトを追加する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Calculate frame range with auto-detection from media file
    from_frame, to_frame = _calculate_frame_range(sc, from_frame, to_frame, duration, media_path=video_path)

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, from_frame, to_frame)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= to_frame and obj.frame_end >= from_frame:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {from_frame}-{to_frame} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Check for overlap warnings
    if warn_overlap:
        warnings = _check_overlap_warnings(sc, from_frame, to_frame, "動画ファイル", {"動画ファイル"})
        for w in warnings:
            safe_echo(w, err=True)

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Create video effect
    video_effect = Effect(
        effect_id=0,
        name="動画ファイル",
        properties={
            "再生位置": StaticValue(value=0.0),
            "再生速度": StaticValue(value=100.0),
            "ループ再生": StaticValue(value=0.0),
            "アルファチャンネルを読み込む": StaticValue(value=0.0),
            "ファイル": video_path,
        },
    )

    # Create standard draw effect
    draw_effect = Effect(
        effect_id=1,
        name="標準描画",
        properties={
            "X": StaticValue(value=x),
            "Y": StaticValue(value=y),
            "Z": StaticValue(value=0.0),
            "Group": StaticValue(value=1.0),
            "中心X": StaticValue(value=0.0),
            "中心Y": StaticValue(value=0.0),
            "中心Z": StaticValue(value=0.0),
            "X軸回転": StaticValue(value=0.0),
            "Y軸回転": StaticValue(value=0.0),
            "Z軸回転": StaticValue(value=0.0),
            "拡大率": StaticValue(value=100.0),
            "縦横比": StaticValue(value=0.0),
            "透明度": StaticValue(value=0.0),
            "合成モード": "通常",
        },
    )

    # Create timeline object
    obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=from_frame,
        frame_end=to_frame,
        effects=[video_effect, draw_effect],
    )

    sc.objects.append(obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"動画追加: ID {new_id}, {layer_info}, フレーム {from_frame}-{to_frame}")
    safe_echo(f"  ファイル: {video_path}")
    safe_echo(f"  保存先: {save_path}")


@add.command("image")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("image_path", type=str)
@click.option("--layer", "-l", type=str, default="auto", help="レイヤー番号 (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="開始フレーム (省略時は末尾またはフレーム0)")
@click.option("--to", "to_frame", type=int, default=None, help="終了フレーム (省略時は--durationまたは60フレーム)")
@click.option("--duration", "-d", type=int, default=None, help="期間（フレーム数）")
@click.option("--x", type=float, default=0.0, help="X座標")
@click.option("--y", type=float, default=0.0, help="Y座標")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
@click.option("--warn-overlap/--no-warn-overlap", default=False, help="同種オブジェクト重複時の警告（デフォルト: OFF）")
def add_image(
    file: Path,
    image_path: str,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    x: float,
    y: float,
    scene: int,
    output: Path | None,
    warn_overlap: bool,
) -> None:
    """画像ファイルオブジェクトを追加する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Calculate frame range with auto-detection
    from_frame, to_frame = _calculate_frame_range(sc, from_frame, to_frame, duration)

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, from_frame, to_frame)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= to_frame and obj.frame_end >= from_frame:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {from_frame}-{to_frame} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Check for overlap warnings
    if warn_overlap:
        warnings = _check_overlap_warnings(sc, from_frame, to_frame, "画像ファイル", {"画像ファイル"})
        for w in warnings:
            safe_echo(w, err=True)

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Create image effect
    image_effect = Effect(
        effect_id=0,
        name="画像ファイル",
        properties={
            "ファイル": image_path,
        },
    )

    # Create standard draw effect
    draw_effect = Effect(
        effect_id=1,
        name="標準描画",
        properties={
            "X": StaticValue(value=x),
            "Y": StaticValue(value=y),
            "Z": StaticValue(value=0.0),
            "Group": StaticValue(value=1.0),
            "中心X": StaticValue(value=0.0),
            "中心Y": StaticValue(value=0.0),
            "中心Z": StaticValue(value=0.0),
            "X軸回転": StaticValue(value=0.0),
            "Y軸回転": StaticValue(value=0.0),
            "Z軸回転": StaticValue(value=0.0),
            "拡大率": StaticValue(value=100.0),
            "縦横比": StaticValue(value=0.0),
            "透明度": StaticValue(value=0.0),
            "合成モード": "通常",
        },
    )

    # Create timeline object
    obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=from_frame,
        frame_end=to_frame,
        effects=[image_effect, draw_effect],
    )

    sc.objects.append(obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"画像追加: ID {new_id}, {layer_info}, フレーム {from_frame}-{to_frame}")
    safe_echo(f"  ファイル: {image_path}")
    safe_echo(f"  保存先: {save_path}")


@main.command("move")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.option("--layer", "-l", type=int, default=None, help="移動先レイヤー")
@click.option("--from", "from_frame", type=int, default=None, help="新しい開始フレーム")
@click.option("--to", "to_frame", type=int, default=None, help="新しい終了フレーム")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def move_object(
    file: Path,
    object_id: int,
    layer: int | None,
    from_frame: int | None,
    to_frame: int | None,
    scene: int,
    output: Path | None,
) -> None:
    """オブジェクトを移動する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    # Calculate new values
    new_layer = layer if layer is not None else obj.layer
    new_start = from_frame if from_frame is not None else obj.frame_start
    new_end = to_frame if to_frame is not None else obj.frame_end

    # Check for collisions (excluding self)
    for other in sc.objects:
        if other.object_id == object_id:
            continue
        if other.layer == new_layer and other.frame_start <= new_end and other.frame_end >= new_start:
            raise click.ClickException(
                f"移動不可: レイヤー {new_layer} フレーム {new_start}-{new_end} には"
                f"既にオブジェクト(ID {other.object_id})が存在します。"
            )

    # Apply changes
    old_layer, old_start, old_end = obj.layer, obj.frame_start, obj.frame_end
    obj.layer = new_layer
    obj.frame_start = new_start
    obj.frame_end = new_end

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"オブジェクト {object_id} を移動:")
    safe_echo(f"  レイヤー: {old_layer} -> {new_layer}")
    safe_echo(f"  フレーム: {old_start}-{old_end} -> {new_start}-{new_end}")
    safe_echo(f"  保存先: {save_path}")


@main.command("delete")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--yes", "-y", is_flag=True, help="確認なしで削除")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def delete_object(file: Path, object_id: int, scene: int, yes: bool, output: Path | None) -> None:
    """オブジェクトを削除する。"""
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj_index = None
    for i, o in enumerate(sc.objects):
        if o.object_id == object_id:
            obj_index = i
            break

    if obj_index is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    obj = sc.objects[obj_index]

    if not yes:
        safe_echo(f"削除対象: ID {obj.object_id}, {obj.object_type}, "
                   f"レイヤー {obj.layer}, フレーム {obj.frame_start}-{obj.frame_end}")
        if not click.confirm("本当に削除しますか？"):
            safe_echo("キャンセルしました。")
            return

    sc.objects.pop(obj_index)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"オブジェクト {object_id} を削除しました。")
    safe_echo(f"  保存先: {save_path}")


@main.command("modify")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.option("--text", type=str, default=None, help="テキスト内容を変更")
@click.option("--x", type=float, default=None, help="X座標を変更")
@click.option("--y", type=float, default=None, help="Y座標を変更")
@click.option("--z", type=float, default=None, help="Z座標を変更")
@click.option("--scale", type=float, default=None, help="拡大率を変更")
@click.option("--opacity", type=float, default=None, help="透明度を変更（0-100）")
@click.option("--rotation", type=float, default=None, help="回転角度を変更")
@click.option("--size", type=float, default=None, help="サイズを変更（テキスト/図形）")
@click.option("--color", type=str, default=None, help="色を変更（16進数）")
@click.option("--font", type=str, default=None, help="フォントを変更（テキスト）")
@click.option("--volume", type=float, default=None, help="音量を変更（音声）")
@click.option("--layer", type=int, default=None, help="レイヤー位置を変更")
@click.option("--from", "frame_from", type=int, default=None, help="開始フレームを変更")
@click.option("--to", "frame_to", type=int, default=None, help="終了フレームを変更")
@click.option("--effect-name", type=str, default=None, help="メインエフェクト名を変更（画像ファイル、動画ファイルなど）")
@click.option("--force", is_flag=True, help="衝突時に既存オブジェクトを下のレイヤーに押し下げる")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def modify_object(
    file: Path,
    object_id: int,
    text: str | None,
    x: float | None,
    y: float | None,
    z: float | None,
    scale: float | None,
    opacity: float | None,
    rotation: float | None,
    size: float | None,
    color: str | None,
    font: str | None,
    volume: float | None,
    layer: int | None,
    frame_from: int | None,
    frame_to: int | None,
    effect_name: str | None,
    force: bool,
    scene: int,
    output: Path | None,
) -> None:
    """オブジェクトのプロパティを変更する。

    \b
    例:
      aviutl2 modify project.aup2 0 --text "新しいテキスト"
      aviutl2 modify project.aup2 0 --x 100 --y -50
      aviutl2 modify project.aup2 0 --scale 150 --opacity 50
      aviutl2 modify project.aup2 0 --color ff0000
    """
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    changes: list[str] = []

    # Modify text content
    if text is not None:
        text_effect = obj.get_effect("テキスト")
        if text_effect:
            text_effect.properties["テキスト"] = text
            changes.append(f"テキスト: {text[:20]}{'...' if len(text) > 20 else ''}")
        else:
            raise click.ClickException("このオブジェクトにはテキスト効果がありません。")

    # Modify drawing properties
    draw_effect = obj.get_effect("標準描画")
    if draw_effect:
        if x is not None:
            draw_effect.properties["X"] = StaticValue(value=x)
            changes.append(f"X: {x}")
        if y is not None:
            draw_effect.properties["Y"] = StaticValue(value=y)
            changes.append(f"Y: {y}")
        if z is not None:
            draw_effect.properties["Z"] = StaticValue(value=z)
            changes.append(f"Z: {z}")
        if scale is not None:
            draw_effect.properties["拡大率"] = StaticValue(value=scale)
            changes.append(f"拡大率: {scale}%")
        if opacity is not None:
            draw_effect.properties["透明度"] = StaticValue(value=opacity)
            changes.append(f"透明度: {opacity}%")
        if rotation is not None:
            draw_effect.properties["Z軸回転"] = StaticValue(value=rotation)
            changes.append(f"Z軸回転: {rotation}°")

    # Modify size (for text or shape)
    if size is not None:
        text_effect = obj.get_effect("テキスト")
        shape_effect = obj.get_effect("図形")
        if text_effect:
            text_effect.properties["サイズ"] = StaticValue(value=size)
            changes.append(f"フォントサイズ: {size}")
        elif shape_effect:
            shape_effect.properties["サイズ"] = StaticValue(value=size)
            changes.append(f"図形サイズ: {size}")
        else:
            raise click.ClickException("このオブジェクトにはサイズプロパティがありません。")

    # Modify color
    if color is not None:
        # Validate hex color
        try:
            int(color, 16)
        except ValueError:
            raise click.ClickException(f"無効な色指定: {color}")

        text_effect = obj.get_effect("テキスト")
        shape_effect = obj.get_effect("図形")
        if text_effect:
            text_effect.properties["文字色"] = color
            changes.append(f"文字色: #{color}")
        elif shape_effect:
            shape_effect.properties["色"] = color
            changes.append(f"色: #{color}")
        else:
            raise click.ClickException("このオブジェクトには色プロパティがありません。")

    # Modify font (for text)
    if font is not None:
        text_effect = obj.get_effect("テキスト")
        if text_effect:
            text_effect.properties["フォント"] = font
            changes.append(f"フォント: {font}")
        else:
            raise click.ClickException("このオブジェクトにはテキスト効果がありません。")

    # Modify volume (for audio)
    if volume is not None:
        playback_effect = obj.get_effect("音声再生")
        if playback_effect:
            playback_effect.properties["音量"] = StaticValue(value=volume)
            changes.append(f"音量: {volume}")
        else:
            raise click.ClickException("このオブジェクトには音声再生効果がありません。")

    # Modify layer position
    if layer is not None:
        old_layer = obj.layer
        # Check for collision on new layer
        new_frame_start = frame_from if frame_from is not None else obj.frame_start
        new_frame_end = frame_to if frame_to is not None else obj.frame_end

        collisions = sc.find_collisions(layer, new_frame_start, new_frame_end, exclude_object_id=obj.object_id)

        if collisions and force:
            # Force mode: push colliding objects down
            try:
                movements = sc.resolve_collision_by_pushing_down(
                    layer, new_frame_start, new_frame_end, exclude_object_id=obj.object_id
                )
                for moved_id, old_l, new_l in movements:
                    moved_obj = next((o for o in sc.objects if o.object_id == moved_id), None)
                    obj_type = moved_obj.object_type if moved_obj else "不明"
                    safe_echo(f"  オブジェクト {moved_id} ({obj_type}) をレイヤー {old_l} → {new_l} に移動", err=True)
            except RuntimeError as e:
                raise click.ClickException(str(e))
        elif collisions:
            # Non-force mode: just warn
            for other_obj in collisions:
                safe_echo(f"警告: レイヤー {layer} のフレーム {other_obj.frame_start}-{other_obj.frame_end} に "
                         f"オブジェクト {other_obj.object_id} ({other_obj.object_type}) が存在します。", err=True)

        obj.layer = layer
        changes.append(f"レイヤー: {old_layer} → {layer}")

    # Modify frame range
    if frame_from is not None or frame_to is not None:
        old_start = obj.frame_start
        old_end = obj.frame_end
        target_layer = layer if layer is not None else obj.layer
        new_start = frame_from if frame_from is not None else obj.frame_start
        new_end = frame_to if frame_to is not None else obj.frame_end

        # Skip collision check if layer was already changed (already handled above)
        if layer is None:
            collisions = sc.find_collisions(target_layer, new_start, new_end, exclude_object_id=obj.object_id)

            if collisions and force:
                # Force mode: push colliding objects down
                try:
                    movements = sc.resolve_collision_by_pushing_down(
                        target_layer, new_start, new_end, exclude_object_id=obj.object_id
                    )
                    for moved_id, old_l, new_l in movements:
                        moved_obj = next((o for o in sc.objects if o.object_id == moved_id), None)
                        obj_type = moved_obj.object_type if moved_obj else "不明"
                        safe_echo(f"  オブジェクト {moved_id} ({obj_type}) をレイヤー {old_l} → {new_l} に移動", err=True)
                except RuntimeError as e:
                    raise click.ClickException(str(e))
            elif collisions:
                # Non-force mode: just warn
                for other_obj in collisions:
                    safe_echo(f"警告: レイヤー {target_layer} のフレーム {other_obj.frame_start}-{other_obj.frame_end} に "
                             f"オブジェクト {other_obj.object_id} ({other_obj.object_type}) が存在します。", err=True)

        if frame_from is not None:
            obj.frame_start = frame_from
            changes.append(f"開始フレーム: {old_start} → {frame_from}")

        if frame_to is not None:
            obj.frame_end = frame_to
            changes.append(f"終了フレーム: {old_end} → {frame_to}")

    # Modify effect name
    if effect_name is not None:
        if obj.main_effect:
            old_name = obj.main_effect.name
            obj.main_effect.name = effect_name
            changes.append(f"エフェクト名: {old_name} → {effect_name}")
        else:
            raise click.ClickException("このオブジェクトにはメインエフェクトがありません。")

    if not changes:
        raise click.ClickException("変更するプロパティを指定してください。")

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"オブジェクト {object_id} を変更:")
    for change in changes:
        safe_echo(f"  {change}")
    safe_echo(f"  保存先: {save_path}")


@main.command("copy")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.option("--layer", "-l", type=str, default="auto", help="コピー先レイヤー (autoで自動選択)")
@click.option("--from", "from_frame", type=int, default=None, help="コピー先開始フレーム（省略時は元と同じ）")
@click.option("--to", "to_frame", type=int, default=None, help="コピー先終了フレーム（省略時は元と同じ）")
@click.option("--offset", type=int, default=None, help="時間オフセット（元のフレームからずらす）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def copy_object(
    file: Path,
    object_id: int,
    layer: str,
    from_frame: int | None,
    to_frame: int | None,
    offset: int | None,
    scene: int,
    output: Path | None,
) -> None:
    """オブジェクトを複製する。

    \b
    例:
      aviutl2 copy project.aup2 0                    # 同じ位置に複製（自動レイヤー）
      aviutl2 copy project.aup2 0 --offset 100       # 100フレーム後に複製
      aviutl2 copy project.aup2 0 -l 5 --from 200    # レイヤー5, フレーム200から
    """
    import copy as copy_module

    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find source object
    src_obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            src_obj = o
            break

    if src_obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    # Calculate target position
    if offset is not None:
        new_start = src_obj.frame_start + offset
        new_end = src_obj.frame_end + offset
    else:
        new_start = from_frame if from_frame is not None else src_obj.frame_start
        new_end = to_frame if to_frame is not None else src_obj.frame_end

    # Determine layer
    auto_layer = False
    if layer == "auto":
        actual_layer = _find_available_layer(sc, new_start, new_end)
        auto_layer = True
    else:
        try:
            actual_layer = int(layer)
        except ValueError:
            raise click.ClickException(f"無効なレイヤー指定: {layer}")

        # Check for collisions
        for obj in sc.objects:
            if obj.layer == actual_layer and obj.frame_start <= new_end and obj.frame_end >= new_start:
                raise click.ClickException(
                    f"配置不可: レイヤー {actual_layer} フレーム {new_start}-{new_end} には"
                    f"既にオブジェクト(ID {obj.object_id})が存在します。"
                )

    # Generate new object ID
    new_id = max((o.object_id for o in sc.objects), default=-1) + 1

    # Deep copy effects
    new_effects = []
    for eff in src_obj.effects:
        new_eff = Effect(
            effect_id=eff.effect_id,
            name=eff.name,
            properties=copy_module.deepcopy(eff.properties),
        )
        new_effects.append(new_eff)

    # Create new object
    new_obj = TimelineObject(
        object_id=new_id,
        layer=actual_layer,
        frame_start=new_start,
        frame_end=new_end,
        effects=new_effects,
        focus=False,
    )

    sc.objects.append(new_obj)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    layer_info = f"レイヤー {actual_layer}" + (" (auto)" if auto_layer else "")
    safe_echo(f"オブジェクト複製: ID {object_id} -> ID {new_id}")
    safe_echo(f"  {layer_info}, フレーム {new_start}-{new_end}")
    safe_echo(f"  保存先: {save_path}")


# =============================================================================
# フィルタ・アニメーションコマンド
# =============================================================================


@main.group()
def filter() -> None:
    """フィルタエフェクトを操作する。"""
    pass


@filter.command("add")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.argument("filter_type", type=click.Choice([
    "blur", "glow", "fade", "gradient", "shadow", "border",
    "mosaic", "sharpen", "chromakey", "shake"
]))
@click.option("--strength", type=float, default=None, help="強度/範囲")
@click.option("--color", type=str, default=None, help="色（16進数）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def filter_add(
    file: Path,
    object_id: int,
    filter_type: str,
    strength: float | None,
    color: str | None,
    scene: int,
    output: Path | None,
) -> None:
    """オブジェクトにフィルタエフェクトを追加する。

    \b
    フィルタ種類:
      blur      - ぼかし
      glow      - グロー（発光）
      fade      - フェード
      gradient  - グラデーション
      shadow    - ドロップシャドウ
      border    - 縁取り
      mosaic    - モザイク
      sharpen   - シャープ
      chromakey - クロマキー
      shake     - 振動

    \b
    例:
      aviutl2 filter add project.aup2 0 blur --strength 10
      aviutl2 filter add project.aup2 0 glow --strength 50 --color ffff00
      aviutl2 filter add project.aup2 0 fade
    """
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    # Parse color if provided
    color_int = 16777215  # Default white
    if color:
        try:
            rgb = int(color, 16)
            r = (rgb >> 16) & 0xFF
            g = (rgb >> 8) & 0xFF
            b = rgb & 0xFF
            color_int = (b << 16) | (g << 8) | r  # BGR
        except ValueError:
            raise click.ClickException(f"無効な色指定: {color}")

    # Filter definitions - using AviUtl2 property names
    color_hex = color or "ffffff"  # Default white
    filter_map: dict[str, tuple[str, dict[str, Any]]] = {
        "blur": ("ぼかし", {
            "範囲": StaticValue(value=strength or 10.0),
            "縦横比": StaticValue(value=0.0),
            "光の強さ": StaticValue(value=0.0),
            "サイズ固定": StaticValue(value=0.0),
        }),
        "glow": ("グロー", {
            "強さ": StaticValue(value=strength or 50.0),
            "拡散": StaticValue(value=60),
            "角度": StaticValue(value=25.0),
            "しきい値": StaticValue(value=50.0),
            "比率": StaticValue(value=100.0),
            "ぼかし": StaticValue(value=1),
            "形状": "クロス(4本)",
            "光色": color_hex if color else "",
            "光成分のみ": StaticValue(value=0),
            "サイズ固定": StaticValue(value=0),
        }),
        "fade": ("フェード", {
            "イン": StaticValue(value=strength or 0.5),
            "アウト": StaticValue(value=strength or 0.5),
        }),
        "gradient": ("グラデーション", {
            "強さ": StaticValue(value=strength or 100.0),
            "中心X": StaticValue(value=0.0),
            "中心Y": StaticValue(value=0.0),
            "角度": StaticValue(value=0.0),
            "幅": StaticValue(value=100.0),
            "形状": "線",
            "開始色": color_hex,
            "終了色": "000000",
        }),
        "shadow": ("ドロップシャドウ", {
            "X": StaticValue(value=5),
            "Y": StaticValue(value=5),
            "濃さ": StaticValue(value=strength or 60.0),
            "拡散": StaticValue(value=5),
            "影色": "000000",
            "影を別オブジェクトで描画": StaticValue(value=0),
        }),
        "border": ("縁取り", {
            "サイズ": StaticValue(value=strength or 5),
            "ぼかし": StaticValue(value=0),
            "縁色": color_hex,
            "パターン画像": "",
        }),
        "mosaic": ("モザイク", {
            "サイズ": StaticValue(value=strength or 10.0),
            "タイル風": StaticValue(value=0.0),
        }),
        "sharpen": ("シャープ", {
            "強さ": StaticValue(value=strength or 50.0),
            "範囲": StaticValue(value=5.0),
        }),
        "chromakey": ("クロマキー", {
            "色相範囲": StaticValue(value=strength or 24.0),
            "彩度範囲": StaticValue(value=96.0),
            "境界補正": StaticValue(value=1.0),
        }),
        "shake": ("振動", {
            "X": StaticValue(value=strength or 10.0),
            "Y": StaticValue(value=strength or 10.0),
            "Z": StaticValue(value=0.0),
            "周期": StaticValue(value=1.0),
            "ランダム": StaticValue(value=0.0),
            "複雑さ": StaticValue(value=0.0),
        }),
    }

    filter_name, properties = filter_map[filter_type]

    # Generate new effect ID
    new_effect_id = max((e.effect_id for e in obj.effects), default=-1) + 1

    # Create effect
    new_effect = Effect(
        effect_id=new_effect_id,
        name=filter_name,
        properties=properties,
    )

    obj.effects.append(new_effect)

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"フィルタ追加: オブジェクト {object_id} に {filter_name}")
    if strength:
        safe_echo(f"  強度: {strength}")
    if color:
        safe_echo(f"  色: #{color}")
    safe_echo(f"  保存先: {save_path}")


@main.command("animate")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.argument("property_name", type=click.Choice(["x", "y", "z", "scale", "opacity", "rotation"]))
@click.option("--start", type=float, required=True, help="開始値")
@click.option("--end", type=float, required=True, help="終了値")
@click.option("--motion", type=click.Choice([
    "linear", "smooth", "instant", "bounce", "repeat"
]), default="linear", help="移動タイプ（デフォルト: linear）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def animate_property(
    file: Path,
    object_id: int,
    property_name: str,
    start: float,
    end: float,
    motion: str,
    scene: int,
    output: Path | None,
) -> None:
    """プロパティにアニメーションを設定する。

    \b
    プロパティ:
      x        - X座標
      y        - Y座標
      z        - Z座標
      scale    - 拡大率
      opacity  - 透明度
      rotation - 回転

    \b
    移動タイプ:
      linear   - 直線移動（デフォルト）
      smooth   - 補間移動（なめらか）
      instant  - 瞬間移動
      bounce   - 反復移動
      repeat   - 回転

    \b
    例:
      aviutl2 animate project.aup2 0 x --start -100 --end 100
      aviutl2 animate project.aup2 0 opacity --start 0 --end 100 --motion smooth
      aviutl2 animate project.aup2 0 rotation --start 0 --end 360 --motion linear
    """
    from aviutl2_api.models.values import AnimatedValue, AnimationParams

    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    # Find draw effect
    draw_effect = obj.get_effect("標準描画")
    if not draw_effect:
        raise click.ClickException("このオブジェクトには標準描画効果がありません。")

    # Map property names
    property_map = {
        "x": "X",
        "y": "Y",
        "z": "Z",
        "scale": "拡大率",
        "opacity": "透明度",
        "rotation": "Z軸回転",
    }

    # Map motion types
    motion_map = {
        "linear": "直線移動",
        "smooth": "補間移動",
        "instant": "瞬間移動",
        "bounce": "反復移動",
        "repeat": "回転",
    }

    prop_key = property_map[property_name]
    motion_type = motion_map[motion]

    # Create animated value
    animated_value = AnimatedValue(
        start=start,
        end=end,
        animation=AnimationParams(
            motion_type=motion_type,
            param="0",
        ),
    )

    draw_effect.properties[prop_key] = animated_value

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"アニメーション設定: オブジェクト {object_id}")
    safe_echo(f"  プロパティ: {prop_key}")
    safe_echo(f"  値: {start} → {end}")
    safe_echo(f"  移動タイプ: {motion_type}")
    safe_echo(f"  保存先: {save_path}")


# =============================================================================
# プリセットコマンド
# =============================================================================


@main.group()
def preset() -> None:
    """プリセットを管理する。"""
    pass


@preset.command("list")
def preset_list() -> None:
    """登録済みプリセット一覧を表示する。"""
    from aviutl2_api.presets import PresetManager

    manager = PresetManager()
    presets = manager.list_presets()

    if not presets:
        safe_echo("プリセットが登録されていません。")
        safe_echo("  'aviutl2 preset init' でサンプルプリセットを追加できます。")
        return

    safe_echo(f"登録済みプリセット ({len(presets)}件):")
    safe_echo("-" * 60)

    for p in presets:
        anim_count = len(p.animations)
        effect_count = len(p.effects)
        contents = []
        if anim_count > 0:
            contents.append(f"アニメーション:{anim_count}")
        if effect_count > 0:
            contents.append(f"エフェクト:{effect_count}")
        contents_str = ", ".join(contents) if contents else "空"

        safe_echo(f"  {p.id:20s} {p.name:16s} [{contents_str}]")


@preset.command("show")
@click.argument("preset_id")
def preset_show(preset_id: str) -> None:
    """プリセットの詳細を表示する。"""
    from aviutl2_api.presets import PresetManager

    manager = PresetManager()
    p = manager.get_preset(preset_id)

    if p is None:
        raise click.ClickException(f"プリセット '{preset_id}' が見つかりません。")

    safe_echo(f"=== プリセット: {p.name} ===")
    safe_echo(f"ID: {p.id}")
    safe_echo(f"説明: {p.description or '(なし)'}")

    if p.animations:
        safe_echo(f"\nアニメーション ({len(p.animations)}件):")
        for anim in p.animations:
            safe_echo(f"  {anim.property}: {anim.start} → {anim.end} ({anim.motion})")

    if p.effects:
        safe_echo(f"\nエフェクト ({len(p.effects)}件):")
        for eff in p.effects:
            safe_echo(f"  {eff.name}:")
            for key, val in eff.properties.items():
                safe_echo(f"    {key}: {val}")


@preset.command("apply")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.argument("preset_id")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def preset_apply(file: Path, object_id: int, preset_id: str, scene: int, output: Path | None) -> None:
    """オブジェクトにプリセットを適用する。

    \b
    例:
      aviutl2 preset apply project.aup2 0 spin-fade-out
      aviutl2 preset apply project.aup2 0 fade-in -o output.aup2
    """
    from aviutl2_api.models.values import AnimatedValue, AnimationParams
    from aviutl2_api.presets import PresetManager

    manager = PresetManager()
    p = manager.get_preset(preset_id)

    if p is None:
        raise click.ClickException(f"プリセット '{preset_id}' が見つかりません。")

    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    applied: list[str] = []

    # Apply animations
    if p.animations:
        draw_effect = obj.get_effect("標準描画")
        if not draw_effect:
            raise click.ClickException("このオブジェクトには標準描画効果がありません。")

        property_map = {
            "X": "X", "Y": "Y", "Z": "Z",
            "拡大率": "拡大率", "透明度": "透明度",
            "回転": "Z軸回転", "Z軸回転": "Z軸回転",
            "X軸回転": "X軸回転", "Y軸回転": "Y軸回転",
        }

        for anim in p.animations:
            if anim.property in property_map:
                prop_key = property_map[anim.property]
                animated_value = AnimatedValue(
                    start=anim.start,
                    end=anim.end,
                    animation=AnimationParams(
                        motion_type=anim.motion,
                        param=anim.param,
                    ),
                )
                draw_effect.properties[prop_key] = animated_value
                applied.append(f"アニメーション: {anim.property} ({anim.start}→{anim.end})")

    # Apply effects
    if p.effects:
        for eff_preset in p.effects:
            new_effect_id = max((e.effect_id for e in obj.effects), default=-1) + 1
            properties = {}
            for key, val in eff_preset.properties.items():
                properties[key] = StaticValue(value=val) if isinstance(val, (int, float)) else val

            new_effect = Effect(
                effect_id=new_effect_id,
                name=eff_preset.name,
                properties=properties,
            )
            obj.effects.append(new_effect)
            applied.append(f"エフェクト: {eff_preset.name}")

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"プリセット '{p.name}' を適用: オブジェクト {object_id}")
    for item in applied:
        safe_echo(f"  {item}")
    safe_echo(f"  保存先: {save_path}")


@preset.command("save")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("object_id", type=int)
@click.argument("preset_id")
@click.option("--name", "-n", type=str, default=None, help="プリセット表示名（省略時はIDと同じ）")
@click.option("--description", "-d", type=str, default="", help="説明")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
def preset_save(file: Path, object_id: int, preset_id: str, name: str | None, description: str, scene: int) -> None:
    """オブジェクトの設定をプリセットとして保存する。

    \b
    例:
      aviutl2 preset save project.aup2 0 my-effect
      aviutl2 preset save project.aup2 0 cool-animation -n "クールなアニメ" -d "説明文"
    """
    from aviutl2_api.presets import AnimationPreset, EffectPreset, Preset, PresetManager

    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Find object
    obj = None
    for o in sc.objects:
        if o.object_id == object_id:
            obj = o
            break

    if obj is None:
        raise click.ClickException(f"オブジェクト ID {object_id} が見つかりません。")

    animations: list[AnimationPreset] = []
    effects: list[EffectPreset] = []

    # Extract animations from standard draw effect
    draw_effect = obj.get_effect("標準描画")
    if draw_effect:
        animation_props = ["X", "Y", "Z", "拡大率", "透明度", "X軸回転", "Y軸回転", "Z軸回転"]
        for prop in animation_props:
            if prop in draw_effect.properties:
                val = draw_effect.properties[prop]
                if isinstance(val, AnimatedValue):
                    animations.append(AnimationPreset(
                        property=prop,
                        start=val.start,
                        end=val.end,
                        motion=val.animation.motion_type,
                        param=val.animation.param,
                    ))

    # Extract filter effects (skip base effects)
    base_effects = {"テキスト", "図形", "画像ファイル", "動画ファイル", "音声ファイル",
                    "標準描画", "映像再生", "音声再生"}
    for eff in obj.effects:
        if eff.name not in base_effects:
            props = {}
            for key, val in eff.properties.items():
                if isinstance(val, StaticValue):
                    props[key] = val.value
                elif isinstance(val, (int, float, str)):
                    props[key] = val
            effects.append(EffectPreset(name=eff.name, properties=props))

    if not animations and not effects:
        raise click.ClickException("保存可能なアニメーションまたはエフェクトがありません。")

    # Create and save preset
    new_preset = Preset(
        id=preset_id,
        name=name or preset_id,
        description=description,
        animations=animations,
        effects=effects,
    )

    manager = PresetManager()
    manager.add_preset(new_preset)

    safe_echo(f"プリセット保存: '{preset_id}'")
    safe_echo(f"  名前: {new_preset.name}")
    if animations:
        safe_echo(f"  アニメーション: {len(animations)}件")
    if effects:
        safe_echo(f"  エフェクト: {len(effects)}件")
    safe_echo(f"  保存先: {manager.preset_path}")


@preset.command("delete")
@click.argument("preset_id")
@click.option("--yes", "-y", is_flag=True, help="確認なしで削除")
def preset_delete(preset_id: str, yes: bool) -> None:
    """プリセットを削除する。"""
    from aviutl2_api.presets import PresetManager

    manager = PresetManager()
    p = manager.get_preset(preset_id)

    if p is None:
        raise click.ClickException(f"プリセット '{preset_id}' が見つかりません。")

    if not yes:
        safe_echo(f"削除対象: {p.id} ({p.name})")
        if not click.confirm("本当に削除しますか？"):
            safe_echo("キャンセルしました。")
            return

    manager.delete_preset(preset_id)
    safe_echo(f"プリセット '{preset_id}' を削除しました。")


@preset.command("init")
def preset_init() -> None:
    """サンプルプリセットで初期化する。"""
    from aviutl2_api.presets import PresetManager

    manager = PresetManager()
    added = manager.init_with_samples()

    if added > 0:
        safe_echo(f"サンプルプリセット {added}件 を追加しました。")
        safe_echo(f"  保存先: {manager.preset_path}")
        safe_echo("\n'aviutl2 preset list' で一覧を確認できます。")
    else:
        safe_echo("すべてのサンプルプリセットは既に登録済みです。")


# =============================================================================
# JSON変換コマンド
# =============================================================================


@main.command("export-json")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("output_file", type=click.Path(path_type=Path))
@click.option("--indent", "-i", type=int, default=2, help="インデント幅")
def export_json(file: Path, output_file: Path, indent: int) -> None:
    """プロジェクトをJSONとしてエクスポートする。"""
    project = parse_file(file)

    try:
        json_str = to_json(project, indent=indent)
        output_file.write_text(json_str, encoding="utf-8")
        safe_echo(f"JSON出力: {output_file}")
    except Exception as e:
        raise click.ClickException(f"エクスポートエラー: {e}")


@main.command("import-json")
@click.argument("json_file", type=click.Path(exists=True, path_type=Path))
@click.argument("output_file", type=click.Path(path_type=Path))
def import_json(json_file: Path, output_file: Path) -> None:
    """JSONからプロジェクトをインポートして.aup2として保存する。"""
    try:
        json_str = json_file.read_text(encoding="utf-8")
        project = from_json(json_str)
        serialize_to_file(project, output_file)
        safe_echo(f"JSONインポート完了: {json_file}")
        safe_echo(f"  出力先: {output_file}")
        _print_project_summary(project)
    except Exception as e:
        raise click.ClickException(f"インポートエラー: {e}")


# =============================================================================
# ヘルパー関数
# =============================================================================


def _get_media_duration_frames(file_path: str, fps: int) -> int | None:
    """Get media file duration in frames.

    Args:
        file_path: Path to media file (video or audio)
        fps: Project frame rate

    Returns:
        Duration in frames, or None if unable to detect
    """
    try:
        import cv2

        # Try to open the file with OpenCV
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return None

        # Get frame count and FPS from the file
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        file_fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        if frame_count > 0 and file_fps > 0:
            # Calculate duration in seconds, then convert to project frames
            duration_seconds = frame_count / file_fps
            return int(duration_seconds * fps)

        return None
    except Exception:
        return None


def _calculate_frame_range(
    scene: Scene,
    from_frame: int | None,
    to_frame: int | None,
    duration: int | None,
    media_path: str | None = None,
) -> tuple[int, int]:
    """Calculate frame range with auto-detection support.

    Args:
        scene: Scene object
        from_frame: Start frame (None for auto)
        to_frame: End frame (None for auto)
        duration: Duration in frames (None for auto)
        media_path: Path to media file for auto-detection (optional)

    Returns:
        Tuple of (from_frame, to_frame)
    """
    # Default duration (60 frames)
    default_duration = 60

    # Determine start frame
    if from_frame is None:
        # Start from the end of existing content, or 0 if empty
        if scene.objects:
            from_frame = scene.max_frame + 1
        else:
            from_frame = 0

    # Determine end frame
    if to_frame is None:
        # Try to auto-detect from media file
        if media_path and duration is None:
            detected_duration = _get_media_duration_frames(media_path, scene.fps)
            if detected_duration:
                duration = detected_duration
                safe_echo(f"メディアファイルから長さを自動検出: {duration}フレーム ({duration/scene.fps:.1f}秒)", err=True)

        # Use duration if specified, otherwise use default
        if duration is not None:
            to_frame = from_frame + duration - 1
        else:
            to_frame = from_frame + default_duration - 1

    return from_frame, to_frame


def _find_available_layer(scene: Scene, from_frame: int, to_frame: int) -> int:
    """Find the lowest available layer for the given time range."""
    used_layers: set[int] = set()

    for obj in scene.objects:
        # Check if this object overlaps with the target range
        if obj.frame_start <= to_frame and obj.frame_end >= from_frame:
            used_layers.add(obj.layer)

    # Find the lowest available layer (starting from 0)
    layer = 0
    while layer in used_layers:
        layer += 1

    return layer


def _check_overlap_warnings(
    scene: Scene,
    from_frame: int,
    to_frame: int,
    obj_type: str,
    warn_types: set[str],
) -> list[str]:
    """Check for overlapping objects and return warning messages.

    Args:
        scene: The scene to check
        from_frame: Start frame of new object
        to_frame: End frame of new object
        obj_type: Type of the new object being added
        warn_types: Set of object types to warn about overlap

    Returns:
        List of warning messages
    """
    warnings = []

    for obj in scene.objects:
        # Check time overlap
        if obj.frame_start <= to_frame and obj.frame_end >= from_frame:
            other_type = obj.object_type or "不明"
            # Warn if either the new type or existing type is in warn_types
            if obj_type in warn_types or other_type in warn_types:
                if other_type == obj_type:
                    warnings.append(
                        f"警告: レイヤー {obj.layer} に同種オブジェクト (ID {obj.object_id}: {other_type}) "
                        f"がフレーム {obj.frame_start}-{obj.frame_end} で重複"
                    )

    return warnings


def _print_project_summary(project: Project) -> None:
    """Print brief project summary."""
    safe_echo(f"  バージョン: {project.version}")
    safe_echo(f"  シーン数: {len(project.scenes)}")
    if project.scenes:
        sc = project.scenes[0]
        safe_echo(f"  解像度: {sc.width}x{sc.height} @ {sc.fps}fps")
        safe_echo(f"  オブジェクト数: {len(sc.objects)}")


def _print_project_info(project: Project, file_path: Path | None, modified: bool) -> None:
    """Print detailed project info."""
    safe_echo("=== プロジェクト情報 ===")
    if file_path:
        status = " (変更あり)" if modified else ""
        safe_echo(f"ファイル: {file_path}{status}")
    else:
        safe_echo("ファイル: (未保存)")

    safe_echo(f"バージョン: {project.version}")
    safe_echo(f"シーン数: {len(project.scenes)}")

    for i, sc in enumerate(project.scenes):
        safe_echo(f"\n--- シーン {i}: {sc.name} ---")
        safe_echo(f"  解像度: {sc.width}x{sc.height}")
        safe_echo(f"  フレームレート: {sc.fps}fps")
        safe_echo(f"  音声レート: {sc.audio_rate}Hz")
        safe_echo(f"  オブジェクト数: {len(sc.objects)}")
        if sc.objects:
            max_frame = sc.max_frame or 0
            safe_echo(f"  最大フレーム: {max_frame} ({max_frame / sc.fps:.2f}秒)")


def _parse_layer_filter(layer_str: str | None) -> set[int] | None:
    """Parse layer filter string into a set of layer numbers.

    Examples:
        "0" -> {0}
        "0-5" -> {0, 1, 2, 3, 4, 5}
        "0,2,4" -> {0, 2, 4}
        "0-2,5,7-9" -> {0, 1, 2, 5, 7, 8, 9}
    """
    if not layer_str:
        return None

    result: set[int] = set()
    parts = layer_str.split(",")

    for part in parts:
        part = part.strip()
        if "-" in part:
            # Range
            try:
                start, end = part.split("-", 1)
                for i in range(int(start), int(end) + 1):
                    result.add(i)
            except ValueError:
                pass
        else:
            # Single number
            try:
                result.add(int(part))
            except ValueError:
                pass

    return result if result else None


def _print_timeline(
    scene: Scene,
    from_frame: int,
    to_frame: int,
    width: int,
    layer_filter: set[int] | None = None,
    compact: bool = False,
) -> None:
    """Print ASCII timeline."""
    # Collect layers used
    layer_set = set(o.layer for o in scene.objects)

    # Apply layer filter
    if layer_filter is not None:
        layer_set = layer_set.intersection(layer_filter)

    if not layer_set:
        safe_echo("オブジェクトがありません。")
        return

    layers = sorted(layer_set)
    label_width = 8
    timeline_width = width - label_width - 2

    # Calculate scale
    frame_range = to_frame - from_frame
    if frame_range <= 0:
        frame_range = 1

    # Build timeline data for all layers first (for compact mode)
    layer_lines: dict[int, list[str]] = {}
    for layer in layers:
        layer_objs = [o for o in scene.objects if o.layer == layer]
        line = [" "] * timeline_width

        for obj in layer_objs:
            start_pos = int((obj.frame_start - from_frame) * timeline_width / frame_range)
            end_pos = int((obj.frame_end - from_frame) * timeline_width / frame_range)

            start_pos = max(0, min(timeline_width - 1, start_pos))
            end_pos = max(0, min(timeline_width - 1, end_pos))

            if start_pos <= end_pos:
                obj_char = obj.object_type[0] if obj.object_type else "?"
                for pos in range(start_pos, end_pos + 1):
                    if pos < timeline_width:
                        line[pos] = obj_char

        layer_lines[layer] = line

    # Compact mode: find contiguous empty regions and abbreviate
    if compact:
        # Find columns that are all empty
        empty_cols: list[bool] = []
        for col in range(timeline_width):
            is_empty = all(layer_lines[layer][col] == " " for layer in layers)
            empty_cols.append(is_empty)

        # Find contiguous empty regions (minimum 10 columns to abbreviate)
        regions: list[tuple[int, int]] = []  # (start, end) of empty regions
        in_region = False
        region_start = 0

        for i, is_empty in enumerate(empty_cols):
            if is_empty and not in_region:
                in_region = True
                region_start = i
            elif not is_empty and in_region:
                in_region = False
                if i - region_start >= 10:
                    regions.append((region_start, i - 1))

        if in_region and timeline_width - region_start >= 10:
            regions.append((region_start, timeline_width - 1))

        # Build abbreviated lines
        if regions:
            for layer in layers:
                new_line: list[str] = []
                last_end = 0

                for reg_start, reg_end in regions:
                    # Add content before region
                    new_line.extend(layer_lines[layer][last_end:reg_start])
                    # Add abbreviation marker
                    new_line.extend(list("..."))
                    last_end = reg_end + 1

                # Add remaining content
                new_line.extend(layer_lines[layer][last_end:])
                layer_lines[layer] = new_line

            # Adjust timeline_width
            timeline_width = len(layer_lines[layers[0]])

    # Print header
    filter_info = ""
    if layer_filter:
        filter_info = f" (レイヤー: {','.join(map(str, sorted(layer_filter)))})"

    safe_echo(f"タイムライン: フレーム {from_frame} - {to_frame}{filter_info}")
    safe_echo("=" * width)

    # Header with frame numbers
    header = " " * label_width + "|"
    if not compact:
        for i in range(0, timeline_width, 10):
            frame = from_frame + int(i * frame_range / timeline_width)
            header += f"{frame:<10}"[:10]
    else:
        header += f"{from_frame}...{to_frame}"
    safe_echo(header[:width])
    safe_echo("-" * width)

    # Print each layer
    for layer in layers:
        label = f"L{layer:02d}    "[:label_width]
        line_str = "".join(layer_lines[layer])[:width - label_width - 1]
        safe_echo(f"{label}|{line_str}")

    safe_echo("=" * width)
    safe_echo("凡例: 動=動画 画=画像 音=音声 図=図形 テ=テキスト")


# =============================================================================
# プレビューコマンド
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--frame", "-f", type=int, default=0, help="レンダリングするフレーム番号")
@click.option("--frames", type=str, default=None, help="複数フレーム指定（例: 0,30,60,90）")
@click.option("--strip", is_flag=True, help="フィルムストリップ表示")
@click.option("--interval", type=int, default=30, help="ストリップのフレーム間隔")
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="出力ファイルパス")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--background", "-b", type=str, default="000000", help="背景色（16進数、例: 000000）")
@click.option("--max-width", type=int, default=None, help="出力画像の最大幅（Vision AI向け縮小）")
@click.option("--max-height", type=int, default=None, help="出力画像の最大高さ")
@click.option("--scale", type=float, default=None, help="出力スケール（例: 0.5で50%縮小）")
def preview(
    file: Path,
    frame: int,
    frames: str | None,
    strip: bool,
    interval: int,
    output: Path,
    scene: int,
    background: str,
    max_width: int | None,
    max_height: int | None,
    scale: float | None,
) -> None:
    """プロジェクトのフレームプレビューを生成する。

    \b
    例:
      aviutl2 preview project.aup2 --frame 150 -o preview.png
      aviutl2 preview project.aup2 --frames 0,30,60 -o thumbs/
      aviutl2 preview project.aup2 --strip --interval 30 -o timeline.png

    \b
    Vision AI向け縮小:
      aviutl2 preview project.aup2 -f 0 -o small.png --max-width 800
      aviutl2 preview project.aup2 -f 0 -o half.png --scale 0.5
    """
    try:
        from aviutl2_api.renderer import FrameRenderer
    except ImportError as e:
        raise click.ClickException(
            f"レンダラーのインポートに失敗しました。opencv-python と pillow がインストールされていることを確認してください: {e}"
        )

    # Parse background color
    try:
        if len(background) == 6:
            r = int(background[0:2], 16)
            g = int(background[2:4], 16)
            b = int(background[4:6], 16)
            bg_color = (r, g, b, 255)
        else:
            bg_color = (0, 0, 0, 255)
    except ValueError:
        bg_color = (0, 0, 0, 255)

    # Load project
    project = parse_file(file)

    # Create renderer
    try:
        renderer = FrameRenderer(project, scene_id=scene, background_color=bg_color)
    except ValueError as e:
        raise click.ClickException(str(e))

    sc = project.get_scene(scene)
    if sc is None:
        raise click.ClickException(f"シーン {scene} が見つかりません。")

    # リサイズが必要かどうか
    needs_resize = scale is not None or max_width is not None or max_height is not None

    def apply_resize(buffer: FrameBuffer) -> tuple[FrameBuffer, list[str]]:
        """必要に応じて画像をリサイズ"""
        if not needs_resize:
            return buffer, []
        return buffer.resize(
            width=max_width,
            height=max_height,
            scale=scale,
            maintain_aspect=True,
        )

    if strip:
        # Filmstrip mode
        max_frame_num = sc.max_frame or 100
        result = renderer.render_strip(0, max_frame_num, interval)

        # リサイズ適用
        final_buffer, resize_warnings = apply_resize(result.buffer)
        for w in resize_warnings:
            safe_echo(w, err=True)

        output.parent.mkdir(parents=True, exist_ok=True)
        final_buffer.save(output)

        safe_echo(f"フィルムストリップ生成: {output}")
        safe_echo(f"  フレーム: 0 - {max_frame_num} (間隔: {interval})")
        if needs_resize:
            safe_echo(f"  出力解像度: {final_buffer.width}x{final_buffer.height}")
        safe_echo(f"  レンダリング時間: {result.render_time_ms:.1f}ms")

        if result.warnings:
            for w in result.warnings:
                safe_echo(f"  警告: {w}", err=True)
        if result.missing_media:
            safe_echo(f"  欠落ファイル: {len(result.missing_media)}件", err=True)

    elif frames:
        # Multiple frames mode
        frame_list = [int(f.strip()) for f in frames.split(",")]

        output.mkdir(parents=True, exist_ok=True)

        total_time = 0.0
        for f in frame_list:
            result = renderer.render_frame(f)

            # リサイズ適用
            final_buffer, resize_warnings = apply_resize(result.buffer)
            for w in resize_warnings:
                safe_echo(w, err=True)

            out_path = output / f"frame_{f:05d}.png"
            final_buffer.save(out_path)
            total_time += result.render_time_ms

            if result.warnings:
                for w in result.warnings:
                    safe_echo(f"フレーム {f} 警告: {w}", err=True)

        safe_echo(f"{len(frame_list)}フレームをレンダリング: {output}/")
        if needs_resize:
            safe_echo(f"  出力解像度: {final_buffer.width}x{final_buffer.height}")
        safe_echo(f"  合計時間: {total_time:.1f}ms")

    else:
        # Single frame mode
        result = renderer.render_frame(frame)

        # リサイズ適用
        final_buffer, resize_warnings = apply_resize(result.buffer)
        for w in resize_warnings:
            safe_echo(w, err=True)

        output.parent.mkdir(parents=True, exist_ok=True)
        final_buffer.save(output)

        safe_echo(f"フレーム {frame} をレンダリング: {output}")
        if needs_resize:
            safe_echo(f"  元解像度: {sc.width}x{sc.height}")
            safe_echo(f"  出力解像度: {final_buffer.width}x{final_buffer.height}")
        else:
            safe_echo(f"  解像度: {sc.width}x{sc.height}")
        safe_echo(f"  レンダリング時間: {result.render_time_ms:.1f}ms")

        if result.warnings:
            for w in result.warnings:
                safe_echo(f"  警告: {w}", err=True)
        if result.missing_media:
            safe_echo(f"  欠落ファイル: {len(result.missing_media)}件", err=True)


# =============================================================================
# ヘルパー関数
# =============================================================================


def _print_layers(scene: Scene) -> None:
    """Print layer summary."""
    layer_map: dict[int, list[TimelineObject]] = {}
    for obj in scene.objects:
        if obj.layer not in layer_map:
            layer_map[obj.layer] = []
        layer_map[obj.layer].append(obj)

    if not layer_map:
        safe_echo("オブジェクトがありません。")
        return

    safe_echo(f"レイヤー一覧 (使用中: {len(layer_map)}レイヤー)")
    safe_echo("-" * 50)

    for layer in sorted(layer_map.keys()):
        objs = layer_map[layer]
        types = {}
        for obj in objs:
            t = obj.object_type or "不明"
            types[t] = types.get(t, 0) + 1

        type_str = ", ".join(f"{t}:{c}" for t, c in types.items())
        safe_echo(f"  レイヤー {layer:3d}: {len(objs)}個 ({type_str})")


def _print_objects(objects: list[TimelineObject], verbose: bool) -> None:
    """Print object list."""
    if not objects:
        safe_echo("  (なし)")
        return

    for obj in objects:
        line = (f"  ID {obj.object_id:3d}: {obj.object_type or '不明':8s} "
                f"レイヤー {obj.layer:2d} フレーム {obj.frame_start:5d}-{obj.frame_end:5d}")
        safe_echo(line)

        if verbose:
            for eff in obj.effects:
                safe_echo(f"         エフェクト: {eff.name}")
                for key, val in eff.properties.items():
                    if isinstance(val, AnimatedValue):
                        val_str = f"{val.start} -> {val.end} ({val.animation.motion_type})"
                    elif isinstance(val, StaticValue):
                        val_str = str(val.value)
                    else:
                        val_str = str(val)[:30]
                    safe_echo(f"           {key}: {val_str}")


@main.command("batch")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--filter-type", type=str, default=None, help="オブジェクトタイプでフィルタ（正規表現）")
@click.option("--filter-text", type=str, default=None, help="テキスト内容でフィルタ（正規表現）")
@click.option("--filter-layer", type=str, default=None, help="レイヤー範囲でフィルタ（例: 1-5 または 3）")
@click.option("--x", type=float, default=None, help="X座標を変更")
@click.option("--y", type=float, default=None, help="Y座標を変更")
@click.option("--z", type=float, default=None, help="Z座標を変更")
@click.option("--scale", type=float, default=None, help="拡大率を変更")
@click.option("--opacity", type=float, default=None, help="透明度を変更（0-100）")
@click.option("--rotation", type=float, default=None, help="回転角度を変更")
@click.option("--size", type=float, default=None, help="サイズを変更（テキスト/図形）")
@click.option("--color", type=str, default=None, help="色を変更（16進数）")
@click.option("--font", type=str, default=None, help="フォントを変更（テキスト）")
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--dry-run", is_flag=True, help="実行せずマッチするオブジェクトのみ表示")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def batch_modify(
    file: Path,
    filter_type: str | None,
    filter_text: str | None,
    filter_layer: str | None,
    x: float | None,
    y: float | None,
    z: float | None,
    scale: float | None,
    opacity: float | None,
    rotation: float | None,
    size: float | None,
    color: str | None,
    font: str | None,
    scene: int,
    dry_run: bool,
    output: Path | None,
) -> None:
    """フィルタ条件に一致する全オブジェクトを一括編集する。

    \b
    例:
      # すべてのテキストオブジェクトの色を変更
      aviutl2 batch project.aup2 --filter-type "テキスト" --color ff0000

      # "Hello"を含むテキストを検索して編集
      aviutl2 batch project.aup2 --filter-text "Hello.*" --font "MS Gothic"

      # レイヤー1-3のすべてのオブジェクトを移動
      aviutl2 batch project.aup2 --filter-layer "1-3" --x 100 --y 50

      # ドライラン（マッチするオブジェクトのみ表示）
      aviutl2 batch project.aup2 --filter-type "図形" --dry-run
    """
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Filter objects
    filtered_objects = []
    for obj in sc.objects:
        # Filter by type (regex)
        if filter_type:
            try:
                if not re.search(filter_type, obj.object_type or ""):
                    continue
            except re.error as e:
                raise click.ClickException(f"正規表現エラー (--filter-type): {e}")

        # Filter by text content (regex)
        if filter_text:
            text_effect = obj.get_effect("テキスト")
            if not text_effect:
                continue
            text_content = text_effect.properties.get("テキスト", "")
            try:
                if not re.search(filter_text, str(text_content)):
                    continue
            except re.error as e:
                raise click.ClickException(f"正規表現エラー (--filter-text): {e}")

        # Filter by layer range
        if filter_layer:
            if "-" in filter_layer:
                try:
                    start, end = map(int, filter_layer.split("-"))
                    if not (start <= obj.layer <= end):
                        continue
                except ValueError:
                    raise click.ClickException(f"無効なレイヤー範囲: {filter_layer}")
            else:
                try:
                    layer_num = int(filter_layer)
                    if obj.layer != layer_num:
                        continue
                except ValueError:
                    raise click.ClickException(f"無効なレイヤー番号: {filter_layer}")

        filtered_objects.append(obj)

    if not filtered_objects:
        safe_echo("フィルタ条件に一致するオブジェクトがありません。")
        return

    safe_echo(f"マッチしたオブジェクト: {len(filtered_objects)}件")
    for obj in filtered_objects:
        safe_echo(f"  ID {obj.object_id}: {obj.object_type}, レイヤー {obj.layer}, "
                 f"フレーム {obj.frame_start}-{obj.frame_end}")

    if dry_run:
        safe_echo("\n--dry-run モードのため、変更は行いません。")
        return

    # Check if any modification is specified
    has_modification = any([
        x is not None, y is not None, z is not None,
        scale is not None, opacity is not None, rotation is not None,
        size is not None, color is not None, font is not None
    ])

    if not has_modification:
        raise click.ClickException("変更するプロパティを指定してください。")

    # Apply modifications to all filtered objects
    total_changes = 0
    for obj in filtered_objects:
        changes = []

        # Modify drawing properties
        draw_effect = obj.get_effect("標準描画")
        if draw_effect:
            if x is not None:
                draw_effect.properties["X"] = StaticValue(value=x)
                changes.append(f"X: {x}")
            if y is not None:
                draw_effect.properties["Y"] = StaticValue(value=y)
                changes.append(f"Y: {y}")
            if z is not None:
                draw_effect.properties["Z"] = StaticValue(value=z)
                changes.append(f"Z: {z}")
            if scale is not None:
                draw_effect.properties["拡大率"] = StaticValue(value=scale)
                changes.append(f"拡大率: {scale}%")
            if opacity is not None:
                draw_effect.properties["透明度"] = StaticValue(value=opacity)
                changes.append(f"透明度: {opacity}%")
            if rotation is not None:
                draw_effect.properties["Z軸回転"] = StaticValue(value=rotation)
                changes.append(f"Z軸回転: {rotation}°")

        # Modify size
        if size is not None:
            text_effect = obj.get_effect("テキスト")
            shape_effect = obj.get_effect("図形")
            if text_effect:
                text_effect.properties["サイズ"] = StaticValue(value=size)
                changes.append(f"フォントサイズ: {size}")
            elif shape_effect:
                shape_effect.properties["サイズ"] = StaticValue(value=size)
                changes.append(f"図形サイズ: {size}")

        # Modify color
        if color is not None:
            text_effect = obj.get_effect("テキスト")
            shape_effect = obj.get_effect("図形")
            if text_effect:
                text_effect.properties["文字色"] = color
                changes.append(f"文字色: #{color}")
            elif shape_effect:
                shape_effect.properties["色"] = color
                changes.append(f"色: #{color}")

        # Modify font
        if font is not None:
            text_effect = obj.get_effect("テキスト")
            if text_effect:
                text_effect.properties["フォント"] = font
                changes.append(f"フォント: {font}")

        if changes:
            total_changes += 1
            safe_echo(f"\nID {obj.object_id} を変更:")
            for change in changes:
                safe_echo(f"  {change}")

    # Save
    save_path = output or file
    serialize_to_file(project, save_path)

    safe_echo(f"\n{total_changes}個のオブジェクトを変更しました。")
    safe_echo(f"保存先: {save_path}")


@main.command("fix")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--scene", "-s", type=int, default=0, help="シーン番号")
@click.option("--dry-run", is_flag=True, help="実行せずに干渉のみ表示")
@click.option("--verbose", "-v", is_flag=True, help="詳細表示")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="出力先（省略時は上書き）")
def fix_collisions(
    file: Path,
    scene: int,
    dry_run: bool,
    verbose: bool,
    output: Path | None,
) -> None:
    """プロジェクト内の全干渉を検出・自動解決する。

    同一レイヤー・同一時刻に複数のオブジェクトが存在する場合、
    後のオブジェクトを下のレイヤーに自動的に押し下げます。

    \b
    例:
      # 干渉を検出（確認のみ）
      aviutl2 fix project.aup2 --dry-run

      # 干渉を自動解決
      aviutl2 fix project.aup2

      # 詳細情報を表示
      aviutl2 fix project.aup2 --verbose
    """
    project = parse_file(file)

    if scene >= len(project.scenes):
        raise click.ClickException(f"シーン {scene} は存在しません。")

    sc = project.scenes[scene]

    # Collect all collisions
    all_collisions: list[tuple[int, TimelineObject, TimelineObject]] = []

    # Check each layer for collisions
    layers = sorted(set(obj.layer for obj in sc.objects))

    for layer in layers:
        layer_objects = sorted(
            [obj for obj in sc.objects if obj.layer == layer],
            key=lambda o: (o.frame_start, o.object_id)
        )

        # Check for overlaps within this layer
        for i, obj1 in enumerate(layer_objects):
            for obj2 in layer_objects[i + 1:]:
                # Check if frame ranges overlap
                if not (obj1.frame_end < obj2.frame_start or obj2.frame_end < obj1.frame_start):
                    all_collisions.append((layer, obj1, obj2))

    if not all_collisions:
        safe_echo("干渉は検出されませんでした。")
        return

    # Display collisions
    safe_echo(f"干渉検出: {len(all_collisions)}件")
    safe_echo("")

    if verbose or dry_run:
        for layer, obj1, obj2 in all_collisions:
            safe_echo(f"レイヤー {layer}:")
            safe_echo(f"  ID {obj1.object_id} ({obj1.object_type}): フレーム {obj1.frame_start}-{obj1.frame_end}")
            safe_echo(f"  ID {obj2.object_id} ({obj2.object_type}): フレーム {obj2.frame_start}-{obj2.frame_end}")
            # Calculate overlap
            overlap_start = max(obj1.frame_start, obj2.frame_start)
            overlap_end = min(obj1.frame_end, obj2.frame_end)
            safe_echo(f"  重複区間: {overlap_start}-{overlap_end}")
            safe_echo("")

    if dry_run:
        safe_echo("--dry-run モードのため、変更は行いません。")
        return

    # Resolve collisions
    safe_echo("干渉を自動解決中...")
    safe_echo("")
    total_moved = 0
    moved_objects = set()  # Track globally which objects have been moved

    # Process all collisions, moving the second object in each pair
    for layer, obj1, obj2 in all_collisions:
        # Skip if obj2 has already been moved
        if obj2.object_id in moved_objects:
            continue

        # Find a safe layer for obj2
        new_layer = obj2.layer + 1
        while sc.find_collisions(new_layer, obj2.frame_start, obj2.frame_end, exclude_object_id=obj2.object_id):
            new_layer += 1

        old_layer = obj2.layer
        obj2.layer = new_layer
        moved_objects.add(obj2.object_id)
        total_moved += 1

        safe_echo(f"  ID {obj2.object_id} ({obj2.object_type}): レイヤー {old_layer} → {new_layer}")

    # Save
    if total_moved > 0:
        save_path = output or file
        serialize_to_file(project, save_path)
        safe_echo(f"\n{total_moved}個のオブジェクトを移動しました。")
        safe_echo(f"保存先: {save_path}")
    else:
        safe_echo("\n移動が必要なオブジェクトはありませんでした。")


if __name__ == "__main__":
    main()
