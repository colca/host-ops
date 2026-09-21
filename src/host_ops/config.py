from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
class AirbnbCalendarSettings:
    url_environment_variable: str = "AIRBNB_ICAL_URL"
    timeout_seconds: int = 20


@dataclass(frozen=True)
class CleanerMessagingSettings:
    provider: str = "file_outbox"
    automatic_delivery_enabled: bool = False
    phone_environment_variable: str = "CLEANER_PHONE_NUMBER"
    name_environment_variable: str = "CLEANER_NAME"
    recipients_environment_variable: str = "CLEANER_RECIPIENTS_JSON"
    weekly_digest_weekday: int = 0
    reminder_hour: int = 9
    account_sid_environment_variable: str = "TWILIO_ACCOUNT_SID"
    auth_token_environment_variable: str = "TWILIO_AUTH_TOKEN"
    from_number_environment_variable: str = "TWILIO_FROM_NUMBER"


@dataclass(frozen=True)
class PropertySettings:
    id: str = "default-property"
    timezone: str = "America/Los_Angeles"


def default_event_category_premiums() -> dict[str, float]:
    return {"sports": 0.0, "concert": 0.0, "conference": 0.0, "other": 0.0}


def default_last_minute_discount_tiers() -> tuple[tuple[int, float], ...]:
    return ((30, 5.0), (14, 10.0), (7, 15.0), (3, 20.0))


@dataclass(frozen=True)
class PricingSettings:
    minimum_nightly_rate: float = 75.0
    maximum_nightly_rate: float = 1500.0
    maximum_recommendation_change_percent: float = 50.0
    maximum_event_premium_percent: float = 60.0
    event_radius_miles: float = 5.0
    minimum_comparable_count: int = 3
    preferred_comparable_count: int = 5
    minimum_rate_by_venue: dict[str, float] = field(default_factory=dict)
    last_minute_discount_tiers: tuple[tuple[int, float], ...] = field(
        default_factory=default_last_minute_discount_tiers
    )
    event_category_premiums: dict[str, float] = field(
        default_factory=default_event_category_premiums
    )


