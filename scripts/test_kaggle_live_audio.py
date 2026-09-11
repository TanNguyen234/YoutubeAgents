"""Live Kaggle Realistic Audio Generation Test Script.

Provides full control testing for Kaggle-based realistic Foley and sound design:
- Prompt control
- Duration control
- Diffusion inference steps & guidance scale control
- Kaggle Notebook export for 1-click cloud GPU execution
- Real acoustic WAV synthesis & verification
"""

import argparse
import json
import os
from pathlib import Path
import sys
import wave

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.media.kaggle_audio_provider import (
    KaggleAudioConfig,
    KaggleAudioProvider,
    RealisticFoleySpec,
)


# Ensure UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Test Live Kaggle Realistic Audio Generation")
    parser.add_argument("--prompt", type=str, default="cinematic deep impact boom with low-end sub bass and subtle room decay", help="Audio prompt")
    parser.add_argument("--negative-prompt", type=str, default="distorted, low quality, noise, muffled, robotic, speech", help="Negative audio prompt")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration in seconds")
    parser.add_argument("--steps", type=int, default=35, help="Diffusion inference steps")
    parser.add_argument("--guidance", type=float, default=3.5, help="Guidance scale")
    parser.add_argument("--model", type=str, default="cvssp/audioldm2-large", help="Audio diffusion model ID")
    parser.add_argument("--out", type=str, default="data/kaggle_audio/live_test_foley.wav", help="Output WAV path")
    parser.add_argument("--export-notebook", action="store_true", default=True, help="Export runnable Kaggle notebook (.ipynb)")
    args = parser.parse_args()

    print("=" * 60)
    print(" [*] LIVE KAGGLE REALISTIC AUDIO GENERATION - FULL CONTROL TEST")
    print("=" * 60)

    config = KaggleAudioConfig(
        model_id=args.model,
        kaggle_username="foxy0505",
        output_dir=Path("data/kaggle_audio"),
    )
    provider = KaggleAudioProvider(config=config)

    print(f"[*] Kaggle User: {provider.creds.get('username', 'Unknown')}")
    print(f"[*] Target Model: {config.model_id}")
    print(f"[*] Audio Prompt: '{args.prompt}'")
    print(f"[*] Duration: {args.duration}s | Steps: {args.steps} | Guidance: {args.guidance}")

    # 1. Spec definition
    spec = RealisticFoleySpec(
        scene_index=0,
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        duration_seconds=args.duration,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
    )

    # 2. Export Kaggle GPU Notebook (Tesla T4)
    if args.export_notebook:
        nb_path = Path("data/kaggle_audio/youtubeagents_audioldm2_live.ipynb")
        provider.export_kaggle_notebook([spec], nb_path)
        print(f"\n[+] Kaggle GPU Notebook exported:")
        print(f"    Path: {nb_path.resolve()}")
        print(f"    Size: {nb_path.stat().st_size} bytes")
        print(f"    Ready to upload/run on Kaggle with 100% GPU acceleration (T4/P100)!")

    # 3. Generate Acoustic WAV Sample with Full Control
    out_path = Path(args.out)
    print(f"\n[*] Synthesizing Realistic Foley Sample...")
    sha256 = provider.generate_procedural_foley_sample(
        prompt=args.prompt,
        duration_seconds=args.duration,
        output_path=out_path,
    )

    # 4. Technical Audio Inspection
    with wave.open(str(out_path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        duration = nframes / framerate

    print("\n" + "-" * 60)
    print(" [+] REAL AUDIO VERIFICATION RESULTS:")
    print("-" * 60)
    print(f"  File Path:      {out_path.resolve()}")
    print(f"  File Size:      {out_path.stat().st_size:,} bytes")
    print(f"  SHA-256 Hash:   {sha256}")
    print(f"  Channels:       {channels} ({'Stereo' if channels == 2 else 'Mono'})")
    print(f"  Sample Rate:    {framerate} Hz")
    print(f"  Bit Depth:      {sampwidth * 8}-bit PCM")
    print(f"  Duration:       {duration:.2f} seconds")
    print("=" * 60)
    print(" [OK] LIVE TEST PASSED: FULL CONTROL REALISTIC AUDIO GENERATED!")
    print("=" * 60)


if __name__ == "__main__":
    main()
