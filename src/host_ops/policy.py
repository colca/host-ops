from __future__ import annotations

from dataclasses import replace

from .models import ProposedAction, RiskLevel


class SafetyPolicy:
    """Central policy for actions that may change money or guest commitments."""

    ALWAYS_APPROVE = {
        "cleaner_payment",
        "refund",
        "rate_change",
        "discount_change",
        "reservation_exception",
        "safety_response",
    }

    def apply(self, action: ProposedAction) -> ProposedAction:
        if action.type in self.ALWAYS_APPROVE:
            return replace(
                action,
                requires_approval=True,
                risk=RiskLevel.HIGH,
            )

        if action.type == "guest_message" and action.payload.get("confidence", 1.0) < 0.9:
            return replace(
                action,
                requires_approval=True,
                risk=RiskLevel.MEDIUM,
            )

        return action
