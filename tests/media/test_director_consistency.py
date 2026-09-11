"""Tests for Director consistency, dossier wiring, reasoning backend propagation, and fallback modality accounting."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.backend import ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import ContentFormat, VideoLifecycleState
from app.domain.models import Channel, Claim, FactCheckReport, ResearchDossier, ResearchSource, Scene, Script, VideoProject
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    BeatPurpose,
    NarrativeBeat,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualIntent,
    VisualModality,
)
from app.media.models import MediaQAResult, RenderResult, TTSResult
from app.media.pipeline import MediaProductionPipeline


class FakeReasoningBackend(ReasoningBackend):
    def generate_text(self, prompt: str, **kwargs) -> str:
        return "fake reasoning response"


def test_media_pipeline_loads_research_dossier_from_repository(tmp_path: Path):
    """Ensure MediaProductionPipeline retrieves real ResearchDossier and FactCheckReport and passes them to Director."""
    repo = MagicMock(spec=SQLiteRepository)
    project_id = "proj_test_dossier"

    script = Script(
        id="scr_01",
        title="WAL Architecture",
        hook="How databases protect data.",
        scenes=[Scene(index=0, narration="Write ahead logging buffers sequential disk writes.", target_duration_seconds=3.0)],
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
    dossier = ResearchDossier(
        id="dos_01",
        topic_id="top_01",
        sources=[ResearchSource(id="src_01", title="PG Docs", url="https://postgresql.org", content_sha256="hash123")],
        claims=[],
        summary="Dossier summary",
    )
    fact_report = FactCheckReport(
        id="fc_01",
        project_id=project_id,
        claims=[],
        audit_summary="Fact report summary",
    )

    repo.get_video_project.return_value = project
    repo.get_channel.return_value = Channel(
        id="chan_01",
        title="Tech Channel",
        handle="@tech",
        niche="programming",
        target_audience="developers",
        default_voice="en-US-GuyNeural",
    )
    repo.get_research_dossier.return_value = dossier
    repo.get_fact_check_report.return_value = fact_report
    repo.get_assets_by_project.return_value = []

    mock_director = MagicMock(spec=AutoDirectorService)
    mock_timeline = ShotTimeline(
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
    mock_storyboard = Storyboard(
        project_id=project_id,
        total_duration=3.0,
        content_format=ContentFormat.EXPLAINER,
        shots=[
            ShotSpec(
                shot_id="shot_01",
                beat_id="b_01",
                scene_index=0,
                narration_segment="Write ahead logging buffers sequential disk writes.",
                purpose="explain",
                duration_seconds=3.0,
                subject="WAL",
                action="explain",
                visual_modality=VisualModality.DIAGRAM,
            )
        ],
    )
    mock_director.plan_and_render_timeline.return_value = (mock_timeline, mock_storyboard)

    # Mock TTS and Subtitles to run quickly without live external services
    mock_tts = MagicMock()
    expected_hash = hashlib.sha256(script.get_canonical_narration().encode("utf-8")).hexdigest()
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
    (tmp_path / "audio.mp3").write_bytes(b"mock audio bytes")
    (tmp_path / "shot_01.png").write_bytes(b"mock shot bytes")

    mock_renderer = MagicMock()
    mock_render_result = RenderResult(
        project_id=project_id,
        video_path=str(tmp_path / "final.mp4"),
        content_sha256="final_sha",
        file_size_bytes=100,
        duration_seconds=3.0,
    )
    mock_renderer.render_timeline.return_value = mock_render_result
    mock_renderer.render_video.return_value = mock_render_result
    (tmp_path / "final.mp4").write_bytes(b"mock video bytes")

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

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=mock_tts,
        renderer=mock_renderer,
        director_service=mock_director,
        qa_inspector=mock_qa,
        base_output_dir=tmp_path,
    )

    pipeline.run_production(project_id=project_id)

    # Assert that repo was queried for real dossier and fact report
    repo.get_research_dossier.assert_called_once_with(project_id)
    repo.get_fact_check_report.assert_called_once_with(project_id)

    # Assert that Director received them explicitly
    mock_director.plan_and_render_timeline.assert_called_once()
    _, kwargs = mock_director.plan_and_render_timeline.call_args
    assert kwargs["dossier"] == dossier
    assert kwargs["fact_report"] == fact_report


def test_reasoning_backend_reaches_beat_decomposer():
    """Verify that a ReasoningBackend provided to MediaProductionPipeline is forwarded to AutoDirectorService and BeatDecomposer."""
    fake_backend = FakeReasoningBackend()
    repo = MagicMock(spec=SQLiteRepository)

    pipeline = MediaProductionPipeline(
        repository=repo,
        reasoning_backend=fake_backend,
    )

    assert pipeline.director is not None
    assert pipeline.director.backend == fake_backend
    assert pipeline.director.decomposer.backend == fake_backend


def test_fallback_updates_actual_modality(tmp_path: Path):
    """Verify that when a requested modality falls back (e.g. STOCK_VIDEO without stock provider or ungrounded DOCUMENT_EVIDENCE), the actual modality is recorded on ShotSpec and TimelineShot."""
    director = AutoDirectorService()

    # Create a shot requesting STOCK_VIDEO (unsupported, should fall back to MOTION_GRAPHICS)
    stock_shot = ShotSpec(
        shot_id="shot_stock",
        beat_id="b_stock",
        scene_index=0,
        narration_segment="Datacenter servers humming at scale.",
        purpose="explain",
        duration_seconds=2.5,
        subject="Datacenter",
        action="show servers",
        visual_modality=VisualModality.STOCK_VIDEO,
    )

    result = director._generate_shot_asset(
        shot=stock_shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Server Architecture",
        channel_name="Tech Channel",
    )

    assert result.requested_modality == VisualModality.STOCK_VIDEO
    assert result.actual_modality in (VisualModality.MOTION_GRAPHICS, VisualModality.DIAGRAM)
    assert Path(result.path).exists()


def test_director_run_state_is_reset_between_projects(tmp_path: Path):
    """Ensure asset_attempts, shot_evaluations, and shot_attempt_counts are cleared before each plan_and_render_timeline run."""
    director = AutoDirectorService()

    # Simulate dirty state from Project A
    director.asset_attempts.append(MagicMock())
    director.shot_evaluations["proj_a_shot_01"] = MagicMock()
    director.shot_attempt_counts["proj_a_shot_01"] = 3

    script = Script(
        id="scr_proj_b",
        title="Project B Topic",
        hook="Hook",
        scenes=[Scene(index=0, narration="Explanation for Project B.", target_duration_seconds=3.0)],
        total_word_count=5,
        estimated_duration_seconds=3.0,
    )

    director.plan_and_render_timeline(
        project_id="proj_b",
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=3.0,
        output_dir=tmp_path,
    )

    # State from Project A must NOT be present
    assert "proj_a_shot_01" not in director.shot_evaluations
    assert "proj_a_shot_01" not in director.shot_attempt_counts
    # Only attempts from Project B should be recorded
    for attempt in director.asset_attempts:
        assert "proj_a" not in attempt.shot_id
