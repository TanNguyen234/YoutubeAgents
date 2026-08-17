"""API route definitions for YouTube Autopilot."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from app import __version__
from app.db.repository import SQLiteRepository
from app.domain.enums import PlatformFormat, PublicationStatus, VideoLifecycleState
from app.domain.models import PublicationJob, VideoProject


def get_router(repo: SQLiteRepository) -> APIRouter:
    """Construct and configure FastAPI router with repository dependency."""
    router = APIRouter()

    @router.get("/health", tags=["System"])
    def get_health():
        return {
            "status": "ok",
            "version": __version__,
            "engine": "antigravity-autopilot",
        }

    @router.get("/capabilities", tags=["System"])
    def get_capabilities():
        stages = [
            "Research",
            "Select Topic",
            "Evidence",
            "Script",
            "Fact Check",
            "Visual Plan",
            "Media",
            "TTS",
            "Subtitle",
            "Render",
            "QA",
            "Human Review",
            "YouTube Upload/Schedule",
            "Analytics",
            "Strategy Feedback",
        ]
        from app.media.capabilities import check_media_capabilities
        caps = check_media_capabilities()
        return {
            "version": __version__,
            "stages": stages,
            "formats": [fmt.value for fmt in PlatformFormat],
            "lifecycle_states": [state.value for state in VideoLifecycleState],
            "reasoning_backend": "Antigravity Runtime (Local)",
            "evidence_assistant": "NotebookLM MCP",
            "rendering_engine": "FFmpeg",
            "media_capabilities": caps.model_dump(),
        }

    @router.get("/videos", response_model=List[VideoProject], tags=["Videos"])
    def list_videos(state: Optional[VideoLifecycleState] = Query(default=None)):
        return repo.list_video_projects(state=state)

    @router.get("/videos/{project_id}", response_model=VideoProject, tags=["Videos"])
    def get_video(project_id: str):
        project = repo.get_video_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Video project '{project_id}' not found")
        return project

    @router.get("/queue", response_model=List[PublicationJob], tags=["Publication"])
    def get_queue(status: Optional[PublicationStatus] = Query(default=None)):
        return repo.get_publication_queue(status=status)

    return router
