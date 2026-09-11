"""Stage 13 YouTube Upload and Scheduling Service enforcing strict privacy safeguards and metadata contracts."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.db.repository import SQLiteRepository
from app.domain.enums import (
    AssetType,
    PrivacyStatus,
    PublicationStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import PublicationJob, VideoProject
from app.services.youtube_oauth import YouTubeOAuthManager
from app.services.youtube_uploader import YouTubeUploader



class YouTubePublishError(RuntimeError):
    """Raised when publication preconditions are violated or upload fails."""
    pass


class YouTubePublisherService:
    """Orchestrates YouTube video uploads, metadata formatting, scheduling, and lifecycle transitions."""

    def __init__(
        self,
        repository: SQLiteRepository,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
    ):
        self.repo = repository
        self.oauth_mgr = oauth_manager


    def build_metadata_payload(
        self,
        project: VideoProject,
        privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE,
        scheduled_time: Optional[datetime] = None,
        contains_synthetic_media: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Construct a validated YouTube Data API v3 upload payload."""
        # 1. Channel publishing defaults
        channel = self.repo.get_channel(project.channel_id) if project.channel_id else None
        cat_id = getattr(channel, "youtube_category_id", "28") if channel else "28"
        lang = getattr(channel, "default_language", "en") if channel else "en"
        kids = getattr(channel, "made_for_kids", False) if channel else False
        base_tags = list(getattr(channel, "default_tags", [])) if channel else []

        # 1b. Synthetic Media Disclosure (P1-7)
        if contains_synthetic_media is None:
            manifest_path = Path(f"output/projects/{project.id}/manifests/render_manifest_{project.id}.json")
            if manifest_path.exists():
                try:
                    import json
                    m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    contains_synthetic_media = bool(m_data.get("contains_synthetic_media", False))
                except Exception:
                    pass

        if contains_synthetic_media is None:
            contains_synthetic_media = False
            for a in (project.assets or []):
                url = (a.source_url or "").lower()
                if "gflow" in url or "generated_image" in url or "generated_video" in url:
                    contains_synthetic_media = True
                    break

        # 1c. YouTube Scheduling Privacy Semantics (P1-6):
        # Under YouTube Data API v3, scheduled videos must be uploaded with privacyStatus="private"
        # and publishAt set to the scheduled UTC timestamp.
        if scheduled_time:
            api_privacy = PrivacyStatus.PRIVATE.value
            publish_at = scheduled_time.isoformat()
        else:
            api_privacy = privacy_status.value
            publish_at = None

        # 2. Consume SEOPackage if present
        seo_pkg = self.repo.get_seo_package(project.id)
        if seo_pkg:
            raw_title = seo_pkg.selected_title or project.title
            title = raw_title[:100].strip()

            desc = (seo_pkg.description or "").strip()
            # If chapters exist and are not already formatted in description, append them
            if seo_pkg.chapters and "00:00" not in desc:
                chapter_lines = ["\n=== CHAPTERS ==="]
                for ch in seo_pkg.chapters:
                    chapter_lines.append(f"{ch.timestamp_formatted} {ch.title}")
                desc = desc + "\n" + "\n".join(chapter_lines)
            description = desc.strip()
            tags = list(seo_pkg.tags) if seo_pkg.tags else list(project.metadata_tags or [])
        else:
            raw_title = project.script.title if project.script else project.title
            title = raw_title[:100].strip()

            # Build description with citations and overview
            desc_lines = []
            if project.script and project.script.hook:
                desc_lines.append(project.script.hook)
                desc_lines.append("")

            desc_lines.append("=== OVERVIEW ===")
            canonical_narration = project.script.get_canonical_narration() if project.script else ""
            if canonical_narration:
                desc_lines.append(canonical_narration[:400] + ("..." if len(canonical_narration) > 400 else ""))
                desc_lines.append("")

            dossier = self.repo.get_research_dossier(project.id)
            if dossier and dossier.sources:
                desc_lines.append("=== SOURCES & CITATIONS ===")
                for s in dossier.sources[:5]:
                    desc_lines.append(f"- {s.title}: {s.url}")
                desc_lines.append("")

            desc_lines.append("Generated autonomously by YouTube Autopilot.")
            desc_lines.append("#Tech #Engineering #Automation")
            description = "\n".join(desc_lines)

            # Build tags (max 500 chars total)
            tags = list(project.metadata_tags or [])
            if project.script and "Shorts" in project.script.title:
                tags.append("Shorts")
            tags.extend(["Tech", "Guide", "Tutorial"])

        # Combine with channel default tags
        for t in base_tags:
            if t not in tags:
                tags.append(t)

        # Deduplicate preserving order
        seen = set()
        clean_tags = []
        for t in tags:
            if t.lower() not in seen and len(t) <= 30:
                seen.add(t.lower())
                clean_tags.append(t)

        status_dict: Dict[str, Any] = {
            "privacyStatus": api_privacy,
            "selfDeclaredMadeForKids": kids,
            "containsSyntheticMedia": bool(contains_synthetic_media),
        }
        if publish_at:
            status_dict["publishAt"] = publish_at

        return {
            "snippet": {
                "title": title,
                "description": description,
                "tags": clean_tags[:15],
                "categoryId": cat_id,
                "defaultLanguage": lang,
            },
            "status": status_dict,
        }

    def publish_project(
        self,
        project_id: str,
        scheduled_time: Optional[datetime] = None,
        enforce_approved_privacy: bool = True,
        force_dry_run: bool = False,
    ) -> Tuple[PublicationJob, str]:
        """Execute Stage 13 upload and scheduling workflow with verified lifecycle transitions.

        Returns (PublicationJob, execution_mode: 'REAL' | 'DRY_RUN').
        """
        project = self.repo.get_video_project(project_id)
        if not project:
            raise YouTubePublishError(f"Project '{project_id}' not found.")

        # 1. State Precondition: Must be APPROVED
        if project.state != VideoLifecycleState.APPROVED:
            raise YouTubePublishError(
                f"Publication requires project in APPROVED state, but '{project_id}' is in {project.state.value}."
            )

        # 2. Review Record & Privacy Verification
        latest_review = self.repo.get_latest_review(project_id)
        if not latest_review or latest_review.action != ReviewAction.APPROVE:
            raise YouTubePublishError(
                f"Project '{project_id}' has no valid APPROVE review record in audit log."
            )

        # Privacy safety: Default is always PRIVATE unless explicitly approved
        target_privacy = latest_review.approved_privacy_status if enforce_approved_privacy else PrivacyStatus.PRIVATE

        # 3. Master Video Asset Pre-flight Check
        final_video_asset = None
        for a in project.assets:
            if a.asset_type == AssetType.FINAL_VIDEO:
                final_video_asset = a
                break

        video_path = None
        if final_video_asset and Path(final_video_asset.file_path).exists():
            video_path = Path(final_video_asset.file_path)
        else:
            # Check default output location
            default_path = Path(f"output/projects/{project_id}/render/final_{project_id}.mp4")
            if default_path.exists():
                video_path = default_path

        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            raise YouTubePublishError(
                f"No valid rendered master video file found for project '{project_id}'."
            )

        # 4. Construct API Payload
        payload = self.build_metadata_payload(
            project=project,
            privacy_status=target_privacy,
            scheduled_time=scheduled_time,
        )

        # 5. Transition to UPLOADING
        self.repo.update_project_state(
            project_id=project.id,
            to_state=VideoLifecycleState.UPLOADING,
            reason="Starting YouTube upload pipeline",
            expected_current_state=VideoLifecycleState.APPROVED,
        )

        # 6. Check for Live YouTube Credentials
        oauth_mgr = self.oauth_mgr or YouTubeOAuthManager()
        has_credentials = (not force_dry_run) and oauth_mgr.has_valid_token()

        job_id = f"pub-{uuid4().hex[:8]}"

        if has_credentials:
            # Real live upload path via native HTTP multipart API
            try:
                uploader = YouTubeUploader(oauth_manager=oauth_mgr)
                thumb_path = None
                for a in project.assets:
                    if a.asset_type == AssetType.THUMBNAIL and Path(a.file_path).exists():
                        thumb_path = Path(a.file_path)
                        break
                if not thumb_path:
                    thumb_pkg = self.repo.get_thumbnail_package(project.id)
                    if thumb_pkg and Path(thumb_pkg.file_path_16_9).exists():
                        thumb_path = Path(thumb_pkg.file_path_16_9)
                yt_video_id, watch_url = uploader.upload_video(
                    video_path=video_path,
                    metadata_payload=payload,
                    thumbnail_path=thumb_path,
                )
                execution_mode = "REAL"
            except Exception as e:
                self.repo.update_project_state(
                    project_id=project.id,
                    to_state=VideoLifecycleState.FAILED,
                    reason=f"Live upload failed: {e}",
                    expected_current_state=VideoLifecycleState.UPLOADING,
                )
                raise YouTubePublishError(f"Live YouTube upload failed: {e}") from e

            # Final Lifecycle State Transition for REAL uploads
            if scheduled_time is not None:
                target_state = VideoLifecycleState.SCHEDULED
                job_status = PublicationStatus.SCHEDULED
                now_published = None
            else:
                target_state = VideoLifecycleState.PUBLISHED
                job_status = PublicationStatus.COMPLETED
                now_published = datetime.now(timezone.utc)

            transition_reason = f"Upload completed successfully (Mode: REAL). YouTube Video ID: {yt_video_id}"
        else:
            # Verified DRY_RUN path: full schema and file validation without credential forgery
            yt_video_id = f"yt-dryrun-{hashlib.sha256(str(video_path).encode()).hexdigest()[:11]}"
            execution_mode = "DRY_RUN"
            target_state = VideoLifecycleState.BLOCKED
            job_status = PublicationStatus.PENDING
            now_published = None
            transition_reason = f"Dry-run simulation validated. Live upload BLOCKED: Missing valid OAuth credentials (token.json). Simulated ID: {yt_video_id}"

        self.repo.update_project_state(
            project_id=project.id,
            to_state=target_state,
            reason=transition_reason,
            expected_current_state=VideoLifecycleState.UPLOADING,
        )

        job = PublicationJob(
            id=job_id,
            project_id=project.id,
            channel_id=project.channel_id,
            status=job_status,
            privacy_status=target_privacy,
            scheduled_publish_time=scheduled_time,
            youtube_video_id=yt_video_id,
            published_at=now_published,
            contains_synthetic_media=bool(payload["status"].get("containsSyntheticMedia", False)),
            error_message=None if execution_mode == "REAL" else "Dry-run validation complete. Awaiting live OAuth credentials for upload.",
            created_at=datetime.now(timezone.utc),
        )
        self.repo.save_publication_job(job)

        return job, execution_mode
