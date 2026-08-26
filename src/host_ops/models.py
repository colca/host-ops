from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ActionStatus(StrEnum):
    READY = "ready"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ProposedAction:
    event_id: str
    type: str
    summary: str
    payload: dict[str, Any]
    idempotency_key: str
    execute_at: datetime | None = None
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utc_now)

    @property
    def initial_status(self) -> ActionStatus:
        if self.requires_approval:
            return ActionStatus.PENDING_APPROVAL
        return ActionStatus.READY


@dataclass(frozen=True)
class StoredAction:
    action: ProposedAction
    status: ActionStatus

