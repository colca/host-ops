from __future__ import annotations

import json
import hashlib
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


def cleaning_dates_in_window(
    check_outs: list[date],
    today: date,
    window_days: int = REMINDER_WINDOW_DAYS,
) -> list[date]:
    """Return unique confirmed cleaning dates in the active reminder window."""
    window_end = today + timedelta(days=window_days)
    return sorted(
        {
            check_out
            for check_out in check_outs
            if today <= check_out <= window_end
        }
    )


def run_due_cleaner_actions(
    store: object,
    outbox: object,
    now: datetime | None = None,
    recipient: str = "configured-cleaner",
    cleaner_name: str = "Cleaner",
    recipients: list[tuple[str, str]] | None = None,
    property_timezone: str = "America/Los_Angeles",
    weekly_digest_weekday: int = 0,
    reminder_hour: int = 9,
) -> tuple[int, int]:
    due_at = now or datetime.now(timezone.utc)
    local_now = due_at.astimezone(ZoneInfo(property_timezone))
    stays: list[tuple[date, date, datetime | None]] = []
    for row in store.list_cleaner_reminder_actions(due_at):
        action_payload = json.loads(row["payload"])
        event_payload = json.loads(row["event_payload"])
        check_in_value = action_payload.get("check_in") or event_payload.get("check_in")
        check_out_value = action_payload.get("check_out") or event_payload.get("check_out")
        if check_in_value and check_out_value:
            occurred_at_value = row["event_occurred_at"]
            stays.append(
                (
                    datetime.fromisoformat(str(check_in_value)).date(),
                    datetime.fromisoformat(str(check_out_value)).date(),
                    (
                        datetime.fromisoformat(str(occurred_at_value))
                        if occurred_at_value
                        else None
                    ),
                )
            )
    cleaning_dates = cleaning_dates_in_window(
        [check_out for _, check_out, _ in stays], local_now.date()
    )
    approved_recipients = recipients or [(recipient, cleaner_name)]
    queued = 0
    today = local_now.date()
    for check_in, check_out, detected_at in sorted(set(stays)):
        if check_out not in cleaning_dates or detected_at is None:
            continue
        detection_age = due_at - detected_at.astimezone(timezone.utc)
        days_until_check_in = (check_in - today).days
        if not (
            timedelta(0) <= detection_age <= timedelta(hours=24)
            and 0 <= days_until_check_in <= 5
        ):
            continue
        if days_until_check_in == 0:
            timing = "today"
        elif days_until_check_in == 1:
            timing = "tomorrow"
        else:
            timing = f"in {days_until_check_in} days"
        for phone, name in approved_recipients:
            recipient_key = hashlib.sha256(phone.encode()).hexdigest()[:16]
            reminder_kind = (
                "five-days-before-checkin"
                if days_until_check_in == 5
                else "last-minute-booking"
            )
            _, inserted = outbox.queue_once(
                recipient=phone,
                body=cleaner_reminder_message(
                    check_out,
                    cleaning_dates,
                    f"Just a friendly last-minute reminder: a newly confirmed guest checks in {timing}, and cleaning is scheduled for {{cleaning_date}}.",
                    name,
                ),
                idempotency_key=(
                    f"cleaner-{reminder_kind}:{check_in.isoformat()}"
                    f":recipient:{recipient_key}"
                ),
            )
            queued += int(inserted)
    if local_now.hour >= reminder_hour:
        for check_in, check_out, _ in sorted(set(stays)):
            if check_out not in cleaning_dates:
                continue
            if today == check_in - timedelta(days=14):
                for phone, name in approved_recipients:
                    recipient_key = hashlib.sha256(phone.encode()).hexdigest()[:16]
                    _, inserted = outbox.queue_once(
                        recipient=phone,
                        body=cleaner_reminder_message(
                            check_out,
                            cleaning_dates,
                            "Just a friendly reminder and early heads-up: our next guest checks in in 14 days, and cleaning is scheduled for {cleaning_date}.",
                            name,
                        ),
                        idempotency_key=(
                            f"cleaner-fourteen-days-before-checkin:{check_in.isoformat()}"
                            f":recipient:{recipient_key}"
                        ),
                    )
                    queued += int(inserted)
            if today == check_in - timedelta(days=5):
                for phone, name in approved_recipients:
                    recipient_key = hashlib.sha256(phone.encode()).hexdigest()[:16]
                    _, inserted = outbox.queue_once(
                        recipient=phone,
                        body=cleaner_reminder_message(
                            check_out,
                            cleaning_dates,
                            "Just a friendly reminder: our next guest checks in in 5 days, and cleaning is scheduled for {cleaning_date}.",
                            name,
                        ),
                        idempotency_key=(
                            f"cleaner-five-days-before-checkin:{check_in.isoformat()}"
                            f":recipient:{recipient_key}"
                        ),
                    )
                    queued += int(inserted)
            if today == check_out - timedelta(days=1):
                for phone, name in approved_recipients:
                    recipient_key = hashlib.sha256(phone.encode()).hexdigest()[:16]
                    _, inserted = outbox.queue_once(
                        recipient=phone,
                        body=cleaner_reminder_message(
                            check_out,
                            cleaning_dates,
                            "Just a friendly reminder that cleaning is scheduled for tomorrow, {cleaning_date}.",
                            name,
                        ),
                        idempotency_key=(
                            f"cleaner-day-before:{check_out.isoformat()}"
                            f":recipient:{recipient_key}"
                        ),
                    )
                    queued += int(inserted)
    return len(cleaning_dates), queued
