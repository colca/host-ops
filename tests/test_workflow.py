from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from host_ops.adapters.ical import parse_ical
from host_ops.config import AppConfig, load_config
from host_ops.models import ActionStatus, Event
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



if __name__ == "__main__":
    unittest.main()
