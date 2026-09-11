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
