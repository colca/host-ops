from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .base import MessagingAdapter


class FileOutboxMessagingAdapter:
    """Safe local stand-in for a programmable SMS provider."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, recipient: str, body: str) -> str:
        message_id = f"outbox-{uuid4()}"
        record = {
            "id": message_id,
            "recipient": recipient,
            "body": body,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivery_status": "not_sent",
        }
        with self.path.open("a", encoding="utf-8") as outbox:
            outbox.write(json.dumps(record, sort_keys=True) + "\n")
        return message_id

    def queue_once(
        self, recipient: str, body: str, idempotency_key: str
    ) -> tuple[str, bool]:
        """Append once even if a runner stopped after writing but before finishing."""
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("idempotency_key") == idempotency_key:
                    return str(record["id"]), False
        message_id = f"outbox-{uuid4()}"
        record = {
            "id": message_id,
            "idempotency_key": idempotency_key,
            "recipient": recipient,
            "body": body,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivery_status": "not_sent",
        }
        with self.path.open("a", encoding="utf-8") as outbox:
            outbox.write(json.dumps(record, sort_keys=True) + "\n")
        return message_id, True

    def deliver_pending(
        self, adapter: MessagingAdapter, allowed_recipient: str | None = None
    ) -> int:
        """Submit pending records, persisting a crash-visible sending state."""
        if not self.path.exists():
            return 0
        records = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
        ]
        delivered = 0
        for index, record in enumerate(records):
            if record.get("delivery_status") != "not_sent":
                continue
            if allowed_recipient and record.get("recipient") != allowed_recipient:
                raise ValueError(
                    "Pending SMS recipient does not match the configured recipient."
                )
            record["delivery_status"] = "sending"
            self._replace_records(records)
            try:
                provider_id = adapter.send(record["recipient"], record["body"])
            except Exception:
                record["delivery_status"] = "not_sent"
                self._replace_records(records)
                raise
            record.update(
                {
                    "delivery_status": "submitted",
                    "provider_message_id": provider_id,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._replace_records(records)
            delivered += 1
        return delivered

    def _replace_records(self, records: list[dict[str, object]]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(temporary, self.path)
