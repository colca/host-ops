from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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

