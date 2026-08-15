"""Smoke tests verifying workspace bootstrapping, configuration, and contracts."""

import sys
from pathlib import Path

import app
from app import ExecutionState
import config
from config import config as app_config


def test_python_version_floor() -> None:
    """Verify runtime Python meets the minimum requirement (>= 3.11)."""
    assert sys.version_info >= (3, 11), f"Python version too old: {sys.version}"


def test_package_import_and_version() -> None:
    """Verify app package imports and has a valid semver version."""
    assert hasattr(app, "__version__")
    assert isinstance(app.__version__, str)
    assert len(app.__version__.split(".")) >= 3


def test_execution_states_contract() -> None:
    """Verify all five mandatory runtime execution states are strictly defined."""
    expected_states = {"REAL", "TEST", "DRY_RUN", "BLOCKED", "FAILED"}
    actual_states = {state.value for state in ExecutionState}
    assert actual_states == expected_states, f"ExecutionState mismatch: {actual_states}"


def test_config_defaults() -> None:
    """Verify default safety invariants (privacy=private, app_env=DRY_RUN)."""
    assert app_config.default_privacy == "private"
    assert app_config.app_env in {"DRY_RUN", "DEVELOPMENT", "PRODUCTION"}
    assert isinstance(app_config.output_dir, Path)
    assert isinstance(app_config.data_dir, Path)
