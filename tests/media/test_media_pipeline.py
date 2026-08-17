"""Integration tests for the full Media Production Pipeline and state machine lifecycle."""

import hashlib
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import AssetType, PlatformFormat, QualityStatus, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.models import RenderProfile, TTSResult
from app.media.pipeline import MediaProductionPipeline
from tests.media.test_ffmpeg_renderer import _create_dummy_wav


def _create_mock_wav(output_path: Path, duration_seconds: float = 6.0, tag: str = ""):
    """Generate a valid audible sine wave WAV file modulated by tag for deterministic test variation."""
    import math
    import struct
    import wave

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    freq_offset = int(hashlib.md5(tag.encode("utf-8")).hexdigest()[:4], 16) % 20
    frequency = 440.0 + freq_offset
    amplitude = 16000
    num_frames = int(sample_rate * duration_seconds)

    frames = bytearray()
    for i in range(num_frames):
        val = int(amplitude * math.sin(2.0 * math.pi * frequency * i / sample_rate))
        frames.extend(struct.pack("<hh", val, val))

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)


class MockTTSBackend:
    """Deterministic TTS double for testing pipeline state machine and cache invalidation."""

    backend_name = "mock-tts"
    default_voice = "mock-voice"

    def __init__(self, duration_seconds: float = 6.0):
        self.duration = duration_seconds
        self.call_count = 0

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice=None,
        language="en",
        rate="+0%",
        pitch="+0Hz",
    ) -> TTSResult:
        self.call_count += 1
        resolved_voice = voice or self.default_voice
        _create_mock_wav(output_path, duration_seconds=self.duration, tag=f"{resolved_voice}|{rate}|{pitch}|{text}")
        content_bytes = output_path.read_bytes()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=self.duration,
            sample_rate=44100,
            backend="mock-tts",
            voice=resolved_voice,
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
        base_output_dir=tmp_path / "out_e2e",
    )

    project, qa_res, manifest = pipeline.run_production(project_id=project_id)

    assert project.state == VideoLifecycleState.READY_FOR_REVIEW
    assert qa_res.passed is True
    assert manifest.qa_verdict == "PASSED"
    assert Path(manifest.final_video_path).exists()
    assert manifest.final_video_size_bytes > 0

    # Verify project in DB
    db_project = repo.get_video_project(project_id)
    assert db_project is not None
    assert db_project.state == VideoLifecycleState.READY_FOR_REVIEW


def test_media_pipeline_idempotency_returns_cached_render(repo_with_verified_project, tmp_path: Path):
    """Calling production twice with same inputs returns cached manifest without re-rendering."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_cache",
    )

    # First run
    proj_1, qa_1, manifest_1 = pipeline.run_production(project_id=project_id)
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW
    assert tts.call_count == 1

    # Second run (idempotent cache hit)
    proj_2, qa_2, manifest_2 = pipeline.run_production(project_id=project_id)
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert tts.call_count == 1  # TTS was not called again
    assert manifest_1.production_fingerprint == manifest_2.production_fingerprint
    assert manifest_1.final_video_sha256 == manifest_2.final_video_sha256


def test_media_pipeline_qa_failure_transitions_to_qa_failed(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """When QA inspection fails, project must transition from RENDERED to QA_FAILED."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_qa_fail",
    )

    # Force QA failure
    def mock_inspect(*args, **kwargs):
        from app.domain.models import QualityResult
        from app.media.models import MediaQAResult
        q = QualityResult(
            id=f"qa-{project_id}",
            project_id=project_id,
            status=QualityStatus.FAILED,
            loudness_lufs=-28.0,
            duration_seconds=3.0,
            sync_drift_ms=10.0,
            issues=["Audio too quiet (-28.0 LUFS)"],
        )
        qa_r = MediaQAResult(
            passed=False,
            file_path="mock.mp4",
            file_size_bytes=1000,
            video_duration=3.0,
            audio_duration=3.0,
            duration_drift=0.0,
            width=1080,
            height=1920,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            pixel_format="yuv420p",
            loudness_lufs=-28.0,
            issues=["Audio too quiet (-28.0 LUFS)"],
        )
        return q, qa_r

    monkeypatch.setattr(pipeline.qa, "inspect_video", mock_inspect)

    project, qa_res, manifest = pipeline.run_production(project_id=project_id)
    assert project.state == VideoLifecycleState.QA_FAILED
    assert qa_res.passed is False
    assert manifest.qa_verdict == "FAILED"

    curr_p = repo.get_video_project(project_id)
    assert curr_p.state == VideoLifecycleState.QA_FAILED


