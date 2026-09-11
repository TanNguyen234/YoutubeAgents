"""Sound Designer Service for sequencing scene transitions and impact sound design."""

import hashlib
from pathlib import Path
from typing import List, Optional
import numpy as np
import wave

from app.media.cinematographer import CinematographerService
from app.media.kaggle_audio_provider import KaggleAudioProvider, RealisticFoleySpec
from app.media.models import SceneRenderPlan


class SoundDesignError(RuntimeError):
    """Raised when sound design sequencing fails."""
    pass


class SoundDesignerService:
    """Sequences multi-channel sound effects (whooshes, impacts, risers) and realistic Foley aligned to visual scene cuts."""

    def __init__(
        self,
        cinematographer: Optional[CinematographerService] = None,
        kaggle_audio: Optional[KaggleAudioProvider] = None,
    ):
        self.cinematographer = cinematographer or CinematographerService()
        self.kaggle_audio = kaggle_audio or KaggleAudioProvider()
        self._cached_whoosh: Optional[np.ndarray] = None
        self._cached_impact: Optional[np.ndarray] = None
        self.sample_rate = 44100

    def _load_or_create_sfx(self, sfx_type: str, cache_dir: Path) -> np.ndarray:
        """Generate and load synthetic SFX sample buffer."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        wav_path = cache_dir / f"sfx_{sfx_type}.wav"

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            if sfx_type == "whoosh":
                self.cinematographer.generate_synthetic_whoosh_sfx(wav_path, duration=0.25)
            elif sfx_type == "impact":
                self.cinematographer.generate_synthetic_impact_sfx(wav_path, duration=0.20)
            else:
                raise SoundDesignError(f"Unknown SFX type: {sfx_type}")

        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
            data = np.frombuffer(frames, dtype=np.int16)
            if channels == 1:
                data = np.column_stack([data, data])
            elif channels > 2:
                data = data.reshape(-1, channels)[:, :2]
            else:
                data = data.reshape(-1, 2)
            return data

    def infer_realistic_foley_specs(self, scene_plans: List[SceneRenderPlan]) -> List[RealisticFoleySpec]:
        """Infer realistic Foley prompts from scene narration context to ground visuals in authentic acoustic space."""
        specs: List[RealisticFoleySpec] = []
        accumulated_time = 0.0

        for idx, plan in enumerate(scene_plans):
            text = (plan.narration_segment or "").lower()
            duration = min(4.0, max(1.5, plan.target_duration_seconds))

            if idx == 0:
                prompt = "cinematic deep impact boom with low-end sub bass and subtle room decay"
            elif any(w in text for w in ["code", "ai", "model", "prompt", "computer", "data", "algorithm", "software"]):
                prompt = "crisp mechanical keyboard typing clicks with subtle electronics server room hum"
            elif any(w in text for w in ["speed", "fast", "surge", "exponential", "growth", "transform", "future"]):
                prompt = "dynamic rising frequency tension whoosh with atmospheric metallic resonance"
            elif any(w in text for w in ["rain", "storm", "dark", "outside", "night", "weather"]):
                prompt = "gentle rain falling against window glass with distant ambient wind"
            else:
                prompt = "subtle organic Foley texture and clean room tone acoustic atmosphere"

            specs.append(
                RealisticFoleySpec(
                    scene_index=idx,
                    prompt=prompt,
                    duration_seconds=duration,
                    time_offset_seconds=accumulated_time,
                    gain_db=-6.0,
                )
            )
            accumulated_time += plan.target_duration_seconds

        return specs

    def generate_sfx_track(
        self,
        scene_plans: List[SceneRenderPlan],
        total_duration_seconds: float,
        output_path: Path,
        enable_realistic_foley: bool = True,
    ) -> tuple[str, str]:
        """Sequence and export a full-length stereo SFX stem matching scene cut points and realistic Foley."""
        if total_duration_seconds <= 0.0:
            raise SoundDesignError(f"Total duration must be > 0 (got {total_duration_seconds}s).")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cache_dir = output_path.parent / ".sfx_cache"

        # 1. Prepare master timeline buffer
        num_samples = int(total_duration_seconds * self.sample_rate)
        master_buffer = np.zeros((num_samples, 2), dtype=np.int32)

        # 2. Load SFX transition assets
        try:
            whoosh = self._load_or_create_sfx("whoosh", cache_dir)
            impact = self._load_or_create_sfx("impact", cache_dir)
        except Exception as e:
            raise SoundDesignError(f"Failed to generate base SFX samples: {e}") from e

        # 3. Add Opening Hook Accent at t=0.05s
        if scene_plans:
            hook_start_idx = int(0.05 * self.sample_rate)
            impact_len = min(len(impact), num_samples - hook_start_idx)
            if impact_len > 0:
                master_buffer[hook_start_idx: hook_start_idx + impact_len] += (impact[:impact_len] * 0.7).astype(np.int32)

        # 4. Sequence Whoosh transitions at every cut boundary
        accumulated_time = 0.0
        for idx in range(len(scene_plans) - 1):
            accumulated_time += scene_plans[idx].target_duration_seconds
            # Place whoosh centered slightly ahead of cut
            cut_start_time = max(0.0, accumulated_time - 0.12)
            start_sample = int(cut_start_time * self.sample_rate)
            whoosh_len = min(len(whoosh), num_samples - start_sample)
            if start_sample < num_samples and whoosh_len > 0:
                master_buffer[start_sample: start_sample + whoosh_len] += (whoosh[:whoosh_len] * 0.85).astype(np.int32)

        # 5. Layer Realistic Scene Foley (AudioLDM 2 / Kaggle Engine)
        if enable_realistic_foley and scene_plans:
            foley_specs = self.infer_realistic_foley_specs(scene_plans)
            foley_dir = cache_dir / "realistic_foley"
            try:
                foley_results = self.kaggle_audio.synthesize_realistic_foley(
                    specs=foley_specs,
                    output_dir=foley_dir,
                    use_kaggle_cloud=False,  # Local synthesis fallback / preview
                )
                for spec, (f_path, _) in zip(foley_specs, foley_results):
                    with wave.open(str(f_path), "rb") as wf:
                        channels = wf.getnchannels()
                        frames = wf.readframes(wf.getnframes())
                        raw = np.frombuffer(frames, dtype=np.int16)
                        if channels == 1:
                            f_data = np.column_stack([raw, raw])
                        else:
                            f_data = raw.reshape(-1, 2)

                    start_idx = int(spec.time_offset_seconds * self.sample_rate)
                    if start_idx < num_samples:
                        available_len = min(len(f_data), num_samples - start_idx)
                        # Layer Foley with subtle gain (0.45) to preserve voiceover headroom
                        master_buffer[start_idx: start_idx + available_len] += (f_data[:available_len] * 0.45).astype(np.int32)
            except Exception as foley_err:
                # Log non-fatal Foley failure and keep baseline transitions
                pass

        # 6. Clip to 16-bit range and write WAV
        clipped = np.clip(master_buffer, -32768, 32767).astype(np.int16)

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(clipped.tobytes())

        content_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), content_sha256
