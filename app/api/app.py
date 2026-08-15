"""FastAPI application factory and middleware configuration."""

from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import get_router
from app.config import get_settings
from app.db.repository import SQLiteRepository


def create_app(repo: Optional[SQLiteRepository] = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    app = FastAPI(
        title="YouTube Autopilot API",
        description="Autonomous YouTube Channel Management & Video Generation API powered by Antigravity",
        version=__version__,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if repo is None:
        db_path = Path(settings.database_url.replace("sqlite:///", ""))
        repo = SQLiteRepository(db_path)

    router = get_router(repo)
    app.include_router(router)

    return app
