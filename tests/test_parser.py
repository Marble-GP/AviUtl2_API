"""Tests for the .aup2 parser."""

import pytest
from pathlib import Path

from aviutl2_api import parse_file, parse_string, to_json, from_json, serialize


SAMPLES_DIR = Path(__file__).parent.parent / "samples"


class TestParserBasic:
    """Basic parser tests."""

    def test_parse_empty_project(self):
        """Test parsing an empty project."""
        project = parse_file(SAMPLES_DIR / "EmptyProject.aup2")

        assert project.version == 2001901
        assert len(project.scenes) == 1

        scene = project.scenes[0]
        assert scene.scene_id == 0
        assert scene.name == "Root"
        assert scene.width == 1920
        assert scene.height == 1080
        assert scene.fps == 30
        assert len(scene.objects) == 0

    def test_parse_sample_project(self):
        """Test parsing the main sample project."""
        project = parse_file(SAMPLES_DIR / "AviUtl2_sample_project_file_1.aup2")

        assert project.version == 2001901
        assert len(project.scenes) == 1

        scene = project.scenes[0]
        assert scene.width == 1920
        assert scene.height == 1080
        assert scene.fps == 30

        # Should have multiple objects
        assert len(scene.objects) > 0

        # Check first object
        obj = scene.objects[0]
        assert obj.object_id == 0
        assert obj.layer == 0
        assert obj.frame_start == 0
        assert obj.frame_end == 299
        assert len(obj.effects) >= 2

        # Check first effect
        effect = obj.effects[0]
        assert effect.name == "動画ファイル"

    def test_parse_motion_project(self):
        """Test parsing project with animations."""
        project = parse_file(SAMPLES_DIR / "ShapeMotionTestProject.aup2")

        scene = project.scenes[0]
        assert len(scene.objects) > 0

        # Find object with animation
        obj = scene.objects[0]  # 円 with linear motion
        assert obj.object_type == "図形"

        # Check for animated value in standard draw effect
        draw_effect = obj.effects[1]
        assert draw_effect.name == "標準描画"

        # X should be animated
        x_value = draw_effect.properties.get("X")
        assert x_value is not None

    def test_parse_special_chars(self):
        """Test parsing project with special characters."""
        project = parse_file(SAMPLES_DIR / "SpecialChars.aup2")

        scene = project.scenes[0]
        obj = scene.objects[0]
        text_effect = obj.effects[0]

        assert text_effect.name == "テキスト"
        text_content = text_effect.properties.get("テキスト")
        assert text_content is not None
        # Should contain special chars like [, ], =
        assert "[" in str(text_content) or "\\n" in str(text_content)

    def test_parse_all_filters_and_movements(self):
        """Test parsing project with all filters and movement types."""
        project = parse_file(SAMPLES_DIR / "AllFiltersAndMovements.aup2")

        scene = project.scenes[0]
        assert len(scene.objects) == 24

        # Collect all effect names
        effects = set()
        for obj in scene.objects:
            for eff in obj.effects:
                effects.add(eff.name)

        # Should have many filter types
        assert "色調補正" in effects
        assert "グラデーション" in effects
        assert "ぼかし" in effects
        assert "グロー" in effects
        assert "フェード" in effects
        assert "振動" in effects

        # Check motion types are detected
        motion_obj = scene.objects[14]  # 直線移動の例
        draw_effect = motion_obj.effects[1]
        x_value = draw_effect.properties.get("X")
        assert "AnimatedValue" in str(type(x_value).__name__)


class TestJsonConversion:
    """Tests for JSON conversion."""

    def test_to_json_and_back(self):
        """Test JSON roundtrip."""
        project = parse_file(SAMPLES_DIR / "EmptyProject.aup2")

        # Convert to JSON
        json_str = to_json(project)
        assert isinstance(json_str, str)
        assert "scenes" in json_str

        # Parse back
        project2 = from_json(json_str)

        assert project2.version == project.version
        assert len(project2.scenes) == len(project.scenes)
        assert project2.scenes[0].width == project.scenes[0].width

    def test_json_with_objects(self):
        """Test JSON conversion with timeline objects."""
        project = parse_file(SAMPLES_DIR / "AviUtl2_sample_project_file_1.aup2")

        json_str = to_json(project)
        project2 = from_json(json_str)

        assert len(project2.scenes[0].objects) == len(project.scenes[0].objects)


class TestSerializer:
    """Tests for .aup2 serialization."""

    def test_serialize_empty_project(self):
        """Test serializing an empty project."""
        project = parse_file(SAMPLES_DIR / "EmptyProject.aup2")

        output = serialize(project)

        # Should contain key sections
        assert "[project]" in output
        assert "[scene.0]" in output
        assert "version=2001901" in output
        assert "video.width=1920" in output

        # Should use CRLF
        assert "\r\n" in output

    def test_roundtrip_parse_serialize_parse(self):
        """Test that parse -> serialize -> parse preserves data."""
        original = parse_file(SAMPLES_DIR / "EmptyProject.aup2")

        # Serialize
        aup2_str = serialize(original)

        # Parse again
        reparsed = parse_string(aup2_str)

        # Compare key properties
        assert reparsed.version == original.version
        assert len(reparsed.scenes) == len(original.scenes)

        orig_scene = original.scenes[0]
        new_scene = reparsed.scenes[0]

        assert new_scene.width == orig_scene.width
        assert new_scene.height == orig_scene.height
        assert new_scene.fps == orig_scene.fps
        assert new_scene.name == orig_scene.name


class TestModels:
    """Tests for data models."""

    def test_scene_helpers(self):
        """Test Scene helper methods."""
        project = parse_file(SAMPLES_DIR / "AviUtl2_sample_project_file_1.aup2")
        scene = project.scenes[0]

        # Test get_objects_at_frame
        objects_at_0 = scene.get_objects_at_frame(0)
        assert len(objects_at_0) > 0

        # Test get_objects_on_layer
        objects_on_layer_0 = scene.get_objects_on_layer(0)
        assert len(objects_on_layer_0) > 0

        # Test max_frame
        assert scene.max_frame > 0

    def test_timeline_object_duration(self):
        """Test TimelineObject duration calculation."""
        project = parse_file(SAMPLES_DIR / "AviUtl2_sample_project_file_1.aup2")
        obj = project.scenes[0].objects[0]

        # frame=0,299 means 300 frames
        assert obj.duration_frames == 300

        # At 30fps, that's 10 seconds
        assert obj.duration_seconds(30) == 10.0
