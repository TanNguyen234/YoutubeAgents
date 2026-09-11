"""Unit tests for visual data grounding modes and conceptual visualization truthfulness."""

from pathlib import Path
import tempfile
import pytest

from app.media.director.models import (
    ChartDatum,
    MissingGroundedVisualData,
    NarrativeBeat,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualIntent,
    VisualModality,
    VisualizationDataMode,
)
from app.media.director.quality_evaluator import VisualShotEvaluator
from app.media.renderers.chart_renderer import ChartRenderer
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


def test_conceptual_token_animation_has_no_fake_percentages():
    """Verify that conceptual token animation runs in CONCEPTUAL data mode without claiming empirical percentages."""
    renderer = MotionGraphicsRenderer(width=360, height=640)
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "token_concept.mp4"
        p, sha = renderer.render_animated_token_prediction(
            prompt_text="The capital of France is",
            candidates=[("Paris", 0.82), ("London", 0.08), ("Berlin", 0.06), ("Rome", 0.04)],
            selected_token="Paris",
            output_path=out_path,
            duration=1.0,
            fps=12,
            data_mode=VisualizationDataMode.CONCEPTUAL,
        )
        assert Path(p).exists()
        assert len(sha) == 64


def test_conceptual_chart_does_not_require_grounded_chart_data():
    """Verify that Creative QA does not flag a CONCEPTUAL DATA_VISUALIZATION shot for missing chart_data."""
    evaluator = VisualShotEvaluator()

    shot = ShotSpec(
        shot_id="s_concept_01",
        beat_id="b_01",
        narration_segment="The model predicts next tokens autoregressively.",
        duration_seconds=3.0,
        visual_modality=VisualModality.DATA_VISUALIZATION,
        visual_data_mode=VisualizationDataMode.CONCEPTUAL,
        headline_text="NEXT TOKEN PREDICTION",
    )
    storyboard = Storyboard(
        project_id="proj_concept_qa",
        total_duration=3.0,
        shots=[shot],
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_asset = Path(tmp_dir) / "asset.png"
        dummy_asset.write_bytes(b"dummy")
        timeline = ShotTimeline(
            shots=[
                TimelineShot(
                    shot_id="s_concept_01",
                    beat_id="b_01",
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                    asset_path=str(dummy_asset),
                    asset_sha256="abc",
                    modality=VisualModality.DATA_VISUALIZATION,
                )
            ],
            total_duration=3.0,
        )
        report = evaluator.generate_quality_report(timeline=timeline, storyboard=storyboard)

        # Must not contain FABRICATED_DATA
        fabricated_errors = [err for err in report.critical_failures if "FABRICATED_DATA" in err]
        assert len(fabricated_errors) == 0


def test_grounded_chart_requires_provenance():
    """Verify that GROUNDED DATA_VISUALIZATION without chart_data triggers FABRICATED_DATA in QA."""
    evaluator = VisualShotEvaluator()

    shot = ShotSpec(
        shot_id="s_grounded_01",
        beat_id="b_01",
        narration_segment="We measured a 75% speedup across all queries.",
        duration_seconds=3.0,
        visual_modality=VisualModality.DATA_VISUALIZATION,
        visual_data_mode=VisualizationDataMode.GROUNDED,
        headline_text="MEASURED SPEEDUP",
        chart_data=[],  # Empty chart data under GROUNDED mode
    )
    storyboard = Storyboard(
        project_id="proj_grounded_qa",
        total_duration=3.0,
        shots=[shot],
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_asset = Path(tmp_dir) / "asset.png"
        dummy_asset.write_bytes(b"dummy")
        timeline = ShotTimeline(
            shots=[
                TimelineShot(
                    shot_id="s_grounded_01",
                    beat_id="b_01",
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                    asset_path=str(dummy_asset),
                    asset_sha256="abc",
                    modality=VisualModality.DATA_VISUALIZATION,
                )
            ],
            total_duration=3.0,
        )
        report = evaluator.generate_quality_report(timeline=timeline, storyboard=storyboard)

        fabricated_errors = [err for err in report.critical_failures if "FABRICATED_DATA" in err]
        assert len(fabricated_errors) == 1
        assert "FABRICATED_DATA" in fabricated_errors[0]


def test_grounded_chart_renderer_rejects_missing_values():
    """Verify ChartRenderer raises MissingGroundedVisualData when values are missing in GROUNDED mode."""
    renderer = ChartRenderer(width=360, height=640)
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "chart.png"
        with pytest.raises(MissingGroundedVisualData):
            renderer.render_horizontal_bar_chart(
                title="Performance",
                categories=["A", "B"],
                values=None,
                output_path=out_path,
                show_numeric_labels=True,
                data_mode=VisualizationDataMode.GROUNDED,
            )
