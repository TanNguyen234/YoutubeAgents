"""Integration tests for the full Media Production Pipeline and state machine lifecycle."""

import hashlib
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import AssetType, PlatformFormat, QualityStatus, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.models import TTSResult
from app.media.pipeline import MediaProductionPipeline
from tests.media.test_ffmpeg_renderer import _create_dummy_wav


class MockTTSBackend:
    """Deterministic TTS double for testing pipeline state machine."""

    def __init__(self, duration_seconds: float = 4.0):
        self.duration = duration_seconds

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice=None,
        language="en",
        rate="+0%",
        pitch="+0Hz",
    ) -> TTSResult:
        _create_dummy_wav(output_path, duration_seconds=self.duration)
        content_bytes = output_path.read_bytes()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=self.duration,
            sample_rate=44100,
            backend="mock-tts",
            voice=voice or "mock-voice",
            rate=rate,
            pitch=pitch,
            canonical_narration_sha256=hashlib.sha256(text.strip().encode("utf-8")).hexdigest(),
            audio_sha256=hashlib.sha256(content_bytes).hexdigest(),
        )


@pytest.fixture
def repo_with_verified_project(tmp_path: Path) -> tuple[SQLiteRepository, str]:
    db_path = tmp_path / "pipeline_test.db"
    init_database(db_path)
    repo = SQLiteRepository(db_path)

    channel = Channel(
        id="chan-01",
        title="Engineering Channel",
        handle="@engchannel",
        niche="Databases",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    script = Script(
        id="sc-verified",
        title="SQLite WAL Architecture",
        hook="How does SQLite WAL mode work?",
        scenes=[
            Scene(scene_index=0, narration="First scene explaining WAL readers.", hook="Hook 1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Second scene explaining WAL writers.", hook="Hook 2", visual_prompt="P2"),
        ],
        total_word_count=12,
        estimated_duration_seconds=6.0,
    )

    project = VideoProject(
        id="proj-verified-01",
        channel_id="chan-01",
        title="SQLite WAL Architecture",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)

    return repo, project.id


def test_media_pipeline_end_to_end_success(repo_with_verified_project, tmp_path: Path):
    """Full lifecycle execution: VERIFIED -> PRODUCING -> RENDERED -> READY_FOR_REVIEW."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=4.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out",
    )

    final_proj, qa_res, manifest = pipeline.run_production(project_id=project_id)

    # 1. State machine assertions
    assert final_proj.state == VideoLifecycleState.READY_FOR_REVIEW
    history = repo.get_state_history(project_id)
    states = [h["to_state"] for h in history]
    assert states[-3:] == ["PRODUCING", "RENDERED", "READY_FOR_REVIEW"]

    # 2. QA verdict
    assert qa_res.passed is True
    assert final_proj.quality is not None
    assert final_proj.quality.status == QualityStatus.PASSED

    # 3. Persisted Asset records in DB
    assets = repo.get_assets_by_project(project_id)
    asset_types = {a.asset_type for a in assets}
    assert AssetType.AUDIO_VOICEOVER in asset_types
    assert AssetType.SUBTITLES in asset_types
    assert AssetType.FINAL_VIDEO in asset_types

    # 4. Manifest generation
    manifest_file = tmp_path / "out" / project_id / "manifests" / "render_manifest.json"
    assert manifest_file.exists()
    assert manifest.qa_verdict == "PASSED"
    assert manifest.project_id == project_id
    assert len(manifest.production_fingerprint) == 64


def test_media_pipeline_idempotency_returns_cached_render(repo_with_verified_project, tmp_path: Path):
    """Subsequent call with identical project & fingerprint reuses cached render without re-executing."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_idem",
    )

    # First run
    proj_1, qa_1, manifest_1 = pipeline.run_production(project_id=project_id)
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW

    # Second run (idempotent)
    proj_2, qa_2, manifest_2 = pipeline.run_production(project_id=project_id)
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_2.production_fingerprint == manifest_1.production_fingerprint
    assert manifest_2.final_video_sha256 == manifest_1.final_video_sha256


def test_media_pipeline_qa_failure_transitions_to_qa_failed(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """When QA fails, the project must transition to QA_FAILED and cannot reach READY_FOR_REVIEW."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_qafail",
    )

    # Force QA to fail
    from app.domain.enums import QualityStatus
    from app.domain.models import QualityResult
    from app.media.models import MediaQAResult

    def mock_inspect(*args, **kwargs):
        return (
            QualityResult(
                id=f"qa-{project_id}",
                project_id=project_id,
                status=QualityStatus.FAILED,
                loudness_lufs=-14.0,
                duration_seconds=3.0,
                sync_drift_ms=999.0,
                issues=["Simulated severe audio-video sync drift failure"],
            ),
            MediaQAResult(
                passed=False,
                file_path="mock.mp4",
                file_size_bytes=1000,
                video_duration=3.0,
                audio_duration=1.0,
                duration_drift=2.0,
                width=1080,
                height=1920,
                fps=30.0,
                video_codec="h264",
                audio_codec="aac",
                pixel_format="yuv420p",
                loudness_lufs=-14.0,
                issues=["Simulated severe audio-video sync drift failure"],
            ),
        )

    monkeypatch.setattr(pipeline.qa, "inspect_video", mock_inspect)

    final_proj, qa_res, manifest = pipeline.run_production(project_id=project_id)

    assert final_proj.state == VideoLifecycleState.QA_FAILED
    assert qa_res.passed is False
    assert manifest.qa_verdict == "FAILED"
    history = repo.get_state_history(project_id)
    states = [h["to_state"] for h in history]
    assert states[-1] == "QA_FAILED"
    assert "READY_FOR_REVIEW" not in states


def test_media_pipeline_failure_does_not_strand_in_producing(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """When rendering throws an unhandled exception, project transitions to FAILED without getting stuck in PRODUCING."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_fail",
    )

    def mock_render_fail(*args, **kwargs):
        raise RuntimeError("Simulated unhandled renderer crash")

    monkeypatch.setattr(pipeline.renderer, "render_video", mock_render_fail)

    with pytest.raises(RuntimeError, match="Simulated unhandled renderer crash"):
        pipeline.run_production(project_id=project_id)

    curr_p = repo.get_video_project(project_id)
    assert curr_p.state == VideoLifecycleState.FAILED
