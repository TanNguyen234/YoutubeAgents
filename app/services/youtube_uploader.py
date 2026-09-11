"""Real YouTube Data API v3 Uploader utilizing native resumable HTTP multipart uploads via httpx."""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import httpx

from app.services.youtube_oauth import YouTubeOAuthManager, YouTubeOAuthError


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"


class YouTubeUploadError(RuntimeError):
    """Raised when an active YouTube API upload operation fails."""
    pass


class YouTubeUploader:
    """Executes verified live resumable uploads to YouTube Data API v3 without third-party SDK bloat."""

    def __init__(self, oauth_manager: Optional[YouTubeOAuthManager] = None):
        self.oauth_manager = oauth_manager or YouTubeOAuthManager()

    def upload_video(
        self,
        video_path: Path,
        metadata_payload: Dict[str, Any],
        thumbnail_path: Optional[Path] = None,
        timeout_seconds: float = 300.0,
    ) -> Tuple[str, str]:
        """Perform real resumable video upload to YouTube and set thumbnail.

        Returns: (video_id, watch_url)
        """
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise YouTubeUploadError(f"Video file does not exist or is empty: {video_path}")

        access_token = self.oauth_manager.get_access_token()
        file_size = video_path.stat().st_size

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        }

        # Step 1: Initiate Resumable Upload Session
        init_url = f"{YOUTUBE_UPLOAD_BASE}/videos?uploadType=resumable&part=snippet,status"
        with httpx.Client(timeout=60.0) as client:
            init_resp = client.post(init_url, headers=headers, json=metadata_payload)
            if init_resp.status_code not in (200, 201):
                raise YouTubeUploadError(
                    f"Failed to initiate YouTube upload session ({init_resp.status_code}): {init_resp.text}"
                )

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                raise YouTubeUploadError("YouTube API did not return a resumable upload Location header.")

        # Step 2: Stream / PUT Video File Content in Chunks (8MB standard chunk)
        chunk_size = 8 * 1024 * 1024  # 8MB chunk
        video_id = None
        with open(video_path, "rb") as vf:
            with httpx.Client(timeout=timeout_seconds) as client:
                start_byte = 0
                while start_byte < file_size:
                    chunk = vf.read(chunk_size)
                    end_byte = start_byte + len(chunk) - 1
                    upload_headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                    }
                    put_resp = client.put(upload_url, headers=upload_headers, content=chunk)
                    if put_resp.status_code in (200, 201):
                        video_data = put_resp.json()
                        video_id = video_data.get("id")
                        if not video_id:
                            raise YouTubeUploadError(f"YouTube response missing video ID: {video_data}")
                        break
                    elif put_resp.status_code == 308:
                        # Resume incomplete, proceed to next chunk
                        start_byte = end_byte + 1
                    else:
                        raise YouTubeUploadError(
                            f"Failed to upload video content to YouTube ({put_resp.status_code}): {put_resp.text}"
                        )

        if not video_id:
            raise YouTubeUploadError(f"Upload completed without returning a valid video ID for {video_path}.")

        # Step 3: Optional Thumbnail Upload
        if thumbnail_path and thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
            thumb_ok = self.set_thumbnail(video_id=video_id, thumbnail_path=thumbnail_path, access_token=access_token)
            if not thumb_ok:
                import logging
                logging.getLogger("youtube_uploader").warning(
                    f"Warning: Custom thumbnail upload failed for YouTube video '{video_id}'. Video remains published."
                )

        watch_url = f"https://youtu.be/{video_id}"
        return video_id, watch_url

    def set_thumbnail(
        self,
        video_id: str,
        thumbnail_path: Path,
        access_token: Optional[str] = None,
    ) -> bool:
        """Upload custom thumbnail to an existing YouTube video."""
        token = access_token or self.oauth_manager.get_access_token()
        url = f"{YOUTUBE_UPLOAD_BASE}/thumbnails/set?videoId={video_id}"

        content_type = "image/png" if thumbnail_path.suffix.lower() == ".png" else "image/jpeg"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Content-Length": str(thumbnail_path.stat().st_size),
        }

        with open(thumbnail_path, "rb") as tf:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, headers=headers, content=tf.read())
                return resp.status_code in (200, 201)
