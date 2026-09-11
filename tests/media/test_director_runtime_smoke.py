"""Director Runtime Smoke Test (P0-A):
Exercises the real un-mocked path:
Script -> BeatDecomposer -> StoryboardPlanner -> CODE_ANIMATION / UI_SIMULATION -> AutoDirectorService._generate_shot_asset -> Rendered PNG
Fails if NameError (e.g. re not defined), missing renderer, wrong signature, or fallback to ScenePlanner.
"""

from pathlib import Path
import pytest

from app.domain.models import Scene, Script
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import ContentFormat, VisualModality


def test_director_runtime_smoke_code_and_ui_generation(tmp_path: Path):
    """Verify AutoDirectorService generates real terminal / code assets from terminal narration without NameError or crash."""
    script = Script(
        id="sc-smoke-code",
        title="Git Rebase Interactive",
        hook="How does git rebase work?",
        scenes=[
            Scene(
                index=0,
                narration="Run git rebase in the terminal and watch the commit graph change.",
                hook="How does git rebase work?",
                target_duration_seconds=5.0,
            )
        ],
        total_word_count=12,
        estimated_duration_seconds=5.0,
    )

    output_dir = tmp_path / "smoke_out"
    director = AutoDirectorService()

    # 1. Full director execution: Decompose -> Plan -> Render timeline assets
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="smoke_code_proj",
        script=script,
        channel_name="Developer Channel",
        total_audio_duration=5.0,
        output_dir=output_dir,
        content_format=ContentFormat.DEMO,
    )

    # 2. Assert Storyboard produced shots
    assert len(storyboard.shots) >= 1
    assert len(timeline.shots) >= 1

    # 3. Check that CODE_ANIMATION or UI_SIMULATION was routed and rendered
    code_or_ui_shots = [
        s for s in storyboard.shots
        if s.visual_modality in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION, VisualModality.DIAGRAM)
    ]
    assert len(code_or_ui_shots) > 0, f"Expected CODE/UI/DIAGRAM shot, got {[s.visual_modality for s in storyboard.shots]}"

    # 4. Verify physical asset exists on disk and has valid non-zero size
    rendered_shot = timeline.shots[0]
    asset_file = Path(rendered_shot.asset_path)
    assert asset_file.exists(), f"Rendered asset file missing at {asset_file}"
    assert asset_file.stat().st_size > 0, "Rendered asset file is empty"

    # 5. Verify no NameError was swallowed into failed attempts
    for attempt in director.asset_attempts:
        assert attempt.error_type != "NameError", f"NameError detected in asset attempts: {attempt.error_message}"
        if attempt.modality in (VisualModality.CODE_ANIMATION.value, VisualModality.UI_SIMULATION.value):
            assert attempt.success is True, f"Code/UI asset generation failed: {attempt.error_message}"
