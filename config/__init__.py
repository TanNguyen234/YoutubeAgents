"""Configuration module for YouTube Autopilot."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Base application settings loaded from environment or defaults."""

    app_env: str = os.getenv("APP_ENV", "DRY_RUN")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/youtube_autopilot.db")
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "output"))
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    default_privacy: str = os.getenv("YOUTUBE_DEFAULT_PRIVACY_STATUS", "private")


# Global default configuration instance
config = AppConfig()

__all__ = ["AppConfig", "config"]
