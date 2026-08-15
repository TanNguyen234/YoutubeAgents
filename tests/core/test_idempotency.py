"""Unit tests for scoped idempotency key management, stale lease recovery, and multi-thread concurrency safety."""

import gc
import threading
import time
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
    scope = "script_generation"

    # Check key initially does not exist
    record = mgr.get(key, scope=scope)
    assert record is None

    # Acquire lock for key
    acquired = mgr.acquire(key, scope=scope)
    assert acquired is True

    # Duplicate acquisition while active in progress should fail
    duplicate_acquired = mgr.acquire(key, scope=scope)
    assert duplicate_acquired is False

    # Complete execution with response payload
    response_data = {"status": "SUCCESS", "script_id": "script-01", "scenes_count": 5}
    mgr.complete(key, scope=scope, response=response_data)

    # Subsequent retrieval returns completed cached response
    cached = mgr.get(key, scope=scope)
    assert cached is not None
    assert cached.status == "COMPLETED"
    assert cached.response == response_data


def test_scoped_identity_allows_same_key_in_different_scopes(temp_db: Path) -> None:
    """Verify scoped identity: same key can be acquired independently under different scopes."""
    mgr = IdempotencyManager(temp_db)
    shared_key = "step-render-101"

    # Scope 1: video rendering
    acquired_1 = mgr.acquire(shared_key, scope="video_rendering")
    assert acquired_1 is True

    # Scope 2: thumbnail rendering
    acquired_2 = mgr.acquire(shared_key, scope="thumbnail_rendering")
    assert acquired_2 is True

    # Duplicate in Scope 1 still blocked
    assert mgr.acquire(shared_key, scope="video_rendering") is False


def test_stale_pending_lease_recovery(temp_db: Path) -> None:
    """Verify stale PENDING lock whose lease expired can be re-acquired safely."""
    mgr = IdempotencyManager(temp_db)
    key = "stale-worker-task"
    scope = "transcription"

    # Worker 1 acquires with 1 second lease
    acquired = mgr.acquire(key, scope=scope, lease_seconds=1)
    assert acquired is True

    # Immediate re-acquisition fails (lease still valid)
    assert mgr.acquire(key, scope=scope, lease_seconds=1) is False

    # Wait for lease to expire
    time.sleep(1.2)

    # Worker 2 attempts acquisition -> detects expired PENDING lease and recovers lock
    recovered = mgr.acquire(key, scope=scope, lease_seconds=10)
    assert recovered is True


def test_concurrent_insert_race_has_exactly_one_winner(temp_db: Path) -> None:
    """Verify concurrent worker threads competing to acquire the same key yields exactly 1 winner and 0 errors."""
    key = "concurrent-render-task"
    scope = "rendering"
    num_threads = 10
    results = []
    exceptions = []

    def worker():
        try:
            mgr = IdempotencyManager(temp_db)
            acquired = mgr.acquire(key, scope=scope, lease_seconds=60)
            results.append(acquired)
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant: No leaked IntegrityError or sqlite errors
    assert len(exceptions) == 0
    # Invariant: Exactly 1 thread won the lease
    assert results.count(True) == 1
    assert results.count(False) == num_threads - 1


def test_concurrent_stale_lease_recovery_has_exactly_one_winner(temp_db: Path) -> None:
    """Verify concurrent worker threads competing to recover an expired stale lease yields exactly 1 winner."""
    key = "concurrent-stale-task"
    scope = "tts_synthesis"

    # Initial acquisition with 0.5s lease
    init_mgr = IdempotencyManager(temp_db)
    assert init_mgr.acquire(key, scope=scope, lease_seconds=1) is True

    # Wait for expiration
    time.sleep(1.2)

    num_threads = 10
    results = []
    exceptions = []

    def worker():
        try:
            mgr = IdempotencyManager(temp_db)
            acquired = mgr.acquire(key, scope=scope, lease_seconds=60)
            results.append(acquired)
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant: No leaked exceptions
    assert len(exceptions) == 0
    # Invariant: Exactly 1 thread won the stale recovery CAS
    assert results.count(True) == 1
    assert results.count(False) == num_threads - 1
