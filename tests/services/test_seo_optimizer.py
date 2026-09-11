"""Tests for SEOOptimizerService."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import PlatformFormat, TitleVariantType, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.services.seo_optimizer import SEOOptimizerService


@pytest.fixture
def seo_service(tmp_path: Path) -> SEOOptimizerService:
    db_file = tmp_path / "test_seo.db"
    repo = SQLiteRepository(db_file)
    channel = Channel(
        id="chan-01",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    return SEOOptimizerService(repository=repo)


def test_generate_seo_package(seo_service: SEOOptimizerService):
    script = Script(
        id="scr-01",
        title="Mixture of Experts Architecture",
        hook="Why are trillion-parameter models running on single GPUs?",
        scenes=[
            Scene(index=0, hook="The Dense Bottleneck", narration="Dense LLMs hit a compute wall.", target_duration_seconds=12.0),
            Scene(index=1, hook="Sparse Routing Secret", narration="MoE only activates two experts per token.", target_duration_seconds=18.0),
            Scene(index=2, hook="Production Benchmark", narration="Inference latency dropped by 70 percent.", target_duration_seconds=15.0),
        ],
        total_word_count=85,
        estimated_duration_seconds=45.0,
    )
    project = VideoProject(
        id="proj-seo-test",
        channel_id="chan-01",
        title="Mixture of Experts Architecture",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    seo_service.repository.save_video_project(project)

    package = seo_service.generate_and_save_seo_package(
        project_id="proj-seo-test",
        primary_keyword="Mixture of Experts MoE",
        series_context={"series_title": "Deep Tech 60s", "episode_number": 3},
    )

    assert package is not None
    assert package.primary_keyword == "Mixture of Experts MoE"
    # Verify 3 title variants
    assert len(package.title_variants) == 3
    angles = {v.angle for v in package.title_variants}
    assert TitleVariantType.CURIOSITY_GAP in angles
    assert TitleVariantType.DIRECT_VALUE in angles
    assert TitleVariantType.PROVOCATIVE_QUESTION in angles

    for v in package.title_variants:
        assert len(v.title) <= 100
        assert len(v.predicted_ctr_rationale) > 5

    # Verify chapters
    assert len(package.chapters) == 3
    assert package.chapters[0].timestamp_formatted == "00:00"
    assert package.chapters[1].timestamp_formatted == "00:12"
    assert package.chapters[2].timestamp_formatted == "00:30"

    # Verify tags limit <= 500 chars
    total_tags_len = sum(len(t) for t in package.tags) + len(package.tags)
    assert total_tags_len <= 500

    # Verify description contains chapters and hook
    assert "00:00" in package.description
    assert "@AIEngineeringDaily" in package.description

    # Verify pinned comment
    assert len(package.pinned_comment) > 10
    assert "?" in package.pinned_comment
