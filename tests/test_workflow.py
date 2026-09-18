from datetime import datetime, timezone
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from email.message import Message
import unittest
from unittest.mock import patch

from host_ops.adapters.ical import parse_ical
from host_ops.adapters.ical_poll import (
    CalendarPollError,
    calendar_url_from_environment,
    fetch_private_ical,
)
from host_ops.adapters.outbox import FileOutboxMessagingAdapter
from host_ops.adapters.twilio import TwilioMessagingAdapter
from host_ops.config import AppConfig, PricingSettings, load_config
from host_ops.models import ActionStatus, Event, ProposedAction
from host_ops.pricing import recommend_nightly_rate
from host_ops.runner import run_due_cleaner_actions
from host_ops.store import SqliteStore
from host_ops.workflow import WorkflowEngine


class WorkflowEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WorkflowEngine()

    def test_reservation_creates_routine_actions_without_approval(self) -> None:
        event = Event(
            type="reservation_confirmed",
            payload={
                "reservation_id": "R1",
                "guest_first_name": "Sam",
                "check_in": "2026-09-01T16:00:00-07:00",
                "check_out": "2026-09-04T11:00:00-07:00",
            },
            occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        actions = self.engine.handle(event)

        self.assertEqual(4, len(actions))
        self.assertTrue(all(not action.requires_approval for action in actions))
        self.assertEqual(
            {"guest_message", "cleaner_sms"},
            {action.type for action in actions},
        )
        cleaner_action = next(action for action in actions if action.type == "cleaner_sms")
        self.assertEqual(
            datetime(2026, 8, 2, tzinfo=timezone.utc), cleaner_action.execute_at
        )

    def test_last_minute_booking_notifies_cleaner_immediately(self) -> None:
        detected_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        event = Event(
            type="reservation_confirmed",
            payload={
                "reservation_id": "R-LAST-MINUTE",
                "guest_first_name": "Sam",
                "check_in": "2026-08-01T12:00:00+00:00",
                "check_out": "2026-08-03T11:00:00+00:00",
            },
            occurred_at=detected_at,
        )

        actions = self.engine.handle(event)
        cleaner_action = next(action for action in actions if action.type == "cleaner_sms")

        self.assertEqual(detected_at, cleaner_action.execute_at)

    def test_money_and_rate_changes_always_need_approval(self) -> None:
        cleaning = Event(
            type="cleaning_completed",
            payload={"turnover_id": "T1", "amount": 100.0},
        )
        stadium = Event(
            type="stadium_event_found",
            payload={
                "external_event_id": "E1",
                "name": "Game",
                "proposed_percent_change": 20,
            },
        )

        actions = self.engine.handle(cleaning) + self.engine.handle(stadium)

        self.assertTrue(all(action.requires_approval for action in actions))
        self.assertTrue(
            all(action.initial_status == ActionStatus.PENDING_APPROVAL for action in actions)
        )

    def test_regular_night_uses_market_comparable_median(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-12",
                "current_rate": 200,
                "comparable_rates": [180, 210, 220, 230, 300],
                "demand_signals": [],
            },
            PricingSettings(),
        )

        self.assertEqual(220, recommendation.market_baseline)
        self.assertEqual(220, recommendation.recommended_rate)
        self.assertEqual(0, recommendation.event_premium_percent)
        self.assertEqual("high", recommendation.comparable_confidence)

    def test_three_same_size_comparables_are_accepted_with_limited_confidence(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-10-21",
                "current_rate": 220,
                "comparable_rates": [200, 220, 240],
                "demand_signals": [],
            },
            PricingSettings(),
        )

        self.assertEqual(220, recommendation.recommended_rate)
        self.assertEqual("same_size", recommendation.comparable_method)
        self.assertEqual("limited", recommendation.comparable_confidence)

    def test_one_bedroom_fallback_requires_market_derived_size_adjustment(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-10-22",
                "current_rate": 240,
                "comparable_rates": [235, 245],
                "one_bedroom_comparable_rates": [180, 200, 220],
                "one_to_two_bedroom_adjustment": {
                    "premium_percent": 20,
                    "evidence_count": 6,
                },
                "demand_signals": [],
            },
            PricingSettings(),
        )

        self.assertEqual(240, recommendation.market_baseline)
        self.assertEqual("one_bedroom_market_adjusted", recommendation.comparable_method)
        self.assertEqual(20, recommendation.bedroom_premium_percent)
        self.assertEqual("limited", recommendation.comparable_confidence)

    def test_nearby_events_add_bounded_distance_weighted_premium(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-13",
                "current_rate": 250,
                "comparable_rates": [240, 245, 250, 255, 260],
                "demand_signals": [
                    {
                        "id": "game-1",
                        "name": "Example game",
                        "category": "sports",
                        "distance_miles": 0.5,
                        "importance": 1,
                    },
                    {
                        "id": "conference-1",
                        "name": "Example conference",
                        "category": "conference",
                        "distance_miles": 1.0,
                        "importance": 1,
                    },
                ],
            },
            PricingSettings(
                maximum_event_premium_percent=40,
                event_category_premiums={
                    "sports": 30,
                    "conference": 25,
                    "other": 0,
                },
            ),
        )

        self.assertEqual(40, recommendation.event_premium_percent)
        self.assertEqual(350, recommendation.recommended_rate)
        self.assertEqual(2, len(recommendation.demand_signals))

    def test_pricing_snapshot_creates_approval_gated_action_per_night(self) -> None:
        event = Event(
            type="pricing_snapshot_received",
            payload={
                "snapshot_id": "snapshot-1",
                "listing_id": "listing-1",
                "nights": [
                    {
                        "date": "2026-09-12",
                        "current_rate": 200,
                        "comparable_rates": [190, 200, 210, 220, 230],
                        "demand_signals": [],
                    },
                    {
                        "date": "2026-09-13",
                        "current_rate": 220,
                        "comparable_rates": [220, 230, 240, 250, 260],
                        "demand_signals": [],
                    },
                ],
            },
        )

        actions = self.engine.handle(event)

        self.assertEqual(2, len(actions))
        self.assertTrue(all(action.type == "rate_change" for action in actions))
        self.assertTrue(all(action.requires_approval for action in actions))
        self.assertTrue(
            all(action.initial_status == ActionStatus.PENDING_APPROVAL for action in actions)
        )
        self.assertEqual(210, actions[0].payload["market_baseline"])

    def test_pricing_bounds_large_market_move_and_ignores_distant_event(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-14",
                "current_rate": 100,
                "comparable_rates": [280, 300, 320, 340, 360],
                "demand_signals": [
                    {
                        "id": "far-event",
                        "name": "Far-away event",
                        "category": "concert",
                        "distance_miles": 10,
                        "importance": 1,
                    }
                ],
            },
            PricingSettings(maximum_recommendation_change_percent=25),
        )

        self.assertEqual(0, recommendation.event_premium_percent)
        self.assertEqual(125, recommendation.recommended_rate)

    def test_verified_venue_floor_wins_after_market_recommendation(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-20",
                "current_rate": 300,
                "comparable_rates": [280, 290, 300, 310, 320],
                "demand_signals": [{
                    "id": "game-1",
                    "name": "Example stadium event",
                    "venue_id": "levis_stadium",
                    "category": "sports",
                    "distance_miles": 0.2,
                    "importance": 1,
                }],
            },
            PricingSettings(minimum_rate_by_venue={"levis_stadium": 485}),
        )

        self.assertEqual(485, recommendation.recommended_rate)
        self.assertEqual(485, recommendation.venue_minimum_rate)

    def test_pricing_applies_configured_last_minute_discount(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-13",
                "as_of_date": "2026-09-09",
                "current_rate": 217,
                "comparable_rates": [197, 207, 217, 227, 237],
                "demand_signals": [],
            },
            PricingSettings(),
        )

        self.assertEqual(20, recommendation.last_minute_discount_percent)
        self.assertEqual(217, recommendation.recommended_rate)

    def test_last_minute_discount_creates_separate_discount_action(self) -> None:
        event = Event(
            type="pricing_snapshot_received",
            payload={
                "snapshot_id": "snapshot-promo",
                "listing_id": "listing-1",
                "nights": [{
                    "date": "2026-09-13",
                    "as_of_date": "2026-09-09",
                    "current_rate": 217,
                    "comparable_rates": [197, 207, 217, 227, 237],
                    "demand_signals": [],
                }],
            },
        )

        actions = self.engine.handle(event)

        self.assertEqual(["rate_change", "discount_change"], [a.type for a in actions])
        self.assertEqual(20, actions[1].payload["discount_percent"])
        self.assertTrue(all(action.requires_approval for action in actions))

    def test_pricing_does_not_discount_outside_last_minute_window(self) -> None:
        recommendation = recommend_nightly_rate(
            {
                "date": "2026-09-15",
                "as_of_date": "2026-09-09",
                "current_rate": 217,
                "comparable_rates": [197, 207, 217, 227, 237],
                "demand_signals": [],
            },
            PricingSettings(),
        )

        self.assertEqual(0, recommendation.last_minute_discount_percent)
        self.assertEqual(217, recommendation.recommended_rate)

    def test_low_confidence_guest_answer_needs_approval(self) -> None:
        event = Event(
            type="guest_question_received",
            payload={
                "message_id": "M1",
                "question": "Can I have a refund?",
                "draft": "Let me check that for you.",
                "confidence": 0.6,
            },
        )

        action = self.engine.handle(event)[0]

        self.assertTrue(action.requires_approval)

    def test_store_is_idempotent_and_can_approve(self) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteStore(Path(directory) / "test.db")
            store.initialize()
            event = Event(
                type="cleaning_completed",
                payload={"turnover_id": "T1", "amount": 100.0},
            )
            actions = self.engine.handle(event)

            self.assertEqual(1, store.save_event_and_actions(event, actions))
            self.assertEqual(0, store.save_event_and_actions(event, actions))
            self.assertTrue(store.approve(actions[0].id))
            self.assertFalse(store.approve(actions[0].id))

    def test_cleaner_extras_are_itemized_and_payment_needs_approval(self) -> None:
        event = Event(
            type="cleaner_work_reported",
            payload={
                "turnover_id": "T2",
                "extras": [
                    {
                        "kind": "trash_cans_out_and_in",
                        "notes": "Tuesday collection",
                    },
                    "supply_restock",
                ],
            },
        )

        action = self.engine.handle(event)[0]

        self.assertEqual(180, action.payload["amount"])
        self.assertEqual(3, len(action.payload["line_items"]))
        self.assertTrue(action.requires_approval)

    def test_private_config_contains_current_fee_schedule(self) -> None:
        config = load_config(Path(__file__).parents[1] / "config" / "property.json")

        self.assertIsInstance(config, AppConfig)
        self.assertEqual(160, config.cleaning.base_fee)
        self.assertEqual(24, config.cleaning.notification_delay_hours)
        self.assertEqual("English", config.cleaning.message_language)
        self.assertEqual(
            {"trash_cans_out_and_in", "supply_restock", "change_lock_battery"},
            set(config.cleaning.extra_tasks),
        )
        self.assertTrue(
            all(task.amount == 10 for task in config.cleaning.extra_tasks.values())
        )
        self.assertEqual(
            "CLEANER_PHONE_NUMBER",
            config.cleaner_messaging.phone_environment_variable,
        )
        self.assertEqual("America/Los_Angeles", config.property.timezone)
        self.assertEqual(0, config.cleaner_messaging.weekly_digest_weekday)
        self.assertEqual(9, config.cleaner_messaging.reminder_hour)

    def test_airbnb_calendar_stay_creates_cleaner_action_only(self) -> None:
        content = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:airbnb-demo-1
