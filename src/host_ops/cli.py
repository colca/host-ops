from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .adapters.ical import parse_ical, parse_ical_file
from .adapters.ical_poll import (
    CalendarPollError,
    calendar_url_from_environment,
    fetch_private_ical,
)
from .adapters.outbox import FileOutboxMessagingAdapter
from .adapters.twilio import SmsDeliveryError, TwilioMessagingAdapter
from .config import AppConfig, load_config
from .models import Event
from .runner import (
    cleaner_reminder_message,
    cleaning_dates_in_window,
    run_due_cleaner_actions,
)
from .store import SqliteStore
from .workflow import WorkflowEngine


DEFAULT_DB = Path(os.environ.get("HOST_OPS_DB_PATH", "var/host-ops.db"))
DEFAULT_CONFIG = Path(os.environ.get("HOST_OPS_CONFIG_PATH", "config/property.json"))
DEFAULT_OUTBOX = Path("var/cleaner-outbox.jsonl")


def cleaner_contacts(app_config: AppConfig) -> list[tuple[str, str]]:
    settings = app_config.cleaner_messaging
    raw_recipients = os.environ.get(settings.recipients_environment_variable, "").strip()
    if raw_recipients:
        try:
            configured = json.loads(raw_recipients)
        except json.JSONDecodeError as error:
            raise SystemExit(
                f"{settings.recipients_environment_variable} must be valid JSON."
            ) from error
        if not isinstance(configured, list) or not configured:
            raise SystemExit(
                f"{settings.recipients_environment_variable} must be a non-empty JSON list."
            )
        contacts: list[tuple[str, str]] = []
        seen: set[str] = set()
        for entry in configured:
            if not isinstance(entry, dict) or entry.get("approved") is not True:
                raise SystemExit("Every SMS recipient must explicitly set approved to true.")
            phone = str(entry.get("phone", "")).strip()
            name = str(entry.get("name", "Recipient")).strip() or "Recipient"
            if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
                raise SystemExit("Every approved SMS recipient must use E.164 format.")
            if phone in seen:
                raise SystemExit("Approved SMS recipient phone numbers must be unique.")
            seen.add(phone)
            contacts.append((phone, name))
        return contacts
    phone = os.environ.get(settings.phone_environment_variable, "").strip()
    name = os.environ.get(settings.name_environment_variable, "Cleaner").strip()
    if phone and not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise SystemExit(
            f"{settings.phone_environment_variable} must use E.164 format."
        )
    return [(phone or "configured-cleaner", name or "Cleaner")]


def demo_events() -> list[Event]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return [
        Event(
            type="reservation_confirmed",
            payload={
                "reservation_id": "demo-reservation-001",
                "guest_first_name": "Jordan",
                "check_in": (now + timedelta(days=7)).isoformat(),
                "check_out": (now + timedelta(days=10)).isoformat(),
            },
        ),
        Event(
            type="stadium_event_found",
            payload={
                "external_event_id": "demo-event-001",
                "name": "Demo stadium concert",
                "starts_at": (now + timedelta(days=30)).isoformat(),
                "proposed_percent_change": 15,
                "affected_dates": [(now + timedelta(days=30)).date().isoformat()],
            },
        ),
        Event(
            type="cleaner_work_reported",
            payload={
                "turnover_id": "demo-turnover-001",
                "extras": ["supply_restock"],
                "photo_review_status": "passed",
            },
        ),
    ]


