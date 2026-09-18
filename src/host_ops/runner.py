from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REMINDER_WINDOW_DAYS = 60


def friendly_date(value: date) -> str:
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def cleaner_reminder_message(
    cleaning_date: date,
    cleaning_dates: list[date],
    trigger: str,
    cleaner_name: str = "Cleaner",
) -> str:
    lines = [
        "COYU | Host Ops cleaner scheduling",
        f"Hi {cleaner_name},",
        trigger.format(cleaning_date=friendly_date(cleaning_date)),
        f"All confirmed cleaning dates for the next {REMINDER_WINDOW_DAYS} days:",
        *(f"- {friendly_date(cleaning_date)}" for cleaning_date in cleaning_dates),
        "Please confirm availability for the next cleaning.",
        "Reply STOP to opt out or HELP for assistance.",
    ]
    return "\n".join(lines)


def run_due_cleaner_actions(
    store: object,
    outbox: object,
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
    stays: list[tuple[date, date]] = []
    for row in store.list_cleaner_reminder_actions(due_at):
        action_payload = json.loads(row["payload"])
        event_payload = json.loads(row["event_payload"])
        check_in_value = action_payload.get("check_in") or event_payload.get("check_in")
        check_out_value = action_payload.get("check_out") or event_payload.get("check_out")
        if check_in_value and check_out_value:
            stays.append(
                (
                    datetime.fromisoformat(str(check_in_value)).date(),
                    datetime.fromisoformat(str(check_out_value)).date(),
                )
            )
    cleaning_dates = sorted({check_out for _, check_out in stays})
    cleaning_dates = [
        cleaning_date
        for cleaning_date in cleaning_dates
        if local_now.date() <= cleaning_date <= window_end
    ]
    queued = 0
    if local_now.hour >= reminder_hour:
        today = local_now.date()
        for check_in, check_out in sorted(set(stays)):
            if check_out not in cleaning_dates:
                continue
            if today == check_in - timedelta(days=5):
                _, inserted = outbox.queue_once(
                    recipient=recipient,
                    body=cleaner_reminder_message(
                        check_out,
                        cleaning_dates,
                        "Just a friendly reminder: our next guest checks in in 5 days, and cleaning is scheduled for {cleaning_date}.",
                        cleaner_name,
                    ),
                    idempotency_key=f"cleaner-five-days-before-checkin:{check_in.isoformat()}",
                )
                queued += int(inserted)
            if today == check_out - timedelta(days=1):
                _, inserted = outbox.queue_once(
                    recipient=recipient,
                    body=cleaner_reminder_message(
                        check_out,
                        cleaning_dates,
                        "Just a friendly reminder that cleaning is scheduled for tomorrow, {cleaning_date}.",
                        cleaner_name,
                    ),
                    idempotency_key=f"cleaner-day-before:{check_out.isoformat()}",
                )
                queued += int(inserted)
    return len(cleaning_dates), queued
