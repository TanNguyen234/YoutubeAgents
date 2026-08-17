"""Tests for scene visual generation and scene duration planning."""

from pathlib import Path
import pytest
from PIL import Image

from app.domain.models import Scene, Script
from app.media.scene_planner import ScenePlanner, ScenePlanningError
from app.media.subtitles import SubtitleGenerator
from app.media.visual_factory import VisualFactory


def test_visual_card_generation_dimensions_and_hash(tmp_path: Path):
    """Visual factory must render 1080x1920 PNG cards with valid SHA-256."""
    factory = VisualFactory(width=1080, height=1920)
    card_path = tmp_path / "card_01.png"

    path_str, sha256 = factory.render_scene_card(
        scene_index=0,
        channel_name="Database Deep Dives",
        topic_title="SQLite WAL Architecture",
        scene_headline="How WAL mode enables concurrent readers without locks",
        output_path=card_path,
        scene_total=3,
    )

    assert card_path.exists()
    assert len(sha256) == 64

    # Verify real image dimensions
    with Image.open(card_path) as img:
        assert img.size == (1080, 1920)
        assert img.format == "PNG"


def test_scene_planner_proportional_allocation(tmp_path: Path):
    """Scene planner must distribute audio duration across all scenes proportionally."""
    script = Script(
        id="sc-plan",
        title="SQLite WAL Concurrency",
        hook="Why is SQLite WAL mode so fast?",
        scenes=[
            Scene(scene_index=0, narration="Scene 1 has six words here.", hook="Hook 1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Scene 2 has twelve words right here to test proportional duration distribution.", hook="Hook 2", visual_prompt="P2"),
            Scene(scene_index=2, narration="Scene 3 has six words too.", hook="Hook 3", visual_prompt="P3"),
        ],
        total_word_count=24,
        estimated_duration_seconds=12.0,
    )
    total_audio_duration = 20.0
    planner = ScenePlanner()

    plans = planner.plan_scenes(
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=total_audio_duration,
        output_scenes_dir=tmp_path / "scenes",
    )

    assert len(plans) == 3
    total_allocated = sum(p.target_duration_seconds for p in plans)
    assert abs(total_allocated - total_audio_duration) < 0.1

    for plan in plans:
        assert Path(plan.visual_asset_path).exists()
        assert len(plan.visual_asset_sha256) == 64
        assert plan.target_duration_seconds > 0.0
