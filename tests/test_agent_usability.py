from __future__ import annotations

import runpy
from pathlib import Path

from aviutl2_api import LocalProject, effect, parse_file
from aviutl2_api.renderer import FrameRenderer

ROOT = Path(__file__).parents[1]


def test_agent_docs_and_examples_are_present_and_importable() -> None:
    card = (ROOT / "docs" / "AGENT_API_CARD.md").read_text(encoding="utf-8")
    assert "LocalProject" in card
    assert "SyncSession" in card
    assert "save_source(overwrite=True" in card

    for name in ("local_checkpoint.py", "explicit_sync.py"):
        namespace = runpy.run_path(str(ROOT / "examples" / name))
        assert callable(namespace["main"])


def test_renderer_uses_a_typed_filter_pipeline() -> None:
    project = parse_file(ROOT / "samples" / "EmptyProject.aup2")
    renderer = FrameRenderer(project)

    assert renderer._filter_config == {}
    assert renderer._filter_pipeline
    assert all(
        callable(candidate.can_apply) and callable(candidate.apply)
        for candidate in renderer._filter_pipeline
    )
    result = renderer.render_frame(0)
    assert result.success
    assert (result.buffer.width, result.buffer.height) == (1920, 1080)

    local = LocalProject.load(ROOT / "samples" / "EmptyProject.aup2")
    local.add_shape(
        "circle",
        duration=10,
        effects=[effect("glow", strength=30)],
    )
    filtered = FrameRenderer(local.model).render_frame(0)
    assert filtered.success
    assert not filtered.warnings
