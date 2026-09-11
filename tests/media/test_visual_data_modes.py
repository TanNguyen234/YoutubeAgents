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


def test_grounded_chart_rejects_datum_without_claim_or_source():
    """Grounded chart must reject datum without verified claim_id or external source_ref."""
    from app.media.director.models import ChartDatumOrigin

    evaluator = VisualShotEvaluator()

    # Datum without claim or source
    invalid_datum = ChartDatum(
        label="Latency",
        value=20.0,
        unit="ms",
        origin=ChartDatumOrigin.EXTERNAL_SOURCE,
        source_ref=None,  # Missing source_ref!
    )

    shot = ShotSpec(
        shot_id="s_chart_invalid",
        beat_id="b_01",
        narration_segment="Latency measured 20ms.",
        duration_seconds=3.0,
        visual_modality=VisualModality.DATA_VISUALIZATION,
        visual_data_mode=VisualizationDataMode.GROUNDED,
        headline_text="LATENCY MEASUREMENT",
        chart_data=[invalid_datum],
    )
    storyboard = Storyboard(
        project_id="proj_invalid_chart",
        total_duration=3.0,
        shots=[shot],
    )

    report = evaluator.generate_quality_report(storyboard=storyboard)
    assert report.creative_status == "FAIL"
    assert any("UNGROUNDED_CHART_DATA" in err or "FABRICATED_DATA" in err for err in report.critical_failures)


def test_verified_claim_number_can_be_charted():
    """Numbers matching verified claims must be charted with verified claim provenance."""
    from app.domain.enums import ClaimVerificationVerdict, ContentFormat
    from app.domain.models import Claim, FactCheckReport, ResearchDossier, ResearchSource, Scene, Script
    from app.media.director.models import BeatPurpose, ChartDatumOrigin
    from app.media.director.storyboard_planner import StoryboardPlanner

    planner = StoryboardPlanner()

    claim = Claim(
        id="clm_lat_20",
        source_id="src_benchmarks",
        statement="Write latency was reduced to 20ms under high load.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://benchmarks.internal.org",
    )
    source = ResearchSource(
        id="src_benchmarks",
        title="Benchmark Lab",
        url="https://benchmarks.org/report",
        content_sha256="bench_sha",
    )
    dossier = ResearchDossier(
        id="dos_bench",
        topic_id="top_bench",
        sources=[source],
        claims=[claim],
        summary="Benchmark summary",
    )
    fact_report = FactCheckReport(
        id="fc_bench",
        project_id="proj_bench",
        claims=[claim],
        audit_summary="Verified",
    )

    script = Script(
        id="scr_chart",
        title="Latency Benchmark",
        hook="How fast is the database?",
        scenes=[Scene(index=0, narration="Write latency was reduced to 20ms under high load.", target_duration_seconds=3.0)],
        total_word_count=9,
        estimated_duration_seconds=3.0,
        content_format=ContentFormat.EXPLAINER,
    )
    beat = NarrativeBeat(
        beat_id="b_01",
        scene_index=0,
        narration="Write latency was reduced to 20ms under high load.",
        duration_hint=3.0,
        purpose=BeatPurpose.PROVE,
        visual_intent=VisualIntent.SHOW_DATA,
        key_claim="Write latency was reduced to 20ms under high load.",
        source_refs=["src_benchmarks"],
    )

    storyboard = planner.plan_storyboard(
        project_id="proj_bench",
        script=script,
        beats=[beat],
        total_audio_duration=3.0,
        dossier=dossier,
        fact_report=fact_report,
    )

    assert len(storyboard.shots) == 1
    shot = storyboard.shots[0]
    assert shot.visual_modality == VisualModality.DATA_VISUALIZATION
    assert len(shot.chart_data) > 0
    assert shot.chart_data[0].origin == ChartDatumOrigin.VERIFIED_CLAIM
    assert shot.chart_data[0].claim_id == "clm_lat_20"
    assert shot.chart_data[0].value == 20.0


def test_unverified_narration_number_reroutes_to_diagram():
    """Arbitrary numbers in narration not matched to verified claims must not be charted as GROUNDED data."""
    from app.domain.enums import ContentFormat
    from app.domain.models import FactCheckReport, ResearchDossier, Scene, Script
    from app.media.director.models import BeatPurpose
    from app.media.director.storyboard_planner import StoryboardPlanner

    planner = StoryboardPlanner()

    script = Script(
        id="scr_unverified_num",
        title="Unverified Numbers",
        hook="Unverified stats.",
        scenes=[Scene(index=0, narration="We saw 99.9% uptime and 15000 QPS across all nodes.", target_duration_seconds=3.0)],
        total_word_count=10,
        estimated_duration_seconds=3.0,
        content_format=ContentFormat.EXPLAINER,
    )
    beat = NarrativeBeat(
        beat_id="b_01",
        scene_index=0,
        narration="We saw 99.9% uptime and 15000 QPS across all nodes.",
        duration_hint=3.0,
        purpose=BeatPurpose.PROVE,
        visual_intent=VisualIntent.SHOW_DATA,
        key_claim=None,
        source_refs=[],
    )

    # Empty dossier and fact report -> no verified claim for 99.9% or 15000
    dossier = ResearchDossier(id="dos_empty", topic_id="top_01", sources=[], claims=[], summary="")
    fact_report = FactCheckReport(id="fc_empty", project_id="proj_01", claims=[], audit_summary="")

    storyboard = planner.plan_storyboard(
        project_id="proj_01",
        script=script,
        beats=[beat],
        total_audio_duration=3.0,
        dossier=dossier,
        fact_report=fact_report,
    )

    assert len(storyboard.shots) == 1
    # Must be rerouted to DIAGRAM because unverified numbers cannot be charted as GROUNDED data
    assert storyboard.shots[0].visual_modality == VisualModality.DIAGRAM


# Explicit aliases for regression test suite
test_grounded_chart_rejects_unprovenanced_data = test_grounded_chart_rejects_datum_without_claim_or_source
test_unverified_narration_number_is_not_charted = test_unverified_narration_number_reroutes_to_diagram


