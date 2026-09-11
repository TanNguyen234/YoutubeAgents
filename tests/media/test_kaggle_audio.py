"""Unit and integration tests for KaggleAudioProvider and Realistic Sound Design."""

import json
from pathlib import Path
import wave
import pytest
import numpy as np

from app.media.kaggle_audio_provider import (
    KaggleAudioProvider,
    KaggleAudioConfig,
    RealisticFoleySpec,
)
from app.media.models import SceneRenderPlan
from app.media.sound_designer import SoundDesignerService


def test_kaggle_audio_config_and_credentials(tmp_path: Path):
    """Verify KaggleAudioProvider properly loads configuration and handles credentials."""
    config = KaggleAudioConfig(
        model_id="cvssp/audioldm2-large",
        kaggle_username="foxy0505",
        output_dir=tmp_path / "audio_out",
    )
    provider = KaggleAudioProvider(config=config)
    assert provider.config.model_id == "cvssp/audioldm2-large"
    assert provider.config.kaggle_username == "foxy0505"
    assert provider.creds["username"] == "foxy0505"


def test_kaggle_runner_code_generation(tmp_path: Path):
    """Verify runner script generation contains correct parameters for Kaggle GPU execution."""
    provider = KaggleAudioProvider(config=KaggleAudioConfig(output_dir=tmp_path))
    specs = [
        RealisticFoleySpec(
            scene_index=0,
            prompt="cinematic deep impact boom with sub bass",
            duration_seconds=3.0,
            guidance_scale=3.5,
            num_inference_steps=30,
        ),
        RealisticFoleySpec(
            scene_index=1,
            prompt="mechanical keyboard fast typing clicks",
            duration_seconds=4.0,
            guidance_scale=3.0,
            num_inference_steps=25,
        ),
    ]

    code = provider.generate_kaggle_runner_code(specs)
    assert "AudioLDM2Pipeline" in code
    assert "cvssp/audioldm2-large" in code
    assert "cinematic deep impact boom" in code
    assert "mechanical keyboard fast typing clicks" in code
    assert "scipy.io.wavfile.write" in code


def test_kaggle_notebook_export(tmp_path: Path):
    """Verify export of ready-to-run Jupyter notebook for Kaggle Web UI."""
    provider = KaggleAudioProvider(config=KaggleAudioConfig(output_dir=tmp_path))
    specs = [
        RealisticFoleySpec(scene_index=0, prompt="rain falling on window glass", duration_seconds=2.5)
    ]
    nb_path = tmp_path / "test_kaggle_audio.ipynb"
    result_path = provider.export_kaggle_notebook(specs, nb_path)

    assert result_path.exists()
    assert result_path.stat().st_size > 0
    with open(result_path, "r", encoding="utf-8") as f:
        nb_data = json.load(f)
    assert nb_data["nbformat"] == 4
    assert len(nb_data["cells"]) >= 2
    assert "AudioLDM2Pipeline" in "".join(nb_data["cells"][1]["source"])


def test_procedural_realistic_foley_generation(tmp_path: Path):
    """Verify procedural realistic Foley synthesis creates valid 44.1kHz stereo WAV files."""
    provider = KaggleAudioProvider(config=KaggleAudioConfig(output_dir=tmp_path))
    wav_out = tmp_path / "foley_keyboard.wav"
    
    sha256 = provider.generate_procedural_foley_sample(
        prompt="crisp mechanical keyboard typing clicks with subtle server hum",
        duration_seconds=1.5,
        output_path=wav_out,
    )

    assert wav_out.exists()
    assert len(sha256) == 64
    with wave.open(str(wav_out), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 44100
        frames = wf.readframes(wf.getnframes())
        assert len(frames) > 0


def test_sound_designer_realistic_foley_integration(tmp_path: Path):
    """Verify SoundDesignerService layers realistic Foley into the master SFX track."""
    provider = KaggleAudioProvider(config=KaggleAudioConfig(output_dir=tmp_path / "kaggle_audio"))
    designer = SoundDesignerService(kaggle_audio=provider)

    scene_plans = [
        SceneRenderPlan(
            scene_index=0,
            narration_segment="Welcome to the future of AI and autonomous coding systems.",
            target_duration_seconds=3.0,
            visual_asset_path=str(tmp_path / "card_0.png"),
            visual_asset_sha256="0" * 64,
        ),
        SceneRenderPlan(
            scene_index=1,
            narration_segment="Our algorithms process code at lightning fast speed across clusters.",
            target_duration_seconds=3.5,
            visual_asset_path=str(tmp_path / "card_1.png"),
            visual_asset_sha256="1" * 64,
        ),
    ]

    sfx_out = tmp_path / "master_sfx.wav"
    sfx_path, sfx_sha256 = designer.generate_sfx_track(
        scene_plans=scene_plans,
        total_duration_seconds=6.5,
        output_path=sfx_out,
        enable_realistic_foley=True,
    )

    assert Path(sfx_path).exists()
    assert len(sfx_sha256) == 64
    with wave.open(sfx_path, "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 44100
        duration = wf.getnframes() / wf.getframerate()
        assert abs(duration - 6.5) < 0.1
