from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from .config import CleaningSettings, PricingSettings
from .models import Event, ProposedAction, RiskLevel
from .policy import SafetyPolicy
from .pricing import recommend_nightly_rate


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class WorkflowEngine:
    def __init__(
        self,
        policy: SafetyPolicy | None = None,
        cleaning: CleaningSettings | None = None,
        pricing: PricingSettings | None = None,
    ) -> None:
        self.policy = policy or SafetyPolicy()
        self.cleaning = cleaning or CleaningSettings()
        self.pricing = pricing or PricingSettings()
        self._handlers: dict[str, Callable[[Event], list[ProposedAction]]] = {
            "reservation_confirmed": self._reservation_confirmed,
            "calendar_stay_detected": self._calendar_stay_detected,
            "cleaning_completed": self._cleaning_completed,
            "cleaner_work_reported": self._cleaner_work_reported,
            "stadium_event_found": self._stadium_event_found,
            "pricing_snapshot_received": self._pricing_snapshot_received,
            "guest_question_received": self._guest_question_received,
        }

    def handle(self, event: Event) -> list[ProposedAction]:
        handler = self._handlers.get(event.type)
        if handler is None:
            return []
        return [self.policy.apply(action) for action in handler(event)]

    def _reservation_confirmed(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        reservation_id = required(data, "reservation_id")
        check_in = parse_time(required(data, "check_in"))
        check_out = parse_time(required(data, "check_out"))
        guest = required(data, "guest_first_name")

        def action(
            action_type: str,
            suffix: str,
            summary: str,
            execute_at: datetime,
            payload: dict[str, Any],
        ) -> ProposedAction:
            return ProposedAction(
                event_id=event.id,
                type=action_type,
                summary=summary,
                payload={"reservation_id": reservation_id, **payload},
                execute_at=execute_at,
                idempotency_key=f"{reservation_id}:{suffix}",
            )

        return [
            action(
                "guest_message",
                "checkin-instructions",
                f"Send check-in instructions to {guest}",
                check_in - timedelta(days=2),
                {"template": "checkin_instructions", "guest_first_name": guest},
            ),
            action(
                "guest_message",
                "post-checkin",
                f"Check in with {guest} after arrival",
                check_in + timedelta(hours=3),
                {"template": "post_checkin", "guest_first_name": guest},
            ),
            action(
                "guest_message",
                "checkout-reminder",
                f"Send checkout reminder to {guest}",
                check_out - timedelta(hours=18),
                {"template": "checkout_reminder", "guest_first_name": guest},
            ),
            action(
                "cleaner_sms",
                "cleaner-schedule",
                f"Ask cleaner to confirm turnover after {guest}'s stay",
                self._cleaner_notification_time(event.occurred_at, check_in),
                {
                    "template": "cleaner_schedule",
                    "language": self.cleaning.message_language,
                    "check_out": check_out.isoformat(),
                    "next_check_in": data.get("next_check_in"),
                },
            ),
        ]

    def _calendar_stay_detected(self, event: Event) -> list[ProposedAction]:
        """Create cleaner work from an Airbnb iCal stay.

        Guest messaging remains in Airbnb native scheduled messages because an
        exported calendar is not a guest-messaging API.
        """
        data = event.payload
        stay_id = required(data, "stay_id")
        check_out = parse_time(required(data, "check_out"))
        return [
            ProposedAction(
                event_id=event.id,
                type="cleaner_sms",
                summary=f"Ask cleaner to confirm turnover on {check_out.date()}",
                payload={
                    "stay_id": stay_id,
                    "template": "cleaner_schedule",
                    "language": self.cleaning.message_language,
                    "check_out": check_out.isoformat(),
                    "work_order": {
                        "base_fee": self.cleaning.base_fee,
                        "available_extras": [
                            {
                                "kind": kind,
                                "description": fee.description,
                                "amount": fee.amount,
                            }
                            for kind, fee in self.cleaning.extra_tasks.items()
                        ],
                        "payment_requires_host_approval": True,
                    },
                },
                execute_at=self._cleaner_notification_time(
                    event.occurred_at,
                    parse_time(required(data, "check_in")),
                ),
                idempotency_key=f"{stay_id}:cleaner-schedule",
            )
        ]

    def _cleaning_completed(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        turnover_id = required(data, "turnover_id")
        amount = required(data, "amount")
        return [
            ProposedAction(
                event_id=event.id,
                type="cleaner_payment",
                summary=f"Approve cleaner payment of ${amount:.2f}",
                payload={
                    "turnover_id": turnover_id,
                    "amount": amount,
                    "photo_review_status": data.get("photo_review_status", "unknown"),
                },
                idempotency_key=f"{turnover_id}:cleaner-payment",
                risk=RiskLevel.HIGH,
            )
        ]

    def _cleaner_work_reported(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        turnover_id = required(data, "turnover_id")
        base_fee = self.cleaning.base_fee
        extras = data.get("extras", [])
        if not isinstance(extras, list):
            raise ValueError("extras must be a list")

        line_items: list[dict[str, Any]] = [
            {"kind": "turnover", "description": "Flat turnover fee", "amount": base_fee}
        ]
        for index, extra in enumerate(extras):
            if isinstance(extra, str):
                kind = extra
                notes = None
            elif isinstance(extra, dict):
                kind = required(extra, "kind")
                notes = extra.get("notes")
            else:
                raise ValueError(f"extras[{index}] must be a task name or object")
            fee = self.cleaning.extra_tasks.get(kind)
            if fee is None:
                raise ValueError(f"Unknown cleaner extra task: {kind}")
            line_items.append(
                {
                    "kind": kind,
                    "description": fee.description,
                    "amount": fee.amount,
                    "notes": notes,
                }
            )

        total = round(sum(item["amount"] for item in line_items), 2)
        extra_summary = f" with {len(extras)} extra task(s)" if extras else ""
        return [
            ProposedAction(
                event_id=event.id,
                type="cleaner_payment",
                summary=f"Approve cleaner payment of ${total:.2f}{extra_summary}",
                payload={
                    "turnover_id": turnover_id,
                    "line_items": line_items,
                    "amount": total,
                    "photo_review_status": data.get("photo_review_status", "unknown"),
                },
                idempotency_key=f"{turnover_id}:cleaner-payment",
                risk=RiskLevel.HIGH,
            )
        ]

    def _cleaner_notification_time(
        self, booking_detected_at: datetime, check_in: datetime
    ) -> datetime:
        delayed = booking_detected_at + timedelta(
            hours=self.cleaning.notification_delay_hours
        )
        if check_in < delayed:
            return booking_detected_at
        return delayed

    def _stadium_event_found(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        event_id = required(data, "external_event_id")
        proposed_change = required(data, "proposed_percent_change")
        return [
            ProposedAction(
                event_id=event.id,
                type="rate_change",
                summary=(
                    f"Review {proposed_change:+.0f}% rate change for "
                    f"{required(data, 'name')}"
                ),
                payload=data,
                idempotency_key=f"event:{event_id}:rate-change",
                risk=RiskLevel.HIGH,
            )
        ]

    def _pricing_snapshot_received(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        snapshot_id = required(data, "snapshot_id")
        listing_id = required(data, "listing_id")
        nights = required(data, "nights")
        if not isinstance(nights, list) or not nights:
            raise ValueError("nights must be a non-empty list")

        actions: list[ProposedAction] = []
        for nightly_input in nights:
            if not isinstance(nightly_input, dict):
                raise ValueError("each night must be an object")
            recommendation = recommend_nightly_rate(nightly_input, self.pricing)
            change_percent = round(
                (recommendation.recommended_rate / recommendation.current_rate - 1.0)
                * 100.0,
                2,
            )
            actions.append(
                ProposedAction(
                    event_id=event.id,
                    type="rate_change",
                    summary=(
                        f"Review ${recommendation.recommended_rate} nightly rate for "
                        f"{recommendation.date} ({change_percent:+.1f}%)"
                    ),
                    payload={
                        "listing_id": listing_id,
                        "date": recommendation.date,
                        "current_rate": recommendation.current_rate,
                        "recommended_rate": recommendation.recommended_rate,
                        "change_percent": change_percent,
                        "market_baseline": recommendation.market_baseline,
                        "comparable_count": recommendation.comparable_count,
                        "comparable_method": recommendation.comparable_method,
                        "comparable_confidence": recommendation.comparable_confidence,
                        "bedroom_premium_percent": (
                            recommendation.bedroom_premium_percent
                        ),
                        "event_premium_percent": recommendation.event_premium_percent,
                        "venue_minimum_rate": recommendation.venue_minimum_rate,
                        "demand_signals": list(recommendation.demand_signals),
                        "explanation": (
                            "Comparable-rate median plus bounded nearby-event premium; "
                            "subject to configured floor, ceiling, and change cap."
                        ),
                    },
                    idempotency_key=(
                        f"pricing:{snapshot_id}:{listing_id}:{recommendation.date}"
                    ),
                    risk=RiskLevel.HIGH,
                )
            )
            if recommendation.last_minute_discount_percent:
                actions.append(
                    ProposedAction(
                        event_id=event.id,
                        type="discount_change",
                        summary=(
                            f"Review {recommendation.last_minute_discount_percent:.0f}% "
                            f"native last-minute discount for {recommendation.date}"
                        ),
                        payload={
                            "listing_id": listing_id,
                            "date": recommendation.date,
                            "discount_type": "last_minute",
                            "discount_percent": (
                                recommendation.last_minute_discount_percent
                            ),
                            "base_rate": recommendation.recommended_rate,
                        },
                        idempotency_key=(
                            f"pricing:{snapshot_id}:{listing_id}:"
                            f"{recommendation.date}:promotion"
                        ),
                        risk=RiskLevel.HIGH,
                    )
                )
        return actions

    def _guest_question_received(self, event: Event) -> list[ProposedAction]:
        data = event.payload
        message_id = required(data, "message_id")
        confidence = float(data.get("confidence", 0.0))
        return [
            ProposedAction(
                event_id=event.id,
                type="guest_message",
                summary=f"Answer guest question: {required(data, 'question')}",
                payload={
                    "message_id": message_id,
                    "draft": required(data, "draft"),
                    "category": data.get("category", "unknown"),
                    "confidence": confidence,
                },
                idempotency_key=f"message:{message_id}:reply",
                risk=RiskLevel.LOW,
            )
        ]


def required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing required event field: {key}")
    return data[key]
