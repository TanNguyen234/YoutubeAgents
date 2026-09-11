"""Representative Quality Fixtures (Requirement 32):
Verifies the system plans rich multi-shot storyboards with semantic modalities (NOT 1 scene = 1 slide).
"""

from pathlib import Path
import pytest

from app.domain.models import Scene, Script
from app.media.director.beat_decomposer import BeatDecomposer
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import ContentFormat, VisualModality
from app.media.director.storyboard_planner import StoryboardPlanner


def test_fixture_example_a_llm_token_prediction(tmp_path: Path):
    """Example A: LLM token prediction.

    Narration concept: LLMs predict the next token from a probability distribution.
    Desired modalities: UI token visualization, probability chart, selection animation.
    Must decompose 1 scene into multiple fine-grained shots and choose semantic modalities.
    """
    script = Script(
        id="scr-test-llm",
        title="How LLMs Predict the Next Token",
        hook="How does an AI model actually write words?",
        scenes=[
            Scene(
                index=0,
                narration=(
                    "Large language models do not understand language like humans do. "
                    "Instead, they predict the next token from a probability distribution over their entire vocabulary. "
                    "When given 'The capital of France is', the model computes likelihoods, picks Paris at 82%, and appends it."
                ),
                target_duration_seconds=9.0,
            )
        ],
        total_word_count=45,
        estimated_duration_seconds=9.0,
    )

    director = AutoDirectorService()
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="test_llm_proj",
        script=script,
        channel_name="AI Explained",
        total_audio_duration=9.0,
        output_dir=tmp_path / "out_llm",
        content_format=ContentFormat.EXPLAINER,
    )

    # 1. Must produce MULTIPLE shots from this single scene (at least 3 shots)
    assert len(storyboard.shots) >= 3, f"Expected >= 3 shots, got {len(storyboard.shots)}"
    assert len(timeline.shots) >= 3

    # 2. Modality diversity: Must NOT be 100% static cards
    used_modalities = {s.visual_modality for s in storyboard.shots}
    assert VisualModality.STATIC_CARD not in used_modalities or len(used_modalities) > 1

    # 3. Should contain DATA_VISUALIZATION or DIAGRAM or UI_SIMULATION
    semantic_modalities = {
        VisualModality.DATA_VISUALIZATION,
        VisualModality.DIAGRAM,
        VisualModality.UI_SIMULATION,
        VisualModality.MOTION_GRAPHICS,
        VisualModality.CODE_ANIMATION,
    }
    assert bool(used_modalities.intersection(semantic_modalities)), f"Expected semantic modalities, got {used_modalities}"

    # 4. Average shot duration should be between 1.2s and 4.5s
    avg_dur = sum(s.duration_seconds for s in storyboard.shots) / len(storyboard.shots)
    assert 1.2 <= avg_dur <= 4.5, f"Average shot length was {avg_dur:.2f}s"


def test_fixture_example_b_docker_vs_virtual_machine(tmp_path: Path):
    """Example B: Docker vs Virtual Machines.

    Narration concept: Docker containers share the host kernel while virtual machines run a full guest OS.
    Desired modalities: Architecture diagram, resource comparison, process/kernel visualization.
    """
    script = Script(
        id="scr-test-docker",
        title="Docker vs Virtual Machines",
        hook="Why are Docker containers so lightweight?",
        scenes=[
            Scene(
                index=0,
                narration=(
                    "Why are Docker containers so much faster than virtual machines? "
                    "A virtual machine packages an entire guest operating system and virtualized kernel on top of a hypervisor. "
                    "Docker containers simply isolate processes on the host Linux kernel using cgroups and namespaces."
                ),
                target_duration_seconds=10.0,
            )
        ],
        total_word_count=48,
        estimated_duration_seconds=10.0,
    )

    director = AutoDirectorService()
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="test_docker_proj",
        script=script,
        channel_name="DevOps Architecture",
        total_audio_duration=10.0,
        output_dir=tmp_path / "out_docker",
        content_format=ContentFormat.COMPARISON,
    )

    assert len(storyboard.shots) >= 3
    used_modalities = {s.visual_modality for s in storyboard.shots}
    assert any(
        m in used_modalities
        for m in (VisualModality.DIAGRAM, VisualModality.COMPARISON, VisualModality.MOTION_GRAPHICS)
    ), f"Expected DIAGRAM or COMPARISON in Docker video, got {used_modalities}"


def test_fixture_example_c_nvidia_ai_revenue(tmp_path: Path):
    """Example C: NVIDIA AI Revenue explosion.

    Narration concept: NVIDIA's revenue exploded as AI companies rushed to buy GPU compute.
    Desired modalities: Contextual establishing visual, animated revenue chart, GPU hardware / datacenter.
    """
    script = Script(
        id="scr-test-nvidia",
        title="NVIDIA Revenue Surge",
        hook="How did NVIDIA conquer the entire AI industry?",
        scenes=[
            Scene(
                index=0,
                narration=(
                    "NVIDIA's revenue exploded as AI companies rushed to buy GPU compute. "
                    "Data center sales skyrocketed over four hundred percent in a single year as every tech giant scrambled for H100 clusters."
                ),
                target_duration_seconds=8.0,
            )
        ],
        total_word_count=36,
        estimated_duration_seconds=8.0,
    )

    director = AutoDirectorService()
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="test_nvidia_proj",
        script=script,
        channel_name="Tech Financials",
        total_audio_duration=8.0,
        output_dir=tmp_path / "out_nvidia",
        content_format=ContentFormat.CASE_STUDY,
    )

    assert len(storyboard.shots) >= 2
    used_modalities = {s.visual_modality for s in storyboard.shots}
    assert any(
        m in used_modalities
        for m in (
            VisualModality.DATA_VISUALIZATION,
            VisualModality.DOCUMENT_EVIDENCE,
            VisualModality.MOTION_GRAPHICS,
            VisualModality.DIAGRAM,
        )
    ), f"Expected chart or evidence in revenue video, got {used_modalities}"
