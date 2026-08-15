"""Idempotency management for resilient agent and API operations."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class IdempotencyRecord(BaseModel):
    """Represents a recorded idempotency execution state."""

    key: str
    scope: str
    status: str = Field(description="PENDING, COMPLETED, or FAILED")
    response: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str


class IdempotencyManager:
    """Provides atomic idempotency locking and response caching using SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        """Retrieve the existing idempotency record by key."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, scope, status, response, created_at, updated_at FROM idempotency_keys WHERE key = ?",
                (key,),
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
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def acquire(self, key: str, scope: str = "default") -> bool:
        """Attempt to acquire an idempotency lock. Returns True if acquired, False if already exists."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO idempotency_keys (key, scope, status, response, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', NULL, ?, ?)
                    """,
                    (key, scope, now_iso, now_iso),
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def complete(self, key: str, response: Dict[str, Any]) -> None:
        """Mark an idempotency key as COMPLETED with its response payload."""
        now_iso = datetime.now(timezone.utc).isoformat()
        response_json = json.dumps(response, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = 'COMPLETED', response = ?, updated_at = ?
                WHERE key = ?
                """,
                (response_json, now_iso, key),
            )
            conn.commit()

    def fail(self, key: str, error_message: str) -> None:
        """Mark an idempotency key as FAILED."""
        now_iso = datetime.now(timezone.utc).isoformat()
        err_payload = json.dumps({"error": error_message}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = 'FAILED', response = ?, updated_at = ?
                WHERE key = ?
                """,
                (err_payload, now_iso, key),
            )
            conn.commit()
