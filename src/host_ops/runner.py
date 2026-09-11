from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .adapters.outbox import FileOutboxMessagingAdapter
from .store import SqliteStore


REMINDER_WINDOW_DAYS = 60


def friendly_date(value: date) -> str:
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def weekly_cleaner_message(
    cleaning_dates: list[date], cleaner_name: str = "Cleaner"
) -> str:
    lines = [
        f"Hi {cleaner_name},",
        f"Here are the cleaning dates for the next {REMINDER_WINDOW_DAYS} days:",
        *(f"- {friendly_date(cleaning_date)}" for cleaning_date in cleaning_dates),
        "Please confirm that these dates work for you.",
    ]
    return "\n".join(lines)


def day_before_cleaner_message(
    cleaning_date: date, cleaner_name: str = "Cleaner"
) -> str:
    return "\n".join(
        [
            f"Hi {cleaner_name},",
            f"Reminder: cleaning is scheduled tomorrow, {friendly_date(cleaning_date)}.",
            "Please confirm availability.",
        ]
    )


def run_due_cleaner_actions(
    store: SqliteStore,
    outbox: FileOutboxMessagingAdapter,
    now: datetime | None = None,
    recipient: str = "configured-cleaner",
    cleaner_name: str = "Cleaner",
    property_timezone: str = "America/Los_Angeles",
    weekly_digest_weekday: int = 0,
    reminder_hour: int = 9,
) -> tuple[int, int]:
    due_at = now or datetime.now(timezone.utc)
    local_now = due_at.astimezone(ZoneInfo(property_timezone))
    window_end = local_now.date() + timedelta(days=REMINDER_WINDOW_DAYS)
    cleaning_dates = sorted(
        {
            datetime.fromisoformat(str(json.loads(row["payload"])["check_out"])).date()
            for row in store.list_cleaner_reminder_actions(due_at)
        }
    )
    cleaning_dates = [
        cleaning_date
        for cleaning_date in cleaning_dates
        if local_now.date() <= cleaning_date <= window_end
    ]
    queued = 0
    weekly_time_reached = (
        local_now.weekday() == weekly_digest_weekday
        and local_now.hour >= reminder_hour
    )
    if cleaning_dates and weekly_time_reached:
        iso_year, iso_week, _ = local_now.date().isocalendar()
        _, inserted = outbox.queue_once(
            recipient=recipient,
            body=weekly_cleaner_message(cleaning_dates, cleaner_name),
            idempotency_key=f"cleaner-weekly:{iso_year}-W{iso_week:02d}",
        )
        queued += int(inserted)

    tomorrow = local_now.date() + timedelta(days=1)
    if tomorrow in cleaning_dates and local_now.hour >= reminder_hour:
        _, inserted = outbox.queue_once(
            recipient=recipient,
            body=day_before_cleaner_message(tomorrow, cleaner_name),
            idempotency_key=f"cleaner-day-before:{tomorrow.isoformat()}",
        )
        queued += int(inserted)
    return len(cleaning_dates), queued
