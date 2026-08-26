from __future__ import annotations

from typing import Any, Protocol


class MessagingAdapter(Protocol):
    def send(self, recipient: str, body: str) -> str: ...


class PricingAdapter(Protocol):
    def set_rates(self, listing_id: str, rates: dict[str, int]) -> str: ...


class PaymentAdapter(Protocol):
    """Creates a payment request; it must not bypass human confirmation."""

    def prepare(self, recipient_id: str, amount: float, memo: str) -> dict[str, Any]: ...

