"""Integration tests for FastAPI REST API endpoints."""

import gc
from pathlib import Path
import pytest
import tempfile
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.db.schema import init_database
from app.db.repository import SQLiteRepository
from app.domain.enums import VideoLifecycleState, PlatformFormat, PublicationStatus, PrivacyStatus
from app.domain.models import Channel, VideoProject, PublicationJob


@pytest.fixture
def test_client_and_repo():
    """Create test client with dedicated temporary SQLite repository."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "api_test.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)
        # Pre-seed a valid channel for foreign key integrity
        channel = Channel(
            id="chan-001",
            title="AI Hub",
            handle="@AIHub",
            niche="AI",
            target_audience="Devs",
        )
        repo.save_channel(channel)
        app = create_app(repo=repo)
        client = TestClient(app)
        yield client, repo
        gc.collect()


def test_get_health(test_client_and_repo) -> None:
    """Verify /health endpoint returns ok and current version."""
    client, _ = test_client_and_repo
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_get_capabilities(test_client_and_repo) -> None:
    """Verify /capabilities returns supported formats, states, and stages."""
    client, _ = test_client_and_repo
    response = client.get("/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert "stages" in data
    assert "formats" in data
    assert "lifecycle_states" in data
    assert len(data["stages"]) == 15
    assert "SHORTS_9_16" in data["formats"]


def test_get_videos_and_by_id(test_client_and_repo) -> None:
    """Verify /videos listing and /videos/{id} lookup."""
    client, repo = test_client_and_repo

    # Seed project
    project = VideoProject(
        id="proj-api-01",
        channel_id="chan-001",
        title="Testing REST API Endpoints",
        format=PlatformFormat.LONG_FORM_16_9,
        state=VideoLifecycleState.PLANNED,
    )
    repo.save_video_project(project)

    # List
    list_resp = client.get("/videos")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["id"] == "proj-api-01"

    # Detail
    detail_resp = client.get("/videos/proj-api-01")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["title"] == "Testing REST API Endpoints"
    assert detail["state"] == "PLANNED"

    # Not Found
    not_found = client.get("/videos/non-existent-id")
    assert not_found.status_code == 404


def test_get_queue(test_client_and_repo) -> None:
    """Verify /queue endpoint returns publication jobs with PrivacyStatus enum."""
    client, repo = test_client_and_repo

    project = VideoProject(
        id="proj-api-01",
        channel_id="chan-001",
        title="Testing REST API Endpoints",
        format=PlatformFormat.LONG_FORM_16_9,
        state=VideoLifecycleState.APPROVED,
    )
    repo.save_video_project(project)

    job = PublicationJob(
        id="job-api-01",
        project_id="proj-api-01",
        channel_id="chan-001",
        status=PublicationStatus.PENDING,
    )
    repo.save_publication_job(job)

    response = client.get("/queue")
    assert response.status_code == 200
    queue = response.json()
    assert len(queue) == 1
    assert queue[0]["id"] == "job-api-01"
    assert queue[0]["privacy_status"] == "private"