@dataclass(frozen=True)
class AppConfig:
    property: PropertySettings = field(default_factory=PropertySettings)
    cleaning: CleaningSettings = field(default_factory=CleaningSettings)
    airbnb_calendar: AirbnbCalendarSettings = field(
        default_factory=AirbnbCalendarSettings
    )
    cleaner_messaging: CleanerMessagingSettings = field(
        default_factory=CleanerMessagingSettings
    )
    pricing: PricingSettings = field(default_factory=PricingSettings)


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
    airbnb = data.get("integrations", {}).get("airbnb", {})
    variable = environment_variable_name(
        airbnb.get("ical_url_environment_variable", "AIRBNB_ICAL_URL"),
        "ical_url_environment_variable",
    )
    calendar = AirbnbCalendarSettings(
        url_environment_variable=variable,
        timeout_seconds=int(airbnb.get("ical_timeout_seconds", 20)),
    )
    if calendar.timeout_seconds <= 0:
        raise ValueError("ical_timeout_seconds must be positive")
    cleaner = data.get("integrations", {}).get("cleaner_messaging", {})
    messaging = CleanerMessagingSettings(
        provider=str(cleaner.get("provider", "file_outbox")),
        automatic_delivery_enabled=bool(
            cleaner.get("automatic_delivery_enabled", False)
        ),
        phone_environment_variable=environment_variable_name(
            cleaner.get("phone_environment_variable", "CLEANER_PHONE_NUMBER"),
            "phone_environment_variable",
        ),
        name_environment_variable=environment_variable_name(
            cleaner.get("name_environment_variable", "CLEANER_NAME"),
            "name_environment_variable",
        ),
        recipients_environment_variable=environment_variable_name(
            cleaner.get(
                "recipients_environment_variable", "CLEANER_RECIPIENTS_JSON"
            ),
            "recipients_environment_variable",
        ),
        weekly_digest_weekday=int(cleaner.get("weekly_digest_weekday", 0)),
        reminder_hour=int(cleaner.get("reminder_hour", 9)),
        account_sid_environment_variable=environment_variable_name(
            cleaner.get("account_sid_environment_variable", "TWILIO_ACCOUNT_SID"),
            "account_sid_environment_variable",
        ),
        auth_token_environment_variable=environment_variable_name(
            cleaner.get("auth_token_environment_variable", "TWILIO_AUTH_TOKEN"),
            "auth_token_environment_variable",
        ),
        from_number_environment_variable=environment_variable_name(
            cleaner.get("from_number_environment_variable", "TWILIO_FROM_NUMBER"),
            "from_number_environment_variable",
        ),
    )
    if messaging.provider not in {"file_outbox", "twilio"}:
        raise ValueError("cleaner_messaging.provider must be file_outbox or twilio")
    if messaging.weekly_digest_weekday not in range(7):
        raise ValueError("weekly_digest_weekday must be between 0 and 6")
    if messaging.reminder_hour not in range(24):
        raise ValueError("reminder_hour must be between 0 and 23")
    property_data = data.get("property", {})
    property_settings = PropertySettings(
        id=str(property_data.get("id", "default-property")),
        timezone=str(property_data.get("timezone", "America/Los_Angeles")),
    )
    if not property_settings.id.strip():
        raise ValueError("property.id cannot be empty")
    try:
        ZoneInfo(property_settings.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError("property.timezone must be a valid IANA timezone") from error
    pricing_data = data.get("pricing", {})
    raw_category_premiums = pricing_data.get(
        "event_category_premiums", default_event_category_premiums()
    )
    if not isinstance(raw_category_premiums, dict):
        raise ValueError("event_category_premiums must be an object")
    category_premiums = {
        str(category).strip().casefold(): non_negative_money(
            value, f"premium for {category}"
        )
        for category, value in raw_category_premiums.items()
    }
    raw_venue_floors = pricing_data.get("minimum_rate_by_venue", {})
    if not isinstance(raw_venue_floors, dict):
        raise ValueError("minimum_rate_by_venue must be an object")
    venue_floors = {
        str(venue).strip().casefold(): non_negative_money(
            value, f"minimum rate for {venue}"
        )
        for venue, value in raw_venue_floors.items()
    }
    raw_discount_tiers = pricing_data.get(
        "last_minute_discount_tiers",
        [
            {"days": days, "percent": percent}
            for days, percent in default_last_minute_discount_tiers()
        ],
    )
    if not isinstance(raw_discount_tiers, list):
        raise ValueError("last_minute_discount_tiers must be a list")
    discount_tiers = tuple(
        (
            int(required(tier, "days")),
            non_negative_money(required(tier, "percent"), f"discount tier {index}"),
        )
        for index, tier in enumerate(raw_discount_tiers)
        if isinstance(tier, dict)
    )
    if len(discount_tiers) != len(raw_discount_tiers):
        raise ValueError("each last-minute discount tier must be an object")
    pricing = PricingSettings(
        minimum_nightly_rate=non_negative_money(
            pricing_data.get("minimum_nightly_rate", 75), "minimum_nightly_rate"
        ),
        maximum_nightly_rate=non_negative_money(
            pricing_data.get("maximum_nightly_rate", 1500), "maximum_nightly_rate"
        ),
        maximum_recommendation_change_percent=non_negative_money(
            pricing_data.get("maximum_recommendation_change_percent", 50),
            "maximum_recommendation_change_percent",
        ),
        maximum_event_premium_percent=non_negative_money(
            pricing_data.get("maximum_event_premium_percent", 60),
            "maximum_event_premium_percent",
        ),
        event_radius_miles=float(pricing_data.get("event_radius_miles", 5)),
        minimum_comparable_count=int(pricing_data.get("minimum_comparable_count", 3)),
        preferred_comparable_count=int(
            pricing_data.get("preferred_comparable_count", 5)
        ),
        minimum_rate_by_venue=venue_floors,
        last_minute_discount_tiers=discount_tiers,
        event_category_premiums=category_premiums,
    )
    if pricing.minimum_nightly_rate <= 0:
        raise ValueError("minimum_nightly_rate must be positive")
    if pricing.maximum_nightly_rate < pricing.minimum_nightly_rate:
        raise ValueError("maximum_nightly_rate must be at least minimum_nightly_rate")
    if pricing.event_radius_miles <= 0:
        raise ValueError("event_radius_miles must be positive")
    if pricing.minimum_comparable_count <= 0:
        raise ValueError("minimum_comparable_count must be positive")
    if pricing.preferred_comparable_count < pricing.minimum_comparable_count:
        raise ValueError(
            "preferred_comparable_count must be at least minimum_comparable_count"
        )
    if any(rate > pricing.maximum_nightly_rate for rate in venue_floors.values()):
        raise ValueError("venue minimum rates cannot exceed maximum_nightly_rate")
    if not pricing.last_minute_discount_tiers:
        raise ValueError("last_minute_discount_tiers cannot be empty")
    tier_days = [days for days, _ in pricing.last_minute_discount_tiers]
    tier_discounts = [percent for _, percent in pricing.last_minute_discount_tiers]
    if any(days <= 0 for days in tier_days):
        raise ValueError("last-minute discount tier days must be positive")
    if len(set(tier_days)) != len(tier_days):
        raise ValueError("last-minute discount tier days must be unique")
    ordered_tiers = sorted(pricing.last_minute_discount_tiers, reverse=True)
    ordered_discounts = [percent for _, percent in ordered_tiers]
    if any(percent > 100 for percent in tier_discounts):
        raise ValueError("last-minute discount tier percent cannot exceed 100")
    if ordered_discounts != sorted(ordered_discounts):
        raise ValueError("discount percentages must increase as the stay gets closer")
    return AppConfig(
        cleaning=settings,
        property=property_settings,
        airbnb_calendar=calendar,
        cleaner_messaging=messaging,
        pricing=pricing,
    )


def environment_variable_name(value: Any, field: str) -> str:
    variable = str(value)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
        raise ValueError(f"{field} must be a valid variable name")
    return variable


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