DTSTART;VALUE=DATE:20260910
DTEND;VALUE=DATE:20260913
SUMMARY:Reserved
END:VEVENT
END:VCALENDAR
"""
        stays = parse_ical(content)

        self.assertEqual(1, len(stays))
        actions = self.engine.handle(stays[0].to_event())
        self.assertEqual(1, len(actions))
        self.assertEqual("cleaner_sms", actions[0].type)
        work_order = actions[0].payload["work_order"]
        self.assertEqual(160, work_order["base_fee"])
        self.assertTrue(work_order["payment_requires_host_approval"])
        self.assertEqual(3, len(work_order["available_extras"]))

    def test_airbnb_calendar_ignores_unavailable_and_cancelled_periods(self) -> None:
        content = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:owner-block-1
DTSTART;VALUE=DATE:20260910
DTEND;VALUE=DATE:20260913
SUMMARY:Airbnb (Not available)
END:VEVENT
BEGIN:VEVENT
UID:cancelled-reservation-1
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261013
SUMMARY:Reserved
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""

        self.assertEqual([], parse_ical(content))

    def test_private_calendar_url_is_read_only_from_environment(self) -> None:
        with patch.dict(
            "os.environ", {"TEST_AIRBNB_ICAL_URL": "https://example.test/private.ics"}
        ):
            self.assertEqual(
                "https://example.test/private.ics",
                calendar_url_from_environment("TEST_AIRBNB_ICAL_URL"),
            )

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CalendarPollError) as context:
                calendar_url_from_environment("TEST_AIRBNB_ICAL_URL")
        self.assertNotIn("https://", str(context.exception))

    def test_private_calendar_fetch_does_not_leak_url_in_errors(self) -> None:
        private_url = "https://example.test/calendar.ics?token=top-secret"

        def failing_opener(*args: object, **kwargs: object) -> object:
            raise OSError("network unavailable")

        with self.assertRaises(CalendarPollError) as context:
            fetch_private_ical(private_url, opener=failing_opener)

        self.assertNotIn(private_url, str(context.exception))
        self.assertNotIn("top-secret", str(context.exception))

    def test_private_calendar_fetch_accepts_ical_content(self) -> None:
        class Response:
            def __init__(self) -> None:
                self.headers = Message()
                self.headers["Content-Type"] = "text/calendar; charset=utf-8"

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def geturl(self) -> str:
                return "https://example.test/calendar.ics"

            def read(self, size: int) -> bytes:
                return b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"

        content = fetch_private_ical(
            "https://example.test/calendar.ics",
            opener=lambda *args, **kwargs: Response(),
        )

        self.assertIn("BEGIN:VCALENDAR", content)

    def test_five_day_and_day_before_reminders_include_all_confirmed_dates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteStore(root / "test.db")
            store.initialize()
            event = Event(
                type="calendar_stay_detected",
                payload={
                    "check_in": "2026-08-27T16:00:00-07:00",
                    "check_out": "2026-09-01T11:00:00-07:00",
                },
            )
            action = ProposedAction(
                event_id=event.id,
                type="cleaner_sms",
                summary="Cleaner work order",
                payload={
                    "check_out": "2026-09-01T11:00:00+00:00",
                    "work_order": {
                        "base_fee": 160,
                        "available_extras": [
                            {"description": "Restock supplies", "amount": 10}
                        ],
                    },
                },
                # A confirmed stay appears in the schedule even before the old
                # booking-detection delay has elapsed.
                execute_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
                idempotency_key="due-cleaner-action",
            )
            store.save_event_and_actions(event, [action])
            later_event = Event(
                type="calendar_stay_detected",
                payload={
                    "check_in": "2026-09-07T16:00:00-07:00",
                    "check_out": "2026-09-10T11:00:00-07:00",
                },
            )
            later_action = ProposedAction(
                event_id=later_event.id,
                type="cleaner_sms",
                summary="Later cleaner work order",
                payload={"check_out": "2026-09-10T11:00:00-07:00"},
                execute_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                idempotency_key="later-cleaner-action",
            )
            store.save_event_and_actions(later_event, [later_action])
            outbox = FileOutboxMessagingAdapter(root / "outbox.jsonl")

            first = run_due_cleaner_actions(
                store, outbox, now=datetime(2026, 8, 22, 16, tzinfo=timezone.utc)
            )
            second = run_due_cleaner_actions(
                store, outbox, now=datetime(2026, 8, 22, 16, tzinfo=timezone.utc)
            )
            day_before = run_due_cleaner_actions(
                store, outbox, now=datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
            )

            self.assertEqual((2, 1), first)
            self.assertEqual((2, 0), second)
            self.assertEqual((2, 1), day_before)
            records = (root / "outbox.jsonl").read_text().splitlines()
            self.assertEqual(2, len(records))
            for record in records:
                self.assertIn("September 1, 2026", record)
                self.assertIn("September 10, 2026", record)
                self.assertIn("friendly reminder", record)
                self.assertIn("COYU | Host Ops cleaner scheduling", record)
                self.assertIn("Reply STOP", record)
            self.assertIn("checks in in 5 days", records[0])
            self.assertIn("scheduled for tomorrow", records[1])

    def test_runner_does_not_claim_future_or_pending_approval_actions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteStore(root / "test.db")
            store.initialize()
            event = Event(type="test", payload={})
            future = ProposedAction(
                event_id=event.id,
                type="cleaner_sms",
                summary="Future cleaner work",
                payload={},
                execute_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                idempotency_key="future-cleaner-action",
            )
            gated = ProposedAction(
                event_id=event.id,
                type="cleaner_sms",
                summary="Gated cleaner work",
                payload={},
                execute_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                idempotency_key="gated-cleaner-action",
                requires_approval=True,
            )
            store.save_event_and_actions(event, [future, gated])

            result = run_due_cleaner_actions(
                store,
                FileOutboxMessagingAdapter(root / "outbox.jsonl"),
                now=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )

            self.assertEqual((0, 0), result)
            self.assertFalse((root / "outbox.jsonl").exists())

    def test_twilio_adapter_and_outbox_record_provider_acceptance(self) -> None:
        requests: list[object] = []

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"sid":"SM-test-message"}'

        def opener(request: object, **kwargs: object) -> Response:
            requests.append(request)
            return Response()

        with TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.jsonl"
            outbox = FileOutboxMessagingAdapter(path)
            outbox.queue_once("+15555550100", "Test reminder", "test-reminder")
            adapter = TwilioMessagingAdapter(
                "AC-test", "private-test-token", "+15555550101", opener=opener
            )

            delivered = outbox.deliver_pending(adapter)
            second = outbox.deliver_pending(adapter)
            record = json.loads(path.read_text().strip())

            self.assertEqual(1, delivered)
            self.assertEqual(0, second)
            self.assertEqual(1, len(requests))
            self.assertEqual("submitted", record["delivery_status"])
            self.assertEqual("SM-test-message", record["provider_message_id"])

    def test_outbox_blocks_a_recipient_other_than_the_configured_contact(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.jsonl"
            outbox = FileOutboxMessagingAdapter(path)
            outbox.queue_once("+15555550199", "Test reminder", "test-reminder")
            adapter = TwilioMessagingAdapter(
                "AC-test", "private-test-token", "+15555550101"
            )

            with self.assertRaisesRegex(ValueError, "configured recipient"):
                outbox.deliver_pending(
                    adapter, allowed_recipient="+15555550100"
                )

            record = json.loads(path.read_text().strip())
            self.assertEqual("not_sent", record["delivery_status"])



if __name__ == "__main__":
    unittest.main()
