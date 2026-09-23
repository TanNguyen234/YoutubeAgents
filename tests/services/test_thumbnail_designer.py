"""Tests for ThumbnailDesignerService."""

import os
from pathlib import Path
import pytest
from PIL import Image

from app.db.repository import SQLiteRepository
from app.domain.enums import PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, VideoProject
from app.services.thumbnail_designer import ThumbnailDesignerService


@pytest.fixture
def thumbnail_service(tmp_path: Path) -> ThumbnailDesignerService:
    db_file = tmp_path / "test_thumb.db"
    repo = SQLiteRepository(db_file)
    channel = Channel(
        id="chan-01",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    output_dir = tmp_path / "thumbnails"
    output_dir.mkdir(parents=True, exist_ok=True)
    return ThumbnailDesignerService(repository=repo, output_dir=output_dir)


def test_design_and_compose_thumbnails(thumbnail_service: ThumbnailDesignerService, tmp_path: Path):
    project = VideoProject(
        id="proj-thumb-test",
        channel_id="chan-01",
        title="Mixture of Experts",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
    )
    thumbnail_service.repository.save_video_project(project)

    # Create a dummy background image to simulate a GFlow AI visual
    bg_path = tmp_path / "bg_visual.png"
    img = Image.new("RGB", (1080, 1920), color=(20, 30, 60))
    img.save(bg_path)

    pkg = thumbnail_service.create_thumbnail_package(
        project_id="proj-thumb-test",
        headline_text="80% CHEAPER AI",
        background_image_path=str(bg_path),
        series_badge="AI 60s #03",
    )

    assert pkg is not None
    assert pkg.project_id == "proj-thumb-test"
    assert pkg.headline_text == "80% CHEAPER AI"
    assert pkg.file_path_16_9 is not None
    assert pkg.file_path_9_16 is not None
    assert os.path.exists(pkg.file_path_16_9)
    assert os.path.exists(pkg.file_path_9_16)

    # Verify dimensions
    with Image.open(pkg.file_path_16_9) as img_16:
        assert img_16.size == (1280, 720)

    with Image.open(pkg.file_path_9_16) as img_9:
        assert img_9.size == (1080, 1920)

    # Verify persistence in repo
    loaded = thumbnail_service.repository.get_thumbnail_package("proj-thumb-test")
    assert loaded is not None
    assert loaded.content_sha256 == pkg.content_sha256


def test_render_candidate_thumbnail_different_strategies_produce_distinct_branches_and_shas(
    thumbnail_service: ThumbnailDesignerService, tmp_path: Path
):
    """Prove that different visual strategies execute distinct rendering branches and produce distinct SHAs.

    Uses the SAME source image and SAME headline to ensure SHA differences are caused by strategy execution,
    not text differences.
    """
    bg_path = tmp_path / "wal_base_image.png"
    img = Image.new("RGB", (1280, 720), color=(30, 45, 80))
    img.save(bg_path)

    headline = "WAL MODE"

    # 1. Render FOCUS
    res_focus = thumbnail_service.render_candidate_thumbnail(
        project_id="proj-strat-test",
        candidate_id="cand-focus",
        headline_text=headline,
        background_image_path=str(bg_path),
        visual_strategy="FOCUS",
    )

    # 2. Render SPLIT_CONTRAST (fallback dual-panel)
    res_split = thumbnail_service.render_candidate_thumbnail(
        project_id="proj-strat-test",
        candidate_id="cand-split",
        headline_text=headline,
        background_image_path=str(bg_path),
        visual_strategy="SPLIT_CONTRAST",
    )

    # 3. Render DETAIL_CROP
    res_detail = thumbnail_service.render_candidate_thumbnail(
        project_id="proj-strat-test",
        candidate_id="cand-detail",
        headline_text=headline,
        background_image_path=str(bg_path),
        visual_strategy="DETAIL_CROP",
    )

    # Assert strategies resolved distinctly
    assert res_focus.actual_visual_strategy == "FOCUS"
    assert res_split.actual_visual_strategy == "SPLIT_CONTRAST"
    assert res_detail.actual_visual_strategy == "DETAIL_CROP"

    # Assert distinct composition branches executed in renderer
    assert res_focus.layout_metadata["composition_branch"] == "FOCUS_LEFT_PILL"
    assert res_split.layout_metadata["composition_branch"] == "SPLIT_PANEL_CONTRAST"
    assert res_detail.layout_metadata["composition_branch"] == "DETAIL_ZOOM_BOTTOM_PILL"

    # Assert distinct dimensions and real image validity
    for res in (res_focus, res_split, res_detail):
        assert res.file_path_16_9.exists()
        assert res.file_path_9_16.exists()
        with Image.open(res.file_path_16_9) as im:
            assert im.size == (1280, 720)
        with Image.open(res.file_path_9_16) as im:
            assert im.size == (1080, 1920)

    # Assert SHAs differ across strategies even with identical headline & base asset
    shas = {res_focus.content_sha256, res_split.content_sha256, res_detail.content_sha256}
    assert len(shas) == 3, f"Expected 3 distinct SHAs, got {shas}"


def test_render_candidate_thumbnail_split_contrast_with_supporting_asset(
    thumbnail_service: ThumbnailDesignerService, tmp_path: Path
):
    """Prove that SPLIT_CONTRAST with a valid supporting asset executes the side-by-side branch."""
    bg1_path = tmp_path / "asset1.png"
    img1 = Image.new("RGB", (1280, 720), color=(10, 20, 40))
    img1.save(bg1_path)

    bg2_path = tmp_path / "asset2.png"
    img2 = Image.new("RGB", (1280, 720), color=(80, 20, 10))
    img2.save(bg2_path)

    res = thumbnail_service.render_candidate_thumbnail(
        project_id="proj-split-supp",
        candidate_id="cand-split-supp",
        headline_text="VS COMPARISON",
        background_image_path=str(bg1_path),
        visual_strategy="SPLIT_CONTRAST",
        supporting_image_paths=[str(bg2_path)],
    )

    assert res.actual_visual_strategy == "SPLIT_CONTRAST"
    assert res.visual_fallback_reason is None
    assert res.layout_metadata["composition_branch"] == "SPLIT_SIDE_BY_SIDE"
    assert res.layout_metadata["meta_9_16"]["layout_branch"] == "SPLIT_TOP_BOTTOM"
