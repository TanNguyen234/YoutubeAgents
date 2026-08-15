"""Idempotency management with scoped keys, atomic CAS stale lease recovery, and concurrency protection."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class IdempotencyRecord(BaseModel):
    """Represents a recorded idempotency execution state."""

    key: str
    scope: str
    status: str = Field(description="PENDING, COMPLETED, or FAILED")
    response: Optional[Dict[str, Any]] = None
    expires_at: Optional[str] = None
    created_at: str
    updated_at: str


class IdempotencyManager:
    """Provides atomic scoped idempotency locking, response caching, and concurrency-safe stale pending lease recovery."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str, scope: str = "default") -> Optional[IdempotencyRecord]:
        """Retrieve the existing idempotency record by scoped key."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT key, scope, status, response, expires_at, created_at, updated_at
                FROM idempotency_keys
                WHERE scope = ? AND key = ?
                """,
                (scope, key),
            )
            row = cursor.fetchone()
            if not row:
                return None

            response_data = json.loads(row["response"]) if row["response"] else None
            return IdempotencyRecord(
                key=row["key"],
                scope=row["scope"],
                status=row["status"],
                response=response_data,
                expires_at=row["expires_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def acquire(self, key: str, scope: str = "default", lease_seconds: int = 300) -> bool:
        """Attempt to acquire an idempotency lock.

        - If key does not exist: atomically inserts PENDING lock. If another worker inserted concurrently, returns False without leaking IntegrityError.
        - If key exists in PENDING and lease has expired: atomically performs Compare-And-Set against previously observed expires_at so exactly one worker wins.
        - Otherwise: returns False.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_at_iso = (now + timedelta(seconds=lease_seconds)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, expires_at FROM idempotency_keys WHERE scope = ? AND key = ?",
                (scope, key),
            )
            row = cursor.fetchone()

            if row is None:
                # Key does not exist: attempt atomic insert
                try:
                    cursor.execute(
                        """
                        INSERT INTO idempotency_keys (key, scope, status, response, expires_at, created_at, updated_at)
                        VALUES (?, ?, 'PENDING', NULL, ?, ?, ?)
                        """,
                        (key, scope, expires_at_iso, now_iso, now_iso),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    # Concurrent worker won the race to insert; return False cleanly
                    return False

            status = row["status"]
            expires_at_str = row["expires_at"]

            if status == "PENDING" and expires_at_str:
                expires_dt = datetime.fromisoformat(expires_at_str)
                if now > expires_dt:
                    # Atomic Compare-And-Set: verify status is still PENDING and expires_at matches the exact observed value
                    cursor.execute(
                        """
                        UPDATE idempotency_keys
                        SET expires_at = ?, updated_at = ?
                        WHERE scope = ? AND key = ? AND status = 'PENDING' AND expires_at = ?
                        """,
                        (expires_at_iso, now_iso, scope, key, expires_at_str),
                    )
                    conn.commit()
                    return cursor.rowcount == 1

            # Key is either actively PENDING (lease still valid) or already COMPLETED/FAILED
            return False

    def complete(self, key: str, scope: str = "default", response: Optional[Dict[str, Any]] = None) -> None:
        """Mark a scoped idempotency key as COMPLETED with its response payload."""
        now_iso = datetime.now(timezone.utc).isoformat()
        response_json = json.dumps(response, ensure_ascii=False) if response is not None else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = 'COMPLETED', response = ?, updated_at = ?
                WHERE scope = ? AND key = ?
                """,
                (response_json, now_iso, scope, key),
            )
            conn.commit()

    def fail(self, key: str, scope: str = "default", error_message: str = "") -> None:
        """Mark a scoped idempotency key as FAILED."""
        now_iso = datetime.now(timezone.utc).isoformat()
        err_payload = json.dumps({"error": error_message}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = 'FAILED', response = ?, updated_at = ?
                WHERE scope = ? AND key = ?
                """,
                (err_payload, now_iso, scope, key),
            )
            conn.commit()
