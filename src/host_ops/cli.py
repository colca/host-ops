from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .adapters.ical import parse_ical_file
from .config import AppConfig, load_config
from .models import Event
from .store import SqliteStore
from .workflow import WorkflowEngine


DEFAULT_DB = Path("var/host-ops.db")
DEFAULT_CONFIG = Path("config/property.json")


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
    approve = subparsers.add_parser("approve", help="Approve one pending action")
    approve.add_argument("action_id")
    args = parser.parse_args()

    store = SqliteStore(args.db)
    store.initialize()
    app_config = load_config(args.config) if args.config.exists() else AppConfig()

    if args.command == "init":
        print(f"Initialized {args.db}")
    elif args.command == "demo":
        engine = WorkflowEngine(cleaning=app_config.cleaning)
        inserted = 0
        for event in demo_events():
            inserted += store.save_event_and_actions(event, engine.handle(event))
        print(f"Created {inserted} actions from synthetic events.")
        print_actions(store)
    elif args.command == "actions":
        print_actions(store)
    elif args.command == "import-ical":
        engine = WorkflowEngine(cleaning=app_config.cleaning)
        inserted = 0
        stays = parse_ical_file(args.path)
        for stay in stays:
            event = stay.to_event()
            inserted += store.save_event_and_actions(event, engine.handle(event))
        print(f"Imported {len(stays)} calendar stay(s); created {inserted} action(s).")
        print_actions(store)
    elif args.command == "approve":
        if store.approve(args.action_id):
            print(f"Approved {args.action_id}")
        else:
            raise SystemExit("Action is missing or is not pending approval.")


if __name__ == "__main__":
    main()
