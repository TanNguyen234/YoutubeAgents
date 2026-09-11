"""Tests for multimodal pipeline integration: AudioModes (Voice, Sound, Both) and GFlow/Kaggle."""

from pathlib import Path
import wave
import pytest
import numpy as np

from app.db.repository import SQLiteRepository
from app.domain.enums import PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.gflow_provider import GFlowMediaProvider
from app.media.kaggle_audio_provider import (
    AudioKind,
    KaggleAudioConfig,
    KaggleAudioProvider,
    RealisticFoleySpec,
)
from app.media.models import AudioMode, RenderProfile
from app.media.pipeline import MediaProductionPipeline
from app.media.tts.base import TTSBackend
from app.media.models import TTSResult


class MockFastTTS(TTSBackend):
    """Fast deterministic TTS test double producing valid stereo audio."""
    def __init__(self):
        self.backend_name = "mock-tts"
        self.default_voice = "en-US-GuyNeural"

    def synthesize(self, text: str, output_path: Path, voice=None, rate="+0%", pitch="+0Hz") -> TTSResult:
        import hashlib
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sr = 44100
        dur = 2.0
        t = np.linspace(0, dur, int(dur * sr))
        tone = (np.sin(2 * np.pi * 300 * t) * 16384).astype(np.int16)
        stereo = np.column_stack([tone, tone])

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(stereo.tobytes())

        sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
        narr_sha = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=dur,
            sample_rate=sr,
            backend="test-tts",
            voice=voice or self.default_voice,
            canonical_narration_sha256=narr_sha,
            audio_sha256=sha,
        )


@pytest.fixture
def test_setup(tmp_path: Path):
    from app.db.schema import init_database
    db_path = tmp_path / "test.db"
    init_database(db_path)
    repo = SQLiteRepository(db_path=db_path)
    channel = Channel(
        id="chan-001",
        title="Test Channel",
        handle="@testchannel",
        niche="AI & Tech",
        target_audience="Developers",
    )
    repo.save_channel(channel)

    script = Script(
        id="sc-001",
        title="AI Revolution Video",
        hook="How AI is transforming software development",
        target_platform=PlatformFormat.SHORTS_9_16,
        scenes=[
            Scene(scene_index=0, narration="Welcome to the future of AI and robotics.", hook="AI is here!", visual_prompt="P1"),
            Scene(scene_index=1, narration="Autonomous systems are taking over the world.", hook="Next level!", visual_prompt="P2"),
        ],
        total_word_count=14,
        estimated_duration_seconds=4.0,
    )

    project = VideoProject(
        id="proj-001",
        channel_id="chan-001",
        title="AI Revolution Video",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    return repo, project, tmp_path


def test_kaggle_audio_provider_speech_and_combined_synthesis(tmp_path: Path):
    """Verify KaggleAudioProvider supports FOLEY, VOICE, and COMBINED audio modes."""
    provider = KaggleAudioProvider(config=KaggleAudioConfig(output_dir=tmp_path))
    specs = [
        RealisticFoleySpec(scene_index=0, prompt="tech clicks", kind=AudioKind.FOLEY, duration_seconds=1.5),
        RealisticFoleySpec(scene_index=1, prompt="speech line", kind=AudioKind.VOICE, text_narration="Hello world", duration_seconds=1.5),
        RealisticFoleySpec(scene_index=2, prompt="rain storm", kind=AudioKind.COMBINED, text_narration="It was a stormy night", duration_seconds=1.5),
    ]

    out_dir = tmp_path / "audio_tests"
    results = provider.synthesize_realistic_foley(specs, out_dir)
    assert len(results) == 3
    for f_path, sha in results:
        assert Path(f_path).exists()
        assert len(sha) == 64
        with wave.open(f_path, "rb") as wf:
            assert wf.getnchannels() == 2
            assert wf.getframerate() == 44100


def test_pipeline_supports_audio_mode_voice_only(test_setup):
    """Verify production pipeline with VOICE_ONLY audio mode."""
    repo, project, tmp_path = test_setup
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=MockFastTTS(),
        base_output_dir=tmp_path / "out",
    )

    profile = RenderProfile(audio_mode=AudioMode.VOICE_ONLY, fps=30)
    updated_proj, qa_res, manifest = pipeline.run_production(
        project_id=project.id,
        profile=profile,
    )

    assert manifest.audio_mode == "voice_only"
    assert qa_res.passed is True
    assert Path(manifest.final_video_path).exists()


def test_pipeline_supports_audio_mode_sound_only(test_setup):
    """Verify production pipeline with SOUND_ONLY audio mode."""
    repo, project, tmp_path = test_setup
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=MockFastTTS(),
        base_output_dir=tmp_path / "out_sound",
    )

    profile = RenderProfile(audio_mode=AudioMode.SOUND_ONLY, fps=30)
    updated_proj, qa_res, manifest = pipeline.run_production(
        project_id=project.id,
        profile=profile,
    )

    assert manifest.audio_mode == "sound_only"
    assert qa_res.passed is True
    assert Path(manifest.final_video_path).exists()


def test_pipeline_supports_audio_mode_both(test_setup):
    """Verify production pipeline with default BOTH (Voice + Foley + BGM) audio mode."""
    repo, project, tmp_path = test_setup
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=MockFastTTS(),
        base_output_dir=tmp_path / "out_both",
    )

    profile = RenderProfile(audio_mode=AudioMode.BOTH, fps=30)
    updated_proj, qa_res, manifest = pipeline.run_production(
        project_id=project.id,
        profile=profile,
    )

    assert manifest.audio_mode == "both"
    assert qa_res.passed is True
    assert Path(manifest.final_video_path).exists()
