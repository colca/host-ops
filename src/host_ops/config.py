from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExtraTaskFee:
    description: str
    amount: float


def default_extra_tasks() -> dict[str, ExtraTaskFee]:
    return {
        "trash_cans_out_and_in": ExtraTaskFee("Move trash cans out and back in", 10.0),
        "supply_restock": ExtraTaskFee("Restock supplies", 10.0),
        "change_lock_battery": ExtraTaskFee("Change lock battery", 10.0),
    }


@dataclass(frozen=True)
class CleaningSettings:
    base_fee: float = 160.0
    notification_delay_hours: int = 24
    message_language: str = "English"
    extra_tasks: dict[str, ExtraTaskFee] = field(default_factory=default_extra_tasks)


@dataclass(frozen=True)
class AppConfig:
    cleaning: CleaningSettings = field(default_factory=CleaningSettings)


def load_config(path: str | Path) -> AppConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cleaning = data.get("cleaning", {})
    catalog = cleaning.get("extra_task_catalog", [])
    extra_tasks = {
        required(item, "kind"): ExtraTaskFee(
            description=required(item, "description"),
            amount=non_negative_money(required(item, "fee"), f"fee for {item.get('kind')}"),
        )
        for item in catalog
    }
    settings = CleaningSettings(
        base_fee=non_negative_money(cleaning.get("base_fee", 160), "base_fee"),
        notification_delay_hours=int(cleaning.get("notification_delay_hours", 24)),
        message_language=str(cleaning.get("message_language", "English")),
        extra_tasks=extra_tasks or default_extra_tasks(),
    )
    if settings.notification_delay_hours < 0:
        raise ValueError("notification_delay_hours cannot be negative")
    return AppConfig(cleaning=settings)


def required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing configuration field: {key}")
    return data[key]


def non_negative_money(value: Any, field: str) -> float:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a number") from error
    if amount < 0:
        raise ValueError(f"{field} cannot be negative")
    return amount

