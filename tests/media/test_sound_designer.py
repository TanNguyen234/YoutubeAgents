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


def test_sound_designer_receives_shot_narration(tmp_path: Path):
    """Ensure MediaProductionPipeline propagates ShotSpec narration into SoundDesigner's scene plans."""
    import hashlib
    from unittest.mock import MagicMock
    from app.db.repository import SQLiteRepository
    from app.domain.enums import ContentFormat, VideoLifecycleState
    from app.domain.models import Channel, Scene, Script, VideoProject
    from app.media.director.director_service import AutoDirectorService
    from app.media.director.models import ShotSpec, ShotTimeline, Storyboard, TimelineShot, VisualModality
    from app.media.models import MediaQAResult, RenderResult, TTSResult
    from app.media.pipeline import MediaProductionPipeline

    repo = MagicMock(spec=SQLiteRepository)
    project_id = "proj_audio_context"

    narration_1 = "Database engines write ahead to protect crash resilience."
    script = Script(
        id="scr_01",
        title="WAL Architecture",
        hook="How databases protect data.",
        scenes=[Scene(index=0, narration=narration_1, target_duration_seconds=3.0)],
        total_word_count=8,
        estimated_duration_seconds=3.0,
    )
    project = VideoProject(
        id=project_id,
        channel_id="chan_01",
        title="WAL Architecture",
        state=VideoLifecycleState.VERIFIED,
        script=script,
    )
    repo.get_video_project.return_value = project
    repo.get_channel.return_value = Channel(
        id="chan_01",
        title="Tech Channel",
        handle="@tech",
        niche="programming",
        target_audience="developers",
    )
    repo.get_research_dossier.return_value = None
    repo.get_fact_check_report.return_value = None
    repo.get_assets_by_project.return_value = []

    mock_director = MagicMock(spec=AutoDirectorService)
    timeline = ShotTimeline(
        shots=[
            TimelineShot(
                shot_id="shot_01",
                scene_index=0,
                beat_id="b_01",
                start=0.0,
                end=3.0,
                duration=3.0,
                asset_path=str(tmp_path / "shot_01.png"),
                asset_sha256="fake_sha",
                modality=VisualModality.DIAGRAM,
            )
        ],
        total_duration=3.0,
    )
    storyboard = Storyboard(
        project_id=project_id,
        total_duration=3.0,
        content_format=ContentFormat.EXPLAINER,
        shots=[
            ShotSpec(
                shot_id="shot_01",
                beat_id="b_01",
                scene_index=0,
                narration_segment=narration_1,
                purpose="explain",
                duration_seconds=3.0,
                subject="WAL",
                action="explain",
                visual_modality=VisualModality.DIAGRAM,
            )
        ],
    )
    mock_director.plan_and_render_timeline.return_value = (timeline, storyboard)

    expected_hash = hashlib.sha256(script.get_canonical_narration().encode("utf-8")).hexdigest()
    mock_tts = MagicMock()
    mock_tts.synthesize.return_value = TTSResult(
        audio_path=str(tmp_path / "audio.mp3"),
        duration_seconds=3.0,
        word_timings=[],
        sample_rate=24000,
        audio_sha256="audio_sha",
        backend="edge_tts",
        voice="en-US-GuyNeural",
        canonical_narration_sha256=expected_hash,
    )
    (tmp_path / "audio.mp3").write_bytes(b"audio")
    (tmp_path / "shot_01.png").write_bytes(b"shot")
    (tmp_path / "final.mp4").write_bytes(b"mp4")

    mock_renderer = MagicMock()
    mock_render_result = RenderResult(
        project_id=project_id,
        video_path=str(tmp_path / "final.mp4"),
        content_sha256="final_sha",
        file_size_bytes=100,
        duration_seconds=3.0,
    )
    mock_renderer.render_timeline.return_value = mock_render_result

    mock_qa = MagicMock()
    mock_qa_res = MediaQAResult(
        passed=True,
        file_path=str(tmp_path / "final.mp4"),
        file_size_bytes=100,
        video_duration=3.0,
        audio_duration=3.0,
        duration_drift=0.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        loudness_lufs=-14.0,
    )
    mock_qa.inspect_video.return_value = (None, mock_qa_res)

    mock_sound_designer = MagicMock()
    mock_sound_designer.generate_sfx_track.return_value = (str(tmp_path / "sfx.wav"), "sfx_sha")
    (tmp_path / "sfx.wav").write_bytes(b"sfx")

    mock_music_gen = MagicMock()
    mock_music_gen.generate_track.return_value = (str(tmp_path / "bgm.wav"), "bgm_sha")
    (tmp_path / "bgm.wav").write_bytes(b"bgm")

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=mock_tts,
        renderer=mock_renderer,
        director_service=mock_director,
        qa_inspector=mock_qa,
        sound_designer=mock_sound_designer,
        music_generator=mock_music_gen,
        base_output_dir=tmp_path,
    )

    pipeline.run_production(project_id=project_id)

    # Assert SoundDesigner received non-empty semantic narration segment
    assert mock_sound_designer.generate_sfx_track.called
    called_plans = mock_sound_designer.generate_sfx_track.call_args[1]["scene_plans"]
    assert len(called_plans) == 1
    assert called_plans[0].narration_segment == narration_1

    # Assert BGM received profile-aware settings for 'programming' niche (Code Native: minimal_electronic, 80 bpm)
    assert mock_music_gen.generate_track.called
    call_kwargs = mock_music_gen.generate_track.call_args[1]
    assert call_kwargs["mood"] == "minimal_electronic"
    assert call_kwargs["bpm"] == 80
