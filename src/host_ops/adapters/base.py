from __future__ import annotations

from typing import Any, Protocol


class MessagingAdapter(Protocol):
    def send(self, recipient: str, body: str) -> str: ...


class PricingAdapter(Protocol):
    def set_rates(self, listing_id: str, rates: dict[str, int]) -> str: ...


class PricingSignalAdapter(Protocol):
    """Supplies normalized market comparables and nearby demand signals."""

    def fetch_snapshot(self, listing_id: str) -> dict[str, Any]: ...


class PaymentAdapter(Protocol):
    """Creates a payment request; it must not bypass human confirmation."""

    def prepare(self, recipient_id: str, amount: float, memo: str) -> dict[str, Any]: ...
