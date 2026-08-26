from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import Event


@dataclass(frozen=True)
class CalendarStay:
    uid: str
    check_in: datetime
    check_out: datetime
    summary: str = "Airbnb calendar stay"

    def to_event(self) -> Event:
        return Event(
            type="calendar_stay_detected",
            payload={
                "stay_id": self.uid,
                "check_in": self.check_in.isoformat(),
                "check_out": self.check_out.isoformat(),
                "source": "airbnb_ical",
            },
        )


def parse_ical_file(path: str | Path) -> list[CalendarStay]:
    return parse_ical(Path(path).read_text(encoding="utf-8"))


def parse_ical(content: str) -> list[CalendarStay]:
    lines = unfold_lines(content)
    stays: list[CalendarStay] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if {"UID", "DTSTART", "DTEND"}.issubset(current):
                stays.append(
                    CalendarStay(
                        uid=current["UID"],
                        check_in=parse_ical_time(current["DTSTART"]),
                        check_out=parse_ical_time(current["DTEND"]),
                        summary=current.get("SUMMARY", "Airbnb calendar stay"),
                    )
                )
            current = None
        elif current is not None and ":" in line:
            raw_key, value = line.split(":", 1)
            key = raw_key.split(";", 1)[0]
            if key in {"UID", "DTSTART", "DTEND", "SUMMARY"}:
                current[key] = value
    return stays


def unfold_lines(content: str) -> list[str]:
    unfolded: list[str] = []
    for raw_line in content.replace("\r\n", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)
    return unfolded


def parse_ical_time(value: str) -> datetime:
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)

