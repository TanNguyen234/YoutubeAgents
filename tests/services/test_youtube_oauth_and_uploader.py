"""Tests for YouTubeOAuthManager and YouTubeUploader."""

import json
from pathlib import Path
import pytest
import respx
import httpx

from app.services.youtube_oauth import (
    YouTubeOAuthManager,
    YouTubeOAuthError,
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
)
from app.services.youtube_uploader import (
    YouTubeUploader,
    YouTubeUploadError,
    YOUTUBE_UPLOAD_BASE,
)


def test_oauth_manager_missing_secrets(tmp_path: Path):
    """Manager must report False and raise typed error when secrets file is absent."""
    mgr = YouTubeOAuthManager(
        secrets_path=tmp_path / "nonexistent.json",
        token_path=tmp_path / "token.json",
    )
    assert not mgr.has_client_secrets()
    assert not mgr.has_valid_token()
    with pytest.raises(YouTubeOAuthError) as exc_info:
        mgr.load_client_secrets()
    assert "Missing 'client_secrets.json'" in str(exc_info.value)


def test_oauth_manager_generate_auth_url(tmp_path: Path):
    """Manager must generate valid Google OAuth URL with youtube.upload scope."""
    secrets_file = tmp_path / "client_secrets.json"
    secrets_file.write_text(
        json.dumps({
            "installed": {
                "client_id": "test-client-id-123.apps.googleusercontent.com",
                "client_secret": "test-secret-456",
            }
        }),
        encoding="utf-8",
    )
    mgr = YouTubeOAuthManager(secrets_path=secrets_file, token_path=tmp_path / "token.json")
    assert mgr.has_client_secrets()
    url = mgr.generate_auth_url(redirect_uri="http://localhost:8080/")
    assert url.startswith(GOOGLE_AUTH_URL)
    assert "client_id=test-client-id-123" in url
    assert "scope=" in url
    assert "youtube.upload" in url


@respx.mock
def test_uploader_resumable_upload_flow(tmp_path: Path):
    """Uploader must successfully initiate resumable session and upload video content."""
    # Setup token
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "ya29.fake-token"}), encoding="utf-8")
    mgr = YouTubeOAuthManager(token_path=token_file)
    uploader = YouTubeUploader(oauth_manager=mgr)

    video_file = tmp_path / "test_video.mp4"
    video_file.write_bytes(b"\x00" * 1024)

    # 1. Mock session initiation
    upload_resumable_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc123session"
    respx.post(f"{YOUTUBE_UPLOAD_BASE}/videos").respond(
        status_code=200,
        headers={"Location": upload_resumable_url},
    )

    # 2. Mock PUT video bytes
    respx.put(upload_resumable_url).respond(
        status_code=200,
        json={"id": "dQw4w9WgXcQ", "status": {"uploadStatus": "uploaded"}},
    )

    video_id, watch_url = uploader.upload_video(
        video_path=video_file,
        metadata_payload={"snippet": {"title": "Test Title"}},
    )

    assert video_id == "dQw4w9WgXcQ"
    assert watch_url == "https://youtu.be/dQw4w9WgXcQ"


def test_oauth_manager_cached_token_not_refreshed(tmp_path: Path):
    """If access token is cached and not expired, return immediately without network call."""
    from datetime import datetime, timezone
    token_file = tmp_path / "token.json"
    now_ts = datetime.now(timezone.utc).timestamp()
    token_file.write_text(
        json.dumps({
            "access_token": "ya29.cached-token-123",
            "expires_at": now_ts + 3600,
        }),
        encoding="utf-8",
    )
    mgr = YouTubeOAuthManager(token_path=token_file)
    # Should return cached token directly
    token = mgr.get_access_token()
    assert token == "ya29.cached-token-123"


@respx.mock
def test_oauth_manager_refreshes_when_expired(tmp_path: Path):
    """If access token is expired, use refresh_token to obtain and cache a new access token."""
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).timestamp()
    secrets_file = tmp_path / "client_secrets.json"
    secrets_file.write_text(
        json.dumps({
            "installed": {
                "client_id": "test-id",
                "client_secret": "test-sec",
            }
        }),
        encoding="utf-8",
    )
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({
            "access_token": "ya29.expired-token",
            "refresh_token": "1//fake-refresh-token",
            "expires_at": now_ts - 100,  # Expired
        }),
        encoding="utf-8",
    )

    respx.post(GOOGLE_TOKEN_URL).respond(
        status_code=200,
        json={
            "access_token": "ya29.brand-new-refreshed-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )

    mgr = YouTubeOAuthManager(secrets_path=secrets_file, token_path=token_file)
    token = mgr.get_access_token()

    assert token == "ya29.brand-new-refreshed-token"
    # Verify persisted to disk with fresh expires_at
    updated_disk = json.loads(token_file.read_text(encoding="utf-8"))
    assert updated_disk["access_token"] == "ya29.brand-new-refreshed-token"
    assert updated_disk["expires_at"] > now_ts + 3000

