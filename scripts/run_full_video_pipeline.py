"""CLI Runner for End-to-End Multimodal Video Generation Pipeline.

Supports:
- Audio Modes: both (Voice + Foley + BGM), voice_only, sound_only
- Kaggle Realistic Foley & Voice Integration
- GFlow / Veo AI Video Generation Integration
- Authoritative FFmpeg 3-stem composition & EBU R128 QA Inspection
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Safe Windows stdout encoding
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.db.repository import SQLiteRepository
from app.domain.enums import PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.gflow_provider import GFlowMediaProvider
from app.media.models import AudioMode, RenderProfile
from app.media.pipeline import MediaProductionPipeline


def create_demo_project(repo: SQLiteRepository, project_id: str, title: str) -> VideoProject:
    """Create and persist a verified demo video project."""
    channel_id = "chan-tech-pulse"
    if not repo.get_channel(channel_id):
        repo.save_channel(
            Channel(
                id=channel_id,
                title="Tech Pulse 24",
                handle="@TechPulse24",
                niche="AI & Tech",
                target_audience="Tech Enthusiasts",
            )
        )

    scenes = [
        Scene(
            index=0,
            narration="Artificial Intelligence has crossed the point of no return.",
            hook="The AI Revolution is Here!",
            visual_prompt="Cinematic neon robotic eye focusing, macro lens, dramatic blue lighting, 8k vertical video",
        ),
        Scene(
            index=1,
            narration="Autonomous coding agents now build full applications in seconds.",
            hook="Agents write the future!",
            visual_prompt="Digital code streams flowing through server racks, glowing holographic data matrix, high-speed camera push-in",
        ),
        Scene(
            index=2,
            narration="Are you ready for the next wave of human-machine intelligence?",
            hook="Join the Future!",
            visual_prompt="Human and humanoid robot shaking hands against sunset metropolis skyline, golden hour, cinematic bokeh",
        ),
    ]

    script = Script(
        id=f"sc-{project_id}",
        title=title,
        hook="The AI Revolution is Here!",
        target_platform=PlatformFormat.SHORTS_9_16,
        scenes=scenes,
        total_word_count=sum(len(s.narration.split()) for s in scenes),
        estimated_duration_seconds=12.0,
    )

    project = VideoProject(
        id=project_id,
        channel_id=channel_id,
        title=title,
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    return repo.get_video_project(project.id)


def main():
    parser = argparse.ArgumentParser(description="Run Full Multimodal Video Generation Pipeline")
    parser.add_argument("--project-id", type=str, default="proj-ai-revolution-01", help="Project ID to render")
    parser.add_argument("--title", type=str, default="The Rise of Autonomous AI", help="Video Title")
    parser.add_argument("--audio-mode", type=str, default="both", choices=["both", "voice_only", "sound_only"], help="Audio composition mode")
    parser.add_argument("--voice", type=str, default="en-US-GuyNeural", help="TTS Voice ID")
    parser.add_argument("--enable-gflow", action="store_true", default=False, help="Enable GFlow Veo video generation")
    parser.add_argument("--out-dir", type=str, default="output/pipeline_demo", help="Base output directory")
    args = parser.parse_args()

    print("=" * 65)
    print(" [*] FULL MULTIMODAL VIDEO GENERATION PIPELINE")
    print("=" * 65)
    print(f"[*] Project ID:   {args.project_id}")
    print(f"[*] Title:        {args.title}")
    print(f"[*] Audio Mode:   {args.audio_mode.upper()}")
    print(f"[*] Voice:        {args.voice}")
    print(f"[*] Output Dir:   {Path(args.out_dir).resolve()}")

    db_path = Path("data/youtube_agents.db")
    repo = SQLiteRepository(db_path=db_path)

    project = repo.get_video_project(args.project_id)
    if not project or not project.script:
        print(f"\n[*] Creating new demo project: '{args.project_id}'...")
        project = create_demo_project(repo, args.project_id, args.title)
    elif project.state != VideoLifecycleState.VERIFIED:
        # Reset to VERIFIED in database if previous run reached RENDERED / QA_PASSED
        conn = repo._get_connection()
        with conn:
            conn.execute("UPDATE video_projects SET state = ? WHERE id = ?", (VideoLifecycleState.VERIFIED.value, project.id))
        project = repo.get_video_project(args.project_id)

    gflow_provider = GFlowMediaProvider() if args.enable_gflow else None

    pipeline = MediaProductionPipeline(
        repository=repo,
        gflow_provider=gflow_provider,
        base_output_dir=Path(args.out_dir),
    )

    audio_mode_enum = AudioMode(args.audio_mode)
    profile = RenderProfile(
        audio_mode=audio_mode_enum,
        width=1080,
        height=1920,
        fps=30,
        target_loudness_lufs=-14.0,
    )

    print(f"\n[*] Executing end-to-end media production...")
    updated_project, qa_res, manifest = pipeline.run_production(
        project_id=project.id,
        profile=profile,
        voice=args.voice,
        force_rebuild=True,
    )

    print("\n" + "=" * 65)
    print(" [+] VIDEO PRODUCTION & TECHNICAL QA REPORT:")
    print("=" * 65)
    print(f"  Final Video Path:    {Path(manifest.final_video_path).resolve()}")
    print(f"  Final Video Size:    {manifest.final_video_size_bytes:,} bytes")
    print(f"  Video Duration:      {manifest.video_duration:.2f} seconds")
    print(f"  Resolution:          {manifest.resolution} (Vertical 9:16 Shorts)")
    print(f"  Frame Rate:          {manifest.fps} fps ({manifest.video_codec})")
    print(f"  Audio Mode:          {manifest.audio_mode.upper()}")
    print(f"  Measured Loudness:   {manifest.measured_loudness_lufs:.2f} LUFS (Target: -14.0 LUFS)")
    print(f"  Technical QA Verdict: {manifest.qa_verdict}")
    print(f"  Project Final State: {updated_project.state.value}")
    print("=" * 65)
    print(" [OK] PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    main()
