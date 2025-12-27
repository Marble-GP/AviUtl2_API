"""Preset management for AviUtl2 effects and animations.

Presets allow saving and reusing combinations of filters and animations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AnimationPreset:
    """A single animation setting within a preset."""

    property: str  # Property name (X, Y, 拡大率, 透明度, 回転, etc.)
    start: float
    end: float
    motion: str = "直線移動"  # Motion type
    param: str = "0"  # Motion parameter

    def to_dict(self) -> dict[str, Any]:
        return {
            "property": self.property,
            "start": self.start,
            "end": self.end,
            "motion": self.motion,
            "param": self.param,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationPreset:
        return cls(
            property=data["property"],
            start=data["start"],
            end=data["end"],
            motion=data.get("motion", "直線移動"),
            param=data.get("param", "0"),
        )


@dataclass
class EffectPreset:
    """A filter effect within a preset."""

    name: str  # Effect name (ぼかし, グロー, etc.)
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EffectPreset:
        return cls(
            name=data["name"],
            properties=data.get("properties", {}),
        )


@dataclass
class Preset:
    """A complete preset with animations and effects."""

    id: str  # Unique identifier (slug)
    name: str  # Display name
    description: str = ""
    animations: list[AnimationPreset] = field(default_factory=list)
    effects: list[EffectPreset] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "animations": [a.to_dict() for a in self.animations],
            "effects": [e.to_dict() for e in self.effects],
        }

    @classmethod
    def from_dict(cls, preset_id: str, data: dict[str, Any]) -> Preset:
        return cls(
            id=preset_id,
            name=data.get("name", preset_id),
            description=data.get("description", ""),
            animations=[
                AnimationPreset.from_dict(a) for a in data.get("animations", [])
            ],
            effects=[EffectPreset.from_dict(e) for e in data.get("effects", [])],
        )


class PresetManager:
    """Manages preset storage and retrieval."""

    def __init__(self, preset_path: Path | None = None):
        """Initialize preset manager.

        Args:
            preset_path: Custom preset file path. If None, uses default location.
        """
        if preset_path is None:
            self.preset_path = self._default_preset_path()
        else:
            self.preset_path = preset_path

        self._presets: dict[str, Preset] = {}
        self._load()

    @staticmethod
    def _default_preset_path() -> Path:
        """Get default preset file path."""
        home = Path.home()
        config_dir = home / ".aviutl2"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "presets.json"

    def _load(self) -> None:
        """Load presets from file."""
        if not self.preset_path.exists():
            self._presets = {}
            return

        try:
            content = self.preset_path.read_text(encoding="utf-8")
            data = json.loads(content)
            presets_data = data.get("presets", {})

            self._presets = {}
            for preset_id, preset_data in presets_data.items():
                self._presets[preset_id] = Preset.from_dict(preset_id, preset_data)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load presets: {e}")
            self._presets = {}

    def _save(self) -> None:
        """Save presets to file."""
        data = {
            "presets": {
                preset_id: preset.to_dict()
                for preset_id, preset in self._presets.items()
            }
        }

        content = json.dumps(data, ensure_ascii=False, indent=2)
        self.preset_path.parent.mkdir(parents=True, exist_ok=True)
        self.preset_path.write_text(content, encoding="utf-8")

    def list_presets(self) -> list[Preset]:
        """Get all presets."""
        return list(self._presets.values())

    def get_preset(self, preset_id: str) -> Preset | None:
        """Get a preset by ID."""
        return self._presets.get(preset_id)

    def add_preset(self, preset: Preset) -> None:
        """Add or update a preset."""
        self._presets[preset.id] = preset
        self._save()

    def delete_preset(self, preset_id: str) -> bool:
        """Delete a preset."""
        if preset_id in self._presets:
            del self._presets[preset_id]
            self._save()
            return True
        return False

    def init_with_samples(self) -> int:
        """Initialize with sample presets. Returns count of added presets."""
        samples = get_sample_presets()
        added = 0
        for preset in samples:
            if preset.id not in self._presets:
                self._presets[preset.id] = preset
                added += 1
        if added > 0:
            self._save()
        return added


def get_sample_presets() -> list[Preset]:
    """Get sample presets for initialization."""
    return [
        Preset(
            id="spin-fade-out",
            name="回転フェードアウト",
            description="10回転しながら縮小してフェードアウトする定番エフェクト",
            animations=[
                AnimationPreset(
                    property="Z軸回転", start=0, end=3600, motion="直線移動"
                ),
                AnimationPreset(
                    property="拡大率", start=100, end=10, motion="直線移動"
                ),
                AnimationPreset(
                    property="透明度", start=0, end=100, motion="直線移動"
                ),
            ],
        ),
        Preset(
            id="fade-in",
            name="フェードイン",
            description="透明から不透明にフェードイン",
            animations=[
                AnimationPreset(
                    property="透明度", start=100, end=0, motion="直線移動"
                ),
            ],
        ),
        Preset(
            id="fade-out",
            name="フェードアウト",
            description="不透明から透明にフェードアウト",
            animations=[
                AnimationPreset(
                    property="透明度", start=0, end=100, motion="直線移動"
                ),
            ],
        ),
        Preset(
            id="slide-in-left",
            name="左からスライドイン",
            description="画面左から中央へスライドイン",
            animations=[
                AnimationPreset(property="X", start=-1000, end=0, motion="補間移動"),
            ],
        ),
        Preset(
            id="slide-in-right",
            name="右からスライドイン",
            description="画面右から中央へスライドイン",
            animations=[
                AnimationPreset(property="X", start=1000, end=0, motion="補間移動"),
            ],
        ),
        Preset(
            id="slide-out-right",
            name="右へスライドアウト",
            description="中央から画面右へスライドアウト",
            animations=[
                AnimationPreset(property="X", start=0, end=1000, motion="補間移動"),
            ],
        ),
        Preset(
            id="bounce-vertical",
            name="縦バウンス",
            description="Y軸で上下に反復運動",
            animations=[
                AnimationPreset(
                    property="Y", start=-100, end=100, motion="反復移動", param="4|5"
                ),
            ],
        ),
        Preset(
            id="bounce-horizontal",
            name="横バウンス",
            description="X軸で左右に反復運動",
            animations=[
                AnimationPreset(
                    property="X", start=-100, end=100, motion="反復移動", param="4|5"
                ),
            ],
        ),
        Preset(
            id="zoom-in",
            name="ズームイン",
            description="小さい状態から拡大",
            animations=[
                AnimationPreset(
                    property="拡大率", start=0, end=100, motion="補間移動"
                ),
            ],
        ),
        Preset(
            id="zoom-out",
            name="ズームアウト",
            description="通常サイズから縮小",
            animations=[
                AnimationPreset(
                    property="拡大率", start=100, end=0, motion="補間移動"
                ),
            ],
        ),
        Preset(
            id="spin-once",
            name="1回転",
            description="360度回転",
            animations=[
                AnimationPreset(
                    property="Z軸回転", start=0, end=360, motion="直線移動"
                ),
            ],
        ),
        Preset(
            id="orbit",
            name="公転",
            description="中心を軸に円周上を移動（半径100、中心(0,100)）",
            animations=[
                # 回転モードでは (中心位置, 初期位置) を指定
                # 半径 = sqrt((X2-X1)^2 + (Y2-Y1)^2 + (Z2-Z1)^2)
                # この設定: 中心(0,100,0)、初期位置(0,200,0)、半径=100
                AnimationPreset(
                    property="X", start=0, end=0, motion="回転",
                    param="4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481"
                ),
                AnimationPreset(
                    property="Y", start=100, end=200, motion="回転",
                    param="4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481"
                ),
                AnimationPreset(
                    property="Z", start=0, end=0, motion="回転",
                    param="4|360|0,1,0.485532,-0.475783,1,0,0.484522,-0.481481"
                ),
            ],
        ),
        Preset(
            id="shake",
            name="振動",
            description="振動エフェクトを追加",
            effects=[
                EffectPreset(
                    name="振動",
                    properties={
                        "X": 10.0,
                        "Y": 10.0,
                        "Z": 0.0,
                        "周期": 1.0,
                        "ランダム": 1.0,
                    },
                ),
            ],
        ),
        Preset(
            id="glow-pulse",
            name="グローパルス",
            description="グローエフェクトを追加",
            effects=[
                EffectPreset(
                    name="グロー",
                    properties={
                        "強さ": 50.0,
                        "拡散": 60,
                        "角度": 25.0,
                        "しきい値": 50.0,
                        "比率": 100.0,
                        "ぼかし": 1,
                        "形状": "クロス(4本)",
                        "光色": "",
                        "光成分のみ": 0,
                        "サイズ固定": 0,
                    },
                ),
            ],
        ),
        Preset(
            id="blur-soft",
            name="ソフトぼかし",
            description="軽いぼかしエフェクト",
            effects=[
                EffectPreset(
                    name="ぼかし",
                    properties={
                        "範囲": 5.0,
                        "縦横比": 0.0,
                    },
                ),
            ],
        ),
        Preset(
            id="text-shadow",
            name="テキストシャドウ",
            description="テキスト用のドロップシャドウ",
            effects=[
                EffectPreset(
                    name="ドロップシャドウ",
                    properties={
                        "X": 5,
                        "Y": 5,
                        "濃さ": 60.0,
                        "拡散": 5,
                        "影色": "000000",
                        "影を別オブジェクトで描画": 0,
                    },
                ),
            ],
        ),
        Preset(
            id="border-white",
            name="白縁取り",
            description="白い縁取りを追加",
            effects=[
                EffectPreset(
                    name="縁取り",
                    properties={
                        "サイズ": 5,
                        "ぼかし": 0,
                        "縁色": "ffffff",
                        "パターン画像": "",
                    },
                ),
            ],
        ),
    ]
