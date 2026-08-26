from __future__ import annotations

import json
import sqlite3
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

