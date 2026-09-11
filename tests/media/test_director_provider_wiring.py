"""Integration test for canonical GFlow provider wiring and stock video semantics."""

from unittest.mock import MagicMock, patch
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import VideoLifecycleState
from app.domain.models import Channel, VideoProject
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import ShotSpec, VisualIntent, VisualModality
from app.media.gflow_provider import GFlowMediaProvider
from app.media.pipeline import MediaProductionPipeline
from app.media.scene_planner import ScenePlanner
from app.services.pipeline_brain import BrainPipeline


def test_provider_wiring_from_brain_pipeline_to_director(tmp_path):
    """Assert enable_gflow=True propagates the exact GFlowMediaProvider instance into AutoDirectorService."""
    repo = SQLiteRepository(tmp_path / "test_wiring.db")
    channel = Channel(id="c1", title="Tech", handle="@tech", niche="Tech", target_audience="all")
    repo.save_channel(channel)
    repo.save_video_project(
        VideoProject(
            id="wiring-test-01",
            channel_id="c1",
            title="Git Rebase Tutorial",
            topic="Git rebase",
            state=VideoLifecycleState.CREATED,
        )
    )
    mock_gflow_instance = MagicMock(spec=GFlowMediaProvider)

    with patch("app.services.pipeline_brain.GFlowMediaProvider", return_value=mock_gflow_instance):
        with patch.object(MediaProductionPipeline, "run_production") as mock_run_prod:
            def mock_run_prod_side_effect(*args, **kwargs):
                proj = repo.get_video_project("wiring-test-01")
                proj.state = VideoLifecycleState.READY_FOR_REVIEW
                repo.save_video_project(proj)
                return (proj, MagicMock(), MagicMock())

            mock_run_prod.side_effect = mock_run_prod_side_effect

            pipeline = BrainPipeline(repository=repo)
            pipeline.run_stage_1_to_5 = MagicMock(return_value=(MagicMock(), MagicMock()))

            captured_pipeline = None
            orig_init = MediaProductionPipeline.__init__

            def intercepted_init(self, *args, **kwargs):
                nonlocal captured_pipeline
                orig_init(self, *args, **kwargs)
                captured_pipeline = self

            with patch.object(MediaProductionPipeline, "__init__", intercepted_init):
                with patch("app.services.pipeline_brain.SEOOptimizerService"):
                    with patch("app.services.pipeline_brain.ThumbnailDesignerService"):
                        with patch("app.services.pipeline_brain.HumanReviewGateService"):
                            with patch("app.services.pipeline_brain.YouTubePublisherService"):
                                with patch("app.services.pipeline_brain.YouTubeAnalyticsTracker"):
                                    with patch("app.services.pipeline_brain.StrategyFeedbackLoop"):
                                        pipeline.run_full_autonomous_lifecycle(
                                            project_id="wiring-test-01",
                                            channel=channel,
                                            keyword="Git rebase",
                                            seed_urls=["https://example.com/git"],
                                            enable_gflow=True,
                                        )

            assert captured_pipeline is not None, "MediaProductionPipeline was not constructed"
            # Canonical provider check:
            assert captured_pipeline.gflow_provider is mock_gflow_instance
            assert captured_pipeline.planner.gflow_provider is mock_gflow_instance
            assert captured_pipeline.director.gflow_provider is mock_gflow_instance


def test_provider_wiring_defensive_inference_from_scene_planner(tmp_path):
    """Verify that if gflow_provider is provided only on ScenePlanner, MediaProductionPipeline still wires it to AutoDirector."""
    repo = SQLiteRepository(tmp_path / "test_defensive.db")
    mock_gflow = MagicMock(spec=GFlowMediaProvider)
    planner = ScenePlanner(gflow_provider=mock_gflow)

    # Note: caller omitted gflow_provider on MediaProductionPipeline
    pipeline = MediaProductionPipeline(
        repository=repo,
        scene_planner=planner,
    )

    assert pipeline.gflow_provider is mock_gflow
    assert pipeline.director.gflow_provider is mock_gflow


def test_stock_video_semantics_records_explicit_fallback(tmp_path):
    """Verify that STOCK_VIDEO is not silently converted without an explicit fallback record."""
    director = AutoDirectorService(gflow_provider=None)
    output_dir = tmp_path / "stock_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    shot = ShotSpec(
        shot_id="shot_stock_01",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.STOCK_VIDEO,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        duration_seconds=3.0,
        narration_segment="B-roll footage of server racks in a datacenter.",
    )

    path, p_hash = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=output_dir,
        script_title="Server Architecture",
        channel_name="Tech Channel",
    )

    # Check attempt records
    assert len(director.asset_attempts) >= 1
    stock_attempt = next(
        (a for a in director.asset_attempts if a.requested_modality == VisualModality.STOCK_VIDEO.value),
        None,
    )
    assert stock_attempt is not None, "Explicit fallback record for STOCK_VIDEO was not created"
    assert stock_attempt.success is False
    assert stock_attempt.error_type == "UnsupportedModalityError"
    assert "no stock" in stock_attempt.fallback_reason.lower()
    assert stock_attempt.actual_modality in (VisualModality.MOTION_GRAPHICS.value, VisualModality.GENERATED_VIDEO.value)