def test_media_pipeline_failure_does_not_strand_in_producing(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """Unhandled renderer crashes must transition project to FAILED and not strand in PRODUCING."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_fail",
    )

    def mock_render_fail(*args, **kwargs):
        raise RuntimeError("Simulated unhandled renderer crash during FFmpeg execution")

    monkeypatch.setattr(pipeline.renderer, "render_video", mock_render_fail)

    with pytest.raises(RuntimeError, match="Simulated unhandled renderer crash"):
        pipeline.run_production(project_id=project_id)

    curr_p = repo.get_video_project(project_id)
    assert curr_p.state == VideoLifecycleState.FAILED


def _create_verified_project_in_repo(repo: SQLiteRepository, project_id: str, scenes=None) -> str:
    script = Script(
        id=f"sc-{project_id}",
        title="SQLite WAL Architecture",
        hook="How does SQLite WAL mode work?",
        scenes=scenes or [
            Scene(scene_index=0, narration="First scene explaining WAL readers.", hook="Hook 1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Second scene explaining WAL writers.", hook="Hook 2", visual_prompt="P2"),
        ],
        total_word_count=12,
        estimated_duration_seconds=6.0,
    )
    project = VideoProject(
        id=project_id,
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
    return project.id


def test_media_pipeline_voice_change_invalidates_cache(repo_with_verified_project, tmp_path: Path):
    """Changing voice on SAME project must invalidate cache, re-render, and advance lifecycle."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=6.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_voice")

    # Run 1
    proj_1, _, manifest_1 = pipeline.run_production(project_id=project_id, voice="en-US-GuyNeural")
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_1.voice == "en-US-GuyNeural"
    assert tts.call_count == 1

    # Run 2 on SAME project with different voice
    proj_2, _, manifest_2 = pipeline.run_production(project_id=project_id, voice="en-US-JennyNeural")
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_2.voice == "en-US-JennyNeural"
    assert tts.call_count == 2
    assert manifest_2.production_fingerprint != manifest_1.production_fingerprint
    assert manifest_2.audio_sha256 != manifest_1.audio_sha256

    # Verify lifecycle history recorded the second production cycle
    history = repo.get_state_history(project_id)
    states = [h["to_state"] for h in history]
    assert states[-4:] == [
        "READY_FOR_REVIEW",
        "PRODUCING",
        "RENDERED",
        "READY_FOR_REVIEW",
    ]


def test_media_pipeline_rate_change_invalidates_cache(repo_with_verified_project, tmp_path: Path):
    """Changing TTS rate on SAME project must invalidate cache, re-render, and produce distinct audio."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=6.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_rate")

    proj_1, _, manifest_1 = pipeline.run_production(project_id=project_id, rate="+0%")
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW
    assert tts.call_count == 1

    proj_2, _, manifest_2 = pipeline.run_production(project_id=project_id, rate="+15%")
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_2.tts_rate == "+15%"
    assert tts.call_count == 2
    assert manifest_2.production_fingerprint != manifest_1.production_fingerprint
    assert manifest_2.audio_sha256 != manifest_1.audio_sha256


def test_media_pipeline_pitch_change_invalidates_cache(repo_with_verified_project, tmp_path: Path):
    """Changing TTS pitch on SAME project must invalidate cache, re-render, and produce distinct audio."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=6.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_pitch")

    proj_1, _, manifest_1 = pipeline.run_production(project_id=project_id, pitch="+0Hz")
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW
    assert tts.call_count == 1

    proj_2, _, manifest_2 = pipeline.run_production(project_id=project_id, pitch="-50Hz")
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_2.tts_pitch == "-50Hz"
    assert tts.call_count == 2
    assert manifest_2.production_fingerprint != manifest_1.production_fingerprint
    assert manifest_2.audio_sha256 != manifest_1.audio_sha256


