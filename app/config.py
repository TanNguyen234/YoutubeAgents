"""Application configuration interface and settings loader."""

from config import AppConfig, config


def get_settings() -> AppConfig:
    """Retrieve the application configuration singleton."""
    return config


__all__ = ["AppConfig", "config", "get_settings"]
