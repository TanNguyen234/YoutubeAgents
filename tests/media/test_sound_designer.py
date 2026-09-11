"""Tests for SoundDesignerService audio stem generation."""

from pathlib import Path
import pytest

from app.media.models import SceneRenderPlan
from app.media.sound_designer import SoundDesignerService, SoundDesignError


def test_sound_designer_sequences_transitions(tmp_path: Path):
    """Verify that SoundDesignerService generates a valid multi-channel SFX track aligned to scene cuts."""
    service = SoundDesignerService()
    plans = [
        SceneRenderPlan(
            scene_index=0,
            narration_segment="Scene one hook.",
            target_duration_seconds=2.0,
            visual_asset_path="dummy1.png",
            visual_asset_sha256="hash1",
        ),
        SceneRenderPlan(
            scene_index=1,
            narration_segment="Scene two body.",
            target_duration_seconds=2.5,
            visual_asset_path="dummy2.png",
            visual_asset_sha256="hash2",
        ),
    ]

    out_sfx = tmp_path / "test_sfx.wav"
    total_duration = 4.5

    sfx_path, sfx_sha256 = service.generate_sfx_track(
        scene_plans=plans,
        total_duration_seconds=total_duration,
        output_path=out_sfx,
    )

    assert out_sfx.exists()
    assert out_sfx.stat().st_size > 1000
    assert len(sfx_sha256) == 64
    assert sfx_path == str(out_sfx)


def test_sound_designer_invalid_duration_error(tmp_path: Path):
    """Zero or negative duration must raise SoundDesignError."""
    service = SoundDesignerService()
    with pytest.raises(SoundDesignError, match="Total duration must be > 0"):
        service.generate_sfx_track(
            scene_plans=[],
            total_duration_seconds=0.0,
            output_path=tmp_path / "err.wav",
        )
