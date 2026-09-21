from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import ActionStatus, Event, ProposedAction


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    execute_at TEXT,
                    payload TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES events(id)
                );
                """
            )

    def save_event_and_actions(
        self, event: Event, actions: list[ProposedAction]
    ) -> int:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)",
                (
                    event.id,
                    event.type,
                    event.occurred_at.isoformat(),
                    json.dumps(event.payload, sort_keys=True),
                ),
            )
            inserted = 0
            for action in actions:
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO actions (
                        id, event_id, type, summary, status, risk,
                        requires_approval, execute_at, payload,
                        idempotency_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.id,
                        action.event_id,
                        action.type,
                        action.summary,
                        action.initial_status.value,
                        action.risk.value,
                        int(action.requires_approval),
                        action.execute_at.isoformat() if action.execute_at else None,
                        json.dumps(action.payload, sort_keys=True),
                        action.idempotency_key,
                        action.created_at.isoformat(),
                    ),
                )
                inserted += cursor.rowcount
            return inserted

    def list_actions(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return list(
                db.execute(
                    """
                    SELECT id, type, summary, status, risk, execute_at
                    FROM actions
                    ORDER BY COALESCE(execute_at, created_at), created_at
                    """
                )
            )

    def approve(self, action_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE actions SET status = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ActionStatus.APPROVED.value,
                    action_id,
                    ActionStatus.PENDING_APPROVAL.value,
                ),
            )
            return cursor.rowcount == 1

    def claim_due_actions(
        self, action_type: str, now: datetime, limit: int = 100
    ) -> list[sqlite3.Row]:
        """Atomically claim due, approval-eligible actions for one runner."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = list(
                db.execute(
                    """
                    SELECT id, type, summary, status, payload, execute_at
                    FROM actions
                    WHERE type = ?
                      AND status IN (?, ?)
                      AND (execute_at IS NULL OR execute_at <= ?)
                    ORDER BY COALESCE(execute_at, created_at), created_at
                    LIMIT ?
                    """,
                    (
                        action_type,
                        ActionStatus.READY.value,
                        ActionStatus.APPROVED.value,
                        now.isoformat(),
                        limit,
                    ),
                )
            )
            if rows:
                placeholders = ", ".join("?" for _ in rows)
                db.execute(
                    f"UPDATE actions SET status = ? WHERE id IN ({placeholders})",
                    (ActionStatus.PROCESSING.value, *(row["id"] for row in rows)),
                )
            return rows

    def list_cleaner_reminder_actions(self, now: datetime) -> list[sqlite3.Row]:
        """List confirmed cleaner work that may appear in reminder messages.

        The action's execution delay does not hide a confirmed date from the
        schedule. The originating event is included so older stored actions,
        created before ``check_in`` was copied into their payload, still work.
        """
        with self.connect() as db:
            return list(
                db.execute(
                    """
                    SELECT actions.id, actions.status, actions.payload,
                           actions.execute_at, events.payload AS event_payload,
                           events.occurred_at AS event_occurred_at
                    FROM actions
                    JOIN events ON events.id = actions.event_id
                    WHERE actions.type = 'cleaner_sms'
                      AND actions.status IN (?, ?, ?)
                    ORDER BY actions.execute_at, actions.created_at
                    """,
                    (
                        ActionStatus.READY.value,
                        ActionStatus.APPROVED.value,
                        ActionStatus.EXECUTED.value,
                    ),
                )
            )

    def finish_action(self, action_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE actions SET status = ? WHERE id = ? AND status = ?",
                (
                    ActionStatus.EXECUTED.value,
                    action_id,
                    ActionStatus.PROCESSING.value,
                ),
            )
            return cursor.rowcount == 1

    def release_action(self, action_id: str, previous_status: str) -> bool:
        if previous_status not in {
            ActionStatus.READY.value,
            ActionStatus.APPROVED.value,
        }:
            raise ValueError("Cannot release an action to an unsafe status")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE actions SET status = ? WHERE id = ? AND status = ?",
                (previous_status, action_id, ActionStatus.PROCESSING.value),
            )
            return cursor.rowcount == 1
