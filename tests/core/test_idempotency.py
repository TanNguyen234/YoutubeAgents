"""Unit tests for idempotency key management and replay protection."""

import gc
from pathlib import Path
import pytest
import tempfile

from app.db.schema import init_database
from app.core.idempotency import IdempotencyManager, IdempotencyRecord


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "idempotency_test.db"
        init_database(db_path)
        yield db_path
        gc.collect()


def test_idempotency_lifecycle(temp_db: Path) -> None:
    """Verify idempotency key registration, execution, and cached response replay."""
    mgr = IdempotencyManager(temp_db)
    key = "generate-script-proj-01-v1"

    # Check key initially does not exist
    record = mgr.get(key)
    assert record is None

    # Acquire lock for key
    acquired = mgr.acquire(key, scope="script_generation")
    assert acquired is True

    # Duplicate acquisition while in progress should fail
    duplicate_acquired = mgr.acquire(key, scope="script_generation")
    assert duplicate_acquired is False

    # Complete execution with response payload
    response_data = {"status": "SUCCESS", "script_id": "script-01", "scenes_count": 5}
    mgr.complete(key, response=response_data)

    # Subsequent retrieval returns completed cached response
    cached = mgr.get(key)
    assert cached is not None
    assert cached.status == "COMPLETED"
    assert cached.response == response_data
