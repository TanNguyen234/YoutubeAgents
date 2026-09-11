"""Stage 12 Human Review Gate service managing operator decisions and state transitions."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from app.db.repository import SQLiteRepository
from app.domain.enums import PrivacyStatus, ReviewAction, VideoLifecycleState
from app.domain.models import ReviewRecord, VideoProject


class ReviewGateError(RuntimeError):
    """Raised when review gate operations violate preconditions or policies."""
    pass


class HumanReviewGateService:
    """Provides human review workflows, recording auditable review records and advancing the lifecycle FSM."""

    def __init__(self, repository: SQLiteRepository):
        self.repo = repository

    def inspect_project_for_review(self, project_id: str) -> Dict[str, Any]:
        """Compile a review payload for human operator inspection."""
        project = self.repo.get_video_project(project_id)
        if not project:
            raise ReviewGateError(f"Project '{project_id}' not found.")

        ready = project.state in (VideoLifecycleState.READY_FOR_REVIEW, VideoLifecycleState.QA_FAILED)
        canonical_narration = project.script.get_canonical_narration() if project.script else ""

        return {
            "project_id": project.id,
            "title": project.title,
            "state": project.state.value,
            "ready_for_review": ready,
            "qa_status": project.quality.status.value if project.quality else None,
            "loudness_lufs": project.quality.loudness_lufs if project.quality else None,
            "duration_seconds": project.quality.duration_seconds if project.quality else None,
            "issues": project.quality.issues if project.quality else [],
            "asset_count": len(project.assets),
            "script_title": project.script.title if project.script else None,
            "canonical_narration_words": len(canonical_narration.split()),
            "canonical_narration_preview": canonical_narration[:200] + "..." if len(canonical_narration) > 200 else canonical_narration,
        }

    def submit_review(
        self,
        project_id: str,
        operator: str,
        action: ReviewAction,
        notes: Optional[str] = None,
        approved_privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE,
        media_overrides: Optional[Dict[str, Any]] = None,
    ) -> ReviewRecord:
        """Execute a review decision, advance the project lifecycle, and record an immutable review audit record."""
        project = self.repo.get_video_project(project_id)
        if not project:
            raise ReviewGateError(f"Project '{project_id}' not found.")

        if project.state not in (VideoLifecycleState.READY_FOR_REVIEW, VideoLifecycleState.QA_FAILED):
            raise ReviewGateError(
                f"Review gate requires project in READY_FOR_REVIEW or QA_FAILED, but '{project_id}' is in {project.state.value}."
            )

        overrides = media_overrides or {}
        record_id = f"rev-{uuid4().hex[:8]}"
        record = ReviewRecord(
            id=record_id,
            project_id=project_id,
            operator=operator,
            action=action,
            notes=notes,
            approved_privacy_status=approved_privacy_status,
            media_overrides=overrides,
            reviewed_at=datetime.now(timezone.utc),
        )

        current_state = project.state

        if action == ReviewAction.APPROVE:
            if current_state != VideoLifecycleState.READY_FOR_REVIEW:
                raise ReviewGateError(f"Cannot approve project in {current_state.value}. Must be in READY_FOR_REVIEW.")
            self.repo.save_review_record(record)
            self.repo.update_project_state(
                project_id=project.id,
                to_state=VideoLifecycleState.APPROVED,
                reason=f"Approved by operator {operator}: {notes or 'No notes provided'}",
                expected_current_state=current_state,
            )
        elif action == ReviewAction.REJECT:
            self.repo.save_review_record(record)
            self.repo.update_project_state(
                project_id=project.id,
                to_state=VideoLifecycleState.REJECTED,
                reason=f"Rejected by operator {operator}: {notes or 'No notes provided'}",
                expected_current_state=current_state,
            )
        elif action == ReviewAction.BLOCK:
            self.repo.save_review_record(record)
            self.repo.update_project_state(
                project_id=project.id,
                to_state=VideoLifecycleState.BLOCKED,
                reason=f"Blocked by operator {operator}: {notes or 'Requires manual resolution'}",
                expected_current_state=current_state,
            )
        elif action == ReviewAction.RERENDER:
            self.repo.save_review_record(record)
            self.repo.update_project_state(
                project_id=project.id,
                to_state=VideoLifecycleState.PRODUCING,
                reason=f"Rerender requested by operator {operator}. Overrides: {overrides}",
                expected_current_state=current_state,
            )
        else:
            raise ReviewGateError(f"Unsupported review action: {action}")

        return record
