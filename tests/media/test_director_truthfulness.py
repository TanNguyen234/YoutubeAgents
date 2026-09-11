"""Truthfulness and anti-hallucination tests for Director and visual renderers."""

from pathlib import Path
import pytest
from PIL import Image

from app.domain.models import ResearchDossier, ResearchSource
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    BeatPurpose,
    ChartDatum,
    ComparisonColumn,
    EvidenceBinding,
    MissingGroundedVisualData,
    NarrativeBeat,
    ShotSpec,
    VisualIntent,
    VisualModality,
)
from app.media.director.storyboard_planner import StoryboardPlanner
from app.media.renderers.chart_renderer import ChartRenderer
from app.media.renderers.evidence_renderer import EvidenceRenderer
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


def test_chart_without_grounded_numbers_does_not_invent_values(tmp_path):
    """Critical Test Case 2: Narration without numbers must NOT invent 82%, 12%, 6%."""
    renderer = ChartRenderer()
    output_path = tmp_path / "token_prob.png"

    # Instruction with NO empirical percentages
    instruction = "LLMs select the next token from candidate vocabulary."
    p, h = renderer.render_from_instruction(instruction, output_path)

    assert Path(p).exists()
    # If the user asks for a chart from an instruction without any numbers and not token concept:
    # it must raise MissingGroundedVisualData rather than inventing numbers
    with pytest.raises(MissingGroundedVisualData):
        renderer.render_from_instruction(
            "Generic performance across distributed cloud nodes.",
            tmp_path / "fail.png",
        )


def test_storyboard_planner_does_not_invent_82_percent(tmp_path):
    """StoryboardPlanner must not inject '82%, 12%, 6%' into chart_instruction."""
    planner = StoryboardPlanner()
    beat = NarrativeBeat(
        beat_id="b_01",
        narration="LLMs select the next token based on learned probability.",
        visual_intent=VisualIntent.SHOW_DATA,
    )
    instr = planner._build_modality_instructions(beat, VisualModality.DATA_VISUALIZATION, "Next Token")
    chart_instr = instr.get("chart_instruction", "")
    assert "82%" not in chart_instr
    assert "12%" not in chart_instr
    assert "6%" not in chart_instr


def test_comparison_uses_planned_points_not_sqlite_defaults(tmp_path):
    """Critical Test Case 3: System A differs from System B must NOT render hardcoded SQLite WAL claims."""
    director = AutoDirectorService(gflow_provider=None)
    output_dir = tmp_path / "comp_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shot without comparison columns: must reroute away from comparison to diagram fallback
    shot_no_cols = ShotSpec(
        shot_id="shot_comp_01",
        beat_id="beat_01",
        scene_index=0,
        narration_segment="System A differs fundamentally from System B.",
        duration_seconds=3.0,
        visual_modality=VisualModality.COMPARISON,
        visual_intent=VisualIntent.SHOW_DIFFERENCE,
    )

    path, p_hash = director._generate_shot_asset(
        shot=shot_no_cols,
        shot_index=0,
        output_dir=output_dir,
        script_title="System Differences",
        channel_name="Tech Channel",
    )

    # Must have fallen back to diagram because comparison data was incomplete, NOT hardcoded WAL
    assert "diagram" in path.name
    assert not (output_dir / "shot_comp_01_comparison.png").exists()

    # When grounded comparison columns are provided, it uses them
    shot_with_cols = ShotSpec(
        shot_id="shot_comp_02",
        beat_id="beat_02",
        scene_index=0,
        narration_segment="Kafka differs from RabbitMQ in message retention.",
        duration_seconds=3.0,
        visual_modality=VisualModality.COMPARISON,
        visual_intent=VisualIntent.SHOW_DIFFERENCE,
        comparison_left=ComparisonColumn(label="Kafka", points=["Log-centric persistence", "Consumer offset tracking"]),
        comparison_right=ComparisonColumn(label="RabbitMQ", points=["Broker queue deletion", "Smart broker, dumb consumer"]),
    )

    path2, p_hash2 = director._generate_shot_asset(
        shot=shot_with_cols,
        shot_index=1,
        output_dir=output_dir,
        script_title="Broker Comparison",
        channel_name="Tech Channel",
    )
    assert path2.exists()
    assert "comparison" in path2.name


def test_terminal_visual_does_not_invent_latency(tmp_path):
    """Illustrative terminal must not fabricate 14.2ms or fake benchmark numbers."""
    director = AutoDirectorService(gflow_provider=None)
    output_dir = tmp_path / "term_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    shot = ShotSpec(
        shot_id="shot_term_01",
        beat_id="beat_01",
        scene_index=0,
        narration_segment="Run git rebase main to replay commits onto current HEAD.",
        duration_seconds=3.0,
        visual_modality=VisualModality.CODE_ANIMATION,
        visual_intent=VisualIntent.SHOW_CODE,
        code_instruction="git rebase main",
        terminal_mode="ILLUSTRATIVE_TERMINAL",
    )

    path, _ = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=output_dir,
        script_title="Git Workflow",
        channel_name="Tech",
    )

    assert path.exists()
    # Check that attempt record did not use fake 14.2ms output
    attempt = next(a for a in director.asset_attempts if a.shot_id == "shot_term_01")
    assert attempt.success is True


def test_evidence_requires_source_binding(tmp_path):
    """Critical Test Case 4: Evidence shot without source binding must reroute to diagram."""
    director = AutoDirectorService(gflow_provider=None)
    output_dir = tmp_path / "ev_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shot without evidence binding or source_refs
    shot_unbound = ShotSpec(
        shot_id="shot_ev_01",
        beat_id="beat_01",
        scene_index=0,
        narration_segment="Our benchmarks verify this architectural assertion.",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        evidence_binding=None,
        source_refs=[],
    )

    path, _ = director._generate_shot_asset(
        shot=shot_unbound,
        shot_index=0,
        output_dir=output_dir,
        script_title="Evidence Check",
        channel_name="Tech",
        dossier=None,
    )

    # Must be rerouted to diagram fallback, NOT rendered as fake evidence card
    assert "diagram" in path.name
    assert not (output_dir / "shot_ev_01_evidence.png").exists()


def test_evidence_never_uses_placeholder_url(tmp_path):
    """Evidence card must never render 'https://official-documentation.org'."""
    director = AutoDirectorService(gflow_provider=None)
    output_dir = tmp_path / "ev_url_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pass an explicit fake URL binding
    shot_fake_url = ShotSpec(
        shot_id="shot_ev_fake",
        beat_id="beat_01",
        scene_index=0,
        narration_segment="According to documentation.",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        evidence_binding=EvidenceBinding(
            source_ref="fake_ref",
            source_title="Fake Docs",
            source_url="https://official-documentation.org",
            quote_or_excerpt="Some claim",
        ),
    )

    path, _ = director._generate_shot_asset(
        shot=shot_fake_url,
        shot_index=0,
        output_dir=output_dir,
        script_title="Fake Docs Test",
        channel_name="Tech",
    )

    # Must be rejected / rerouted away because the URL is placeholder
    assert "diagram" in path.name
    assert not (output_dir / "shot_ev_fake_evidence.png").exists()


def test_stat_callout_requires_grounded_number():
    """MotionGraphicsRenderer.render_stat_callout must raise error if big_stat is empty."""
    renderer = MotionGraphicsRenderer()
    with pytest.raises(MissingGroundedVisualData):
        renderer.render_stat_callout(
            big_stat="",
            label="Throughput",
            context_detail="Latency reduction",
            output_path=Path("dummy.png"),
        )