def test_media_pipeline_profile_change_invalidates_cache(repo_with_verified_project, tmp_path: Path):
    """Changing render profile dimensions on SAME project must invalidate cache and re-render."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=6.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_prof")

    prof_1 = RenderProfile(name="SHORTS_9_16", width=1080, height=1920)
    prof_2 = RenderProfile(name="LANDSCAPE_16_9", width=1920, height=1080)

    proj_1, _, manifest_1 = pipeline.run_production(project_id=project_id, profile=prof_1)
    assert proj_1.state == VideoLifecycleState.READY_FOR_REVIEW

    proj_2, _, manifest_2 = pipeline.run_production(project_id=project_id, profile=prof_2)
    assert proj_2.state == VideoLifecycleState.READY_FOR_REVIEW
    assert manifest_2.render_profile == "LANDSCAPE_16_9"
    assert manifest_2.production_fingerprint != manifest_1.production_fingerprint


def test_media_pipeline_same_project_cache_reuse_after_rerender(repo_with_verified_project, tmp_path: Path):
    """After a rerender with new config, subsequent run with the same new config returns cache hit."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=6.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_cache_hit")

    # Run 1: voice Guy
    pipeline.run_production(project_id=project_id, voice="en-US-GuyNeural")
    assert tts.call_count == 1

    # Run 2: voice Jenny (cache miss -> re-render)
    _, _, m2 = pipeline.run_production(project_id=project_id, voice="en-US-JennyNeural")
    assert tts.call_count == 2

    # Run 3: voice Jenny (cache hit -> return cached)
    proj_3, _, m3 = pipeline.run_production(project_id=project_id, voice="en-US-JennyNeural")
    assert tts.call_count == 2  # Not called again
    assert m3.production_fingerprint == m2.production_fingerprint
    assert m3.final_video_sha256 == m2.final_video_sha256
    assert proj_3.state == VideoLifecycleState.READY_FOR_REVIEW


def test_media_pipeline_scene_change_invalidates_cache(repo_with_verified_project, tmp_path: Path):
    """Mutating scene narration text must produce a different fingerprint and force a new render."""
    repo, project_id = repo_with_verified_project
    mutated_scenes = [
        Scene(scene_index=0, narration="Mutated narration altering the first scene completely.", hook="Hook 1", visual_prompt="P1"),
        Scene(scene_index=1, narration="Second scene explaining WAL writers.", hook="Hook 2", visual_prompt="P2"),
    ]
    p2_id = _create_verified_project_in_repo(repo, "proj-scene-02", scenes=mutated_scenes)
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_scene")

    _, _, manifest_1 = pipeline.run_production(project_id=project_id)
    _, _, manifest_2 = pipeline.run_production(project_id=p2_id)
    assert manifest_2.production_fingerprint != manifest_1.production_fingerprint


def test_media_pipeline_blocked_on_missing_capabilities(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """Missing pre-flight binary or pillow library must transition project to BLOCKED cleanly."""
    from app.media.capabilities import MediaCapabilities
    from app.media.pipeline import MediaProductionBlockerError

    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_blk")

    def mock_check_caps(*args, **kwargs):
        return MediaCapabilities(
            ffmpeg_available=False,
            ffprobe_available=True,
            tts_available=True,
            pillow_available=False,
            output_writable=True,
            blockers=["Simulated missing FFmpeg", "Simulated missing Pillow"],
        )

    monkeypatch.setattr("app.media.pipeline.check_media_capabilities", mock_check_caps)

    with pytest.raises(MediaProductionBlockerError, match="Media capabilities unmet"):
        pipeline.run_production(project_id=project_id)

    curr_p = repo.get_video_project(project_id)
    assert curr_p.state == VideoLifecycleState.BLOCKED


def test_media_pipeline_blocked_on_tts_network_error(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """Environmental network failure during TTS synthesis must transition project to BLOCKED."""
    from app.media.tts.edge_tts_backend import TTSBlockerError

    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(repository=repo, tts_backend=tts, base_output_dir=tmp_path / "out_tts_blk")

    def mock_synthesize_net_fail(*args, **kwargs):
        raise TTSBlockerError("EdgeTTS network/environmental connectivity failure: connection refused")

    monkeypatch.setattr(pipeline.tts, "synthesize", mock_synthesize_net_fail)

    with pytest.raises(TTSBlockerError, match="connectivity failure"):
        pipeline.run_production(project_id=project_id)

    curr_p = repo.get_video_project(project_id)
    assert curr_p.state == VideoLifecycleState.BLOCKED
