from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..models import ActionStatus, Event, ProposedAction


def _document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _namespace(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.casefold()).strip("_")
    if not cleaned:
        raise ValueError("Firestore namespace cannot be empty")
    return cleaned


class FirestoreStore:
    """Persistent Host Ops state for disposable cloud workers."""

    def __init__(self, client: Any, namespace: str) -> None:
        prefix = f"host_ops_{_namespace(namespace)}"
        self.client = client
        self.events = client.collection(f"{prefix}_events")
        self.actions = client.collection(f"{prefix}_actions")
        self.action_keys = client.collection(f"{prefix}_action_keys")

    def initialize(self) -> None:
        return None

    def save_event_and_actions(
        self, event: Event, actions: list[ProposedAction]
    ) -> int:
        from google.cloud import firestore

        transaction = self.client.transaction()

        @firestore.transactional
        def save(transaction: Any) -> int:
            event_ref = self.events.document(event.id)
            event_exists = event_ref.get(transaction=transaction).exists
            key_refs = [
                self.action_keys.document(_document_id(action.idempotency_key))
                for action in actions
            ]
            existing_keys = [
                reference.get(transaction=transaction).exists
                for reference in key_refs
            ]
            if not event_exists:
                transaction.create(
                    event_ref,
                    {
                        "id": event.id,
                        "type": event.type,
                        "occurred_at": event.occurred_at.isoformat(),
                        "payload": event.payload,
                    },
                )
            inserted = 0
            for action, key_ref, key_exists in zip(
                actions, key_refs, existing_keys, strict=True
            ):
                if key_exists:
                    continue
                action_ref = self.actions.document(action.id)
                transaction.create(
                    action_ref,
                    {
                        "id": action.id,
                        "event_id": action.event_id,
                        "type": action.type,
                        "summary": action.summary,
                        "status": action.initial_status.value,
                        "risk": action.risk.value,
                        "requires_approval": action.requires_approval,
                        "execute_at": (
                            action.execute_at.isoformat() if action.execute_at else None
                        ),
                        "payload": action.payload,
                        "idempotency_key": action.idempotency_key,
                        "created_at": action.created_at.isoformat(),
                    },
                )
                transaction.create(
                    key_ref,
                    {"action_id": action.id, "idempotency_key": action.idempotency_key},
                )
                inserted += 1
            return inserted

        return int(save(transaction))

    def list_actions(self) -> list[dict[str, Any]]:
        rows = [snapshot.to_dict() for snapshot in self.actions.stream()]
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("execute_at") or row.get("created_at")),
                str(row.get("created_at")),
            ),
        )

    def approve(self, action_id: str) -> bool:
        from google.cloud import firestore

        transaction = self.client.transaction()
        action_ref = self.actions.document(action_id)

        @firestore.transactional
        def approve(transaction: Any) -> bool:
            snapshot = action_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            if snapshot.to_dict().get("status") != ActionStatus.PENDING_APPROVAL.value:
                return False
            transaction.update(action_ref, {"status": ActionStatus.APPROVED.value})
            return True

        return bool(approve(transaction))

    def list_cleaner_reminder_actions(
        self, now: datetime
    ) -> list[dict[str, Any]]:
        del now
        allowed = {
            ActionStatus.READY.value,
            ActionStatus.APPROVED.value,
            ActionStatus.EXECUTED.value,
        }
        rows: list[dict[str, Any]] = []
        for snapshot in self.actions.stream():
            action = snapshot.to_dict()
            if action.get("type") != "cleaner_sms" or action.get("status") not in allowed:
                continue
            event = self.events.document(str(action["event_id"])).get()
            event_payload = event.to_dict().get("payload", {}) if event.exists else {}
            rows.append(
                {
                    **action,
                    "payload": json.dumps(action.get("payload", {}), sort_keys=True),
                    "event_payload": json.dumps(event_payload, sort_keys=True),
                }
            )
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("execute_at") or ""),
                str(row.get("created_at") or ""),
            ),
        )

    def claim_due_actions(
        self, action_type: str, now: datetime, limit: int = 100
    ) -> list[dict[str, Any]]:
        del action_type, now, limit
        raise NotImplementedError("Cloud action claiming is not used by cleaner reminders")

    def finish_action(self, action_id: str) -> bool:
        return self._change_processing_status(action_id, ActionStatus.EXECUTED.value)

    def release_action(self, action_id: str, previous_status: str) -> bool:
        if previous_status not in {
            ActionStatus.READY.value,
            ActionStatus.APPROVED.value,
        }:
            raise ValueError("Cannot release an action to an unsafe status")
        return self._change_processing_status(action_id, previous_status)

    def _change_processing_status(self, action_id: str, status: str) -> bool:
        snapshot = self.actions.document(action_id).get()
        if not snapshot.exists:
            return False
        if snapshot.to_dict().get("status") != ActionStatus.PROCESSING.value:
            return False
        snapshot.reference.update({"status": status})
        return True


class FirestoreOutboxMessagingAdapter:
    """Durable, deduplicated SMS outbox for Cloud Run Jobs."""

    def __init__(self, client: Any, namespace: str) -> None:
        prefix = f"host_ops_{_namespace(namespace)}"
        self.client = client
        self.messages = client.collection(f"{prefix}_sms_outbox")

    def queue_once(
        self, recipient: str, body: str, idempotency_key: str
    ) -> tuple[str, bool]:
        from google.cloud import firestore

        message_id = _document_id(idempotency_key)
        reference = self.messages.document(message_id)
        transaction = self.client.transaction()

        @firestore.transactional
        def queue(transaction: Any) -> bool:
            if reference.get(transaction=transaction).exists:
                return False
            transaction.create(
                reference,
                {
                    "id": message_id,
                    "idempotency_key": idempotency_key,
                    "recipient": recipient,
                    "body": body,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "delivery_status": "not_sent",
                },
            )
            return True

        return message_id, bool(queue(transaction))

    def deliver_pending(
        self, adapter: Any, allowed_recipients: set[str] | None = None
    ) -> int:
        from google.cloud import firestore

        delivered = 0
        for snapshot in self.messages.stream():
            record = snapshot.to_dict()
            if record.get("delivery_status") != "not_sent":
                continue
            if allowed_recipients and record.get("recipient") not in allowed_recipients:
                raise ValueError(
                    "Pending SMS recipient does not match the configured recipient."
                )
            transaction = self.client.transaction()

            @firestore.transactional
            def claim(transaction: Any) -> bool:
                current = snapshot.reference.get(transaction=transaction)
                if (
                    not current.exists
                    or current.to_dict().get("delivery_status") != "not_sent"
                ):
                    return False
                transaction.update(
                    snapshot.reference,
                    {
                    "delivery_status": "sending",
                    "sending_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return True

            if not claim(transaction):
                continue
            try:
                provider_id = adapter.send(record["recipient"], record["body"])
            except Exception:
                snapshot.reference.update({"delivery_status": "not_sent"})
                raise
            snapshot.reference.update(
                {
                    "delivery_status": "submitted",
                    "provider_message_id": provider_id,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            delivered += 1
        return delivered