def print_actions(store: SqliteStore) -> None:
    rows = store.list_actions()
    if not rows:
        print("No actions.")
        return
    for row in rows:
        when = row["execute_at"] or "now"
        print(
            f"{row['id']}  {row['status']:<18} {row['risk']:<6} "
            f"{when}  {row['summary']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Host Ops local workflow runner")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Initialize the local database")
    subparsers.add_parser("demo", help="Load synthetic events")
    subparsers.add_parser("actions", help="List proposed actions")
    import_ical = subparsers.add_parser(
        "import-ical", help="Import stays from a downloaded Airbnb .ics file"
    )
    import_ical.add_argument("path", type=Path)
    subparsers.add_parser(
        "poll-ical", help="Fetch the private Airbnb iCal URL and import its stays"
    )
    subparsers.add_parser(
        "cloud-cycle",
        help="Poll, queue, and deliver with durable Google Cloud Firestore state",
    )
    test_sms = subparsers.add_parser(
        "send-test-sms", help="Send one explicit connectivity test to the configured recipient"
    )
    test_sms.add_argument("--confirm-live-delivery", action="store_true")
    schedule_test_sms = subparsers.add_parser(
        "send-schedule-test-sms",
        help="Send one explicit test containing live confirmed cleaning dates",
    )
    schedule_test_sms.add_argument("--confirm-live-delivery", action="store_true")
    recommend_prices = subparsers.add_parser(
        "recommend-prices",
        help="Create approval-gated nightly rate recommendations from a JSON snapshot",
    )
    recommend_prices.add_argument("path", type=Path)
    run_due = subparsers.add_parser(
        "run-due", help="Queue due cleaner work orders in the safe local outbox"
    )
    run_due.add_argument("--outbox", type=Path, default=DEFAULT_OUTBOX)
    deliver = subparsers.add_parser(
        "deliver-outbox", help="Submit pending outbox messages to configured SMS"
    )
    deliver.add_argument("--outbox", type=Path, default=DEFAULT_OUTBOX)
    deliver.add_argument("--confirm-live-delivery", action="store_true")
    deliver.add_argument("--automatic", action="store_true")
    approve = subparsers.add_parser("approve", help="Approve one pending action")
    approve.add_argument("action_id")
    args = parser.parse_args()

    store = SqliteStore(args.db)
    store.initialize()
    app_config = load_config(args.config) if args.config.exists() else AppConfig()

    if args.command == "init":
        print(f"Initialized {args.db}")
    elif args.command == "demo":
        engine = WorkflowEngine(
            cleaning=app_config.cleaning, pricing=app_config.pricing
        )
        inserted = 0
        for event in demo_events():
            inserted += store.save_event_and_actions(event, engine.handle(event))
        print(f"Created {inserted} actions from synthetic events.")
        print_actions(store)
    elif args.command == "actions":
        print_actions(store)
    elif args.command == "import-ical":
        engine = WorkflowEngine(
            cleaning=app_config.cleaning, pricing=app_config.pricing
        )
        inserted = 0
        stays = parse_ical_file(args.path)
        for stay in stays:
            event = stay.to_event()
            inserted += store.save_event_and_actions(event, engine.handle(event))
        print(f"Imported {len(stays)} calendar stay(s); created {inserted} action(s).")
        print_actions(store)
    elif args.command == "poll-ical":
        engine = WorkflowEngine(
            cleaning=app_config.cleaning, pricing=app_config.pricing
        )
        try:
            url = calendar_url_from_environment(
                app_config.airbnb_calendar.url_environment_variable
            )
            content = fetch_private_ical(
                url, timeout_seconds=app_config.airbnb_calendar.timeout_seconds
            )
        except CalendarPollError as error:
            raise SystemExit(f"Calendar poll failed: {error}") from error
        stays = parse_ical(content)
        inserted = 0
        for stay in stays:
            event = stay.to_event()
            inserted += store.save_event_and_actions(event, engine.handle(event))
        print(f"Polled {len(stays)} calendar stay(s); created {inserted} action(s).")
        print_actions(store)
    elif args.command == "cloud-cycle":
        from google.cloud import firestore

        from .adapters.firestore import (
            FirestoreOutboxMessagingAdapter,
            FirestoreStore,
        )

        cloud_store = FirestoreStore(firestore.Client(), app_config.property.id)
        engine = WorkflowEngine(
            cleaning=app_config.cleaning, pricing=app_config.pricing
        )
        try:
            url = calendar_url_from_environment(
                app_config.airbnb_calendar.url_environment_variable
            )
            content = fetch_private_ical(
                url, timeout_seconds=app_config.airbnb_calendar.timeout_seconds
            )
        except CalendarPollError as error:
            raise SystemExit(f"Calendar poll failed: {error}") from error
        stays = parse_ical(content)
        inserted = 0
        for stay in stays:
            event = stay.to_event()
            inserted += cloud_store.save_event_and_actions(event, engine.handle(event))
        contacts = cleaner_contacts(app_config)
        cloud_outbox = FirestoreOutboxMessagingAdapter(
            cloud_store.client, app_config.property.id
        )
        cleaning_dates, queued = run_due_cleaner_actions(
            cloud_store,
            cloud_outbox,
            recipients=contacts,
            property_timezone=app_config.property.timezone,
            weekly_digest_weekday=(
                app_config.cleaner_messaging.weekly_digest_weekday
            ),
            reminder_hour=app_config.cleaner_messaging.reminder_hour,
        )
        delivered = 0
        messaging = app_config.cleaner_messaging
        if messaging.provider == "twilio" and messaging.automatic_delivery_enabled:
            if any(phone == "configured-cleaner" for phone, _ in contacts):
                raise SystemExit("A cleaner phone number is required for live delivery.")
            try:
                delivered = cloud_outbox.deliver_pending(
                    TwilioMessagingAdapter(
                        account_sid=os.environ.get(
                            messaging.account_sid_environment_variable, ""
                        ),
                        auth_token=os.environ.get(
                            messaging.auth_token_environment_variable, ""
                        ),
                        from_number=os.environ.get(
                            messaging.from_number_environment_variable, ""
                        ),
                    ),
                    allowed_recipients={phone for phone, _ in contacts},
                )
            except ValueError as error:
                raise SystemExit(f"SMS delivery blocked: {error}") from error
            except SmsDeliveryError as error:
                raise SystemExit(f"SMS delivery failed: {error}") from error
        cloud_store.record_cloud_cycle(
            stays_polled=len(stays),
            actions_created=inserted,
            cleaning_dates=cleaning_dates,
            reminders_queued=queued,
            messages_submitted=delivered,
        )
        print(
            f"Cloud cycle polled {len(stays)} stay(s), created {inserted} action(s), "
            f"found {cleaning_dates} cleaning date(s), queued {queued} reminder(s), "
            f"and submitted {delivered} SMS message(s)."
        )
    elif args.command == "send-test-sms":
        if not args.confirm_live_delivery:
            raise SystemExit("Pass --confirm-live-delivery to submit a real test SMS.")
        messaging = app_config.cleaner_messaging
        if messaging.provider != "twilio":
            raise SystemExit("Live delivery is disabled; provider is not Twilio.")
        contacts = cleaner_contacts(app_config)
        if any(phone == "configured-cleaner" for phone, _ in contacts):
            raise SystemExit("A cleaner phone number is required for live delivery.")
        try:
            adapter = TwilioMessagingAdapter(
                account_sid=os.environ.get(
                    messaging.account_sid_environment_variable, ""
                ),
                auth_token=os.environ.get(
                    messaging.auth_token_environment_variable, ""
                ),
                from_number=os.environ.get(
                    messaging.from_number_environment_variable, ""
                ),
            )
            for recipient, _ in contacts:
                adapter.send(
                    recipient,
                    "COYU | Host Ops cloud test: Cloud Run successfully reached "
                    "Twilio. No action is needed. Reply STOP to opt out.",
                )
        except SmsDeliveryError as error:
            raise SystemExit(f"SMS delivery failed: {error}") from error
        print(f"Twilio accepted {len(contacts)} cloud test SMS message(s).")
    elif args.command == "send-schedule-test-sms":
        if not args.confirm_live_delivery:
            raise SystemExit("Pass --confirm-live-delivery to submit a real test SMS.")
        messaging = app_config.cleaner_messaging
        if messaging.provider != "twilio":
            raise SystemExit("Live delivery is disabled; provider is not Twilio.")
        contacts = cleaner_contacts(app_config)
        if any(phone == "configured-cleaner" for phone, _ in contacts):
            raise SystemExit("A cleaner phone number is required for live delivery.")
        try:
            url = calendar_url_from_environment(
                app_config.airbnb_calendar.url_environment_variable
            )
            content = fetch_private_ical(
                url, timeout_seconds=app_config.airbnb_calendar.timeout_seconds
            )
        except CalendarPollError as error:
            raise SystemExit(f"Calendar poll failed: {error}") from error
        today = datetime.now(ZoneInfo(app_config.property.timezone)).date()
        cleaning_dates = cleaning_dates_in_window(
            [stay.check_out.date() for stay in parse_ical(content)], today
        )
        if not cleaning_dates:
            raise SystemExit("No confirmed cleaning dates were found in the next 60 days.")
        try:
            adapter = TwilioMessagingAdapter(
                account_sid=os.environ.get(
                    messaging.account_sid_environment_variable, ""
                ),
                auth_token=os.environ.get(
                    messaging.auth_token_environment_variable, ""
                ),
                from_number=os.environ.get(
                    messaging.from_number_environment_variable, ""
                ),
            )
            for recipient, recipient_name in contacts:
                adapter.send(
                    recipient,
                    cleaner_reminder_message(
                        cleaning_dates[0],
                        cleaning_dates,
                        "This is a friendly test reminder. The next confirmed cleaning is scheduled for {cleaning_date}.",
                        recipient_name,
                    ),
                )
        except SmsDeliveryError as error:
            raise SystemExit(f"SMS delivery failed: {error}") from error
        print(
            f"Twilio accepted {len(contacts)} schedule test SMS message(s) containing "
            f"{len(cleaning_dates)} confirmed cleaning date(s) each."
        )
    elif args.command == "recommend-prices":
        snapshot = json.loads(args.path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise SystemExit("Pricing snapshot must be a JSON object.")
        engine = WorkflowEngine(
            cleaning=app_config.cleaning, pricing=app_config.pricing
        )
        event = Event(type="pricing_snapshot_received", payload=snapshot)
        inserted = store.save_event_and_actions(event, engine.handle(event))
        print(f"Created {inserted} approval-gated rate recommendation(s).")
        print_actions(store)
    elif args.command == "approve":
        if store.approve(args.action_id):
            print(f"Approved {args.action_id}")
        else:
            raise SystemExit("Action is missing or is not pending approval.")
    elif args.command == "run-due":
        contacts = cleaner_contacts(app_config)
        cleaning_dates, queued = run_due_cleaner_actions(
            store,
            FileOutboxMessagingAdapter(args.outbox),
            recipients=contacts,
            property_timezone=app_config.property.timezone,
            weekly_digest_weekday=(
                app_config.cleaner_messaging.weekly_digest_weekday
            ),
            reminder_hour=app_config.cleaner_messaging.reminder_hour,
        )
        print(
            f"Found {cleaning_dates} cleaning date(s) in the 60-day window; "
            f"queued {queued} reminder(s)."
        )
    elif args.command == "deliver-outbox":
        messaging = app_config.cleaner_messaging
        if messaging.provider != "twilio":
            raise SystemExit("Live delivery is disabled; provider is file_outbox.")
        if args.automatic and not messaging.automatic_delivery_enabled:
            print("Automatic SMS delivery is disabled.")
            return
        if not args.confirm_live_delivery and not args.automatic:
            raise SystemExit("Pass --confirm-live-delivery to submit real SMS messages.")
        contacts = cleaner_contacts(app_config)
        if any(phone == "configured-cleaner" for phone, _ in contacts):
            raise SystemExit("A cleaner phone number is required for live delivery.")
        try:
            adapter = TwilioMessagingAdapter(
                account_sid=os.environ.get(
                    messaging.account_sid_environment_variable, ""
                ),
                auth_token=os.environ.get(
                    messaging.auth_token_environment_variable, ""
                ),
                from_number=os.environ.get(
                    messaging.from_number_environment_variable, ""
                ),
            )
            delivered = FileOutboxMessagingAdapter(args.outbox).deliver_pending(
                adapter, allowed_recipients={phone for phone, _ in contacts}
            )
        except ValueError as error:
            raise SystemExit(f"SMS delivery blocked: {error}") from error
        except SmsDeliveryError as error:
            raise SystemExit(f"SMS delivery failed: {error}") from error
        print(f"Submitted {delivered} SMS message(s).")


if __name__ == "__main__":
    main()
