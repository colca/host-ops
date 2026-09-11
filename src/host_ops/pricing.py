from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from math import isfinite
from statistics import median
from typing import Any

from .config import PricingSettings


@dataclass(frozen=True)
class NightlyRateRecommendation:
    date: str
    current_rate: float
    market_baseline: float
    event_premium_percent: float
    last_minute_discount_percent: float
    venue_minimum_rate: float | None
    recommended_rate: int
    comparable_count: int
    comparable_method: str
    comparable_confidence: str
    bedroom_premium_percent: float
    demand_signals: tuple[dict[str, Any], ...]


def recommend_nightly_rate(
    nightly_input: dict[str, Any], settings: PricingSettings
) -> NightlyRateRecommendation:
    """Build an explainable recommendation from comparables and local demand.

    The market median is the nightly anchor. Nearby events identify dates that
    need fresh market samples. A bounded distance-weighted premium is applied
    only when the operator configured one from observed market evidence; all
    default event premiums are zero. This function only recommends a price;
    applying it remains behind the workflow approval gate.
    """
    date = str(required(nightly_input, "date"))
    try:
        stay_date = calendar_date.fromisoformat(date)
    except ValueError as error:
        raise ValueError("date must use YYYY-MM-DD format") from error
    current_rate = positive_money(
        required(nightly_input, "current_rate"), "current_rate"
    )
    raw_comparables = nightly_input.get("comparable_rates", [])
    if not isinstance(raw_comparables, list):
        raise ValueError("comparable_rates must be a list")
    comparable_rates = [
        positive_money(value, f"comparable_rates[{index}]")
        for index, value in enumerate(raw_comparables)
    ]
    comparable_method = "same_size"
    bedroom_premium = 0.0
    if len(comparable_rates) < settings.minimum_comparable_count:
        raw_fallback = nightly_input.get("one_bedroom_comparable_rates", [])
        if not isinstance(raw_fallback, list):
            raise ValueError("one_bedroom_comparable_rates must be a list")
        fallback_rates = [
            positive_money(value, f"one_bedroom_comparable_rates[{index}]")
            for index, value in enumerate(raw_fallback)
        ]
        if len(fallback_rates) < settings.minimum_comparable_count:
            raise ValueError(
                "need at least three same-size or one-bedroom comparable observations"
            )
        adjustment = nightly_input.get("one_to_two_bedroom_adjustment")
        if not isinstance(adjustment, dict):
            raise ValueError(
                "one_to_two_bedroom_adjustment is required for one-bedroom fallback"
            )
        evidence_count = int(required(adjustment, "evidence_count"))
        if evidence_count < settings.minimum_comparable_count:
            raise ValueError("bedroom adjustment needs sufficient market evidence")
        bedroom_premium = bounded_number(
            required(adjustment, "premium_percent"),
            0.0,
            100.0,
            "one_to_two_bedroom_adjustment.premium_percent",
        )
        comparable_rates = [
            round(rate * (1.0 + bedroom_premium / 100.0), 2)
            for rate in fallback_rates
        ]
        comparable_method = "one_bedroom_market_adjusted"
    comparable_confidence = (
        "high"
        if len(comparable_rates) >= settings.preferred_comparable_count
        else "limited"
    )
    market_baseline = round(float(median(comparable_rates)), 2)

    signals = nightly_input.get("demand_signals", [])
    if not isinstance(signals, list):
        raise ValueError("demand_signals must be a list")
    normalized_signals: list[dict[str, Any]] = []
    premium = 0.0
    venue_minimum_rate: float | None = None
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            raise ValueError(f"demand_signals[{index}] must be an object")
        category = str(signal.get("category", "other")).strip().casefold() or "other"
        importance = bounded_number(
            signal.get("importance", 1.0), 0.0, 1.0, "importance"
        )
        distance = bounded_number(
            signal.get("distance_miles", 0.0), 0.0, None, "distance_miles"
        )
        category_premium = settings.event_category_premiums.get(
            category, settings.event_category_premiums.get("other", 0.0)
        )
        distance_factor = max(0.0, 1.0 - distance / settings.event_radius_miles)
        contribution = category_premium * importance * distance_factor
        premium += contribution
        venue_id = str(signal.get("venue_id", "")).strip().casefold()
        if venue_id in settings.minimum_rate_by_venue:
            configured_floor = settings.minimum_rate_by_venue[venue_id]
            venue_minimum_rate = max(venue_minimum_rate or 0.0, configured_floor)
        normalized_signals.append(
            {
                "id": str(required(signal, "id")),
                "name": str(required(signal, "name")),
                "category": category,
                "distance_miles": round(distance, 2),
                "importance": round(importance, 2),
                "venue_id": venue_id or None,
                "premium_contribution_percent": round(contribution, 2),
            }
        )

    premium = min(premium, settings.maximum_event_premium_percent)
    discount = 0.0
    if nightly_input.get("as_of_date") is not None:
        try:
            as_of_date = calendar_date.fromisoformat(str(nightly_input["as_of_date"]))
        except ValueError as error:
            raise ValueError("as_of_date must use YYYY-MM-DD format") from error
        days_until_stay = (stay_date - as_of_date).days
        if 0 < days_until_stay <= settings.last_minute_window_days:
            discount = settings.last_minute_discount_percent

    # Promotions are intentionally kept separate from the nightly rate. Channel
    # adapters can therefore create a visible native promotion instead of
    # silently lowering the listing's base price.
    unconstrained = market_baseline * (1.0 + premium / 100.0)
    bounded = min(
        max(unconstrained, settings.minimum_nightly_rate),
        settings.maximum_nightly_rate,
    )
    lower_change_bound = current_rate * (
        1.0 - settings.maximum_recommendation_change_percent / 100.0
    )
    upper_change_bound = current_rate * (
        1.0 + settings.maximum_recommendation_change_percent / 100.0
    )
    recommended = round(min(max(bounded, lower_change_bound), upper_change_bound))
    if venue_minimum_rate is not None:
        recommended = round(
            min(max(recommended, venue_minimum_rate), settings.maximum_nightly_rate)
        )

    return NightlyRateRecommendation(
        date=date,
        current_rate=current_rate,
        market_baseline=market_baseline,
        event_premium_percent=round(premium, 2),
        last_minute_discount_percent=round(discount, 2),
        venue_minimum_rate=venue_minimum_rate,
        recommended_rate=recommended,
        comparable_count=len(comparable_rates),
        comparable_method=comparable_method,
        comparable_confidence=comparable_confidence,
        bedroom_premium_percent=round(bedroom_premium, 2),
        demand_signals=tuple(normalized_signals),
    )


def required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing pricing field: {key}")
    return data[key]


def positive_money(value: Any, field: str) -> float:
    amount = bounded_number(value, 0.01, None, field)
    return round(amount, 2)


def bounded_number(
    value: Any, minimum: float, maximum: float | None, field: str
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a number") from error
    if not isfinite(number):
        raise ValueError(f"{field} must be finite")
    if number < minimum or (maximum is not None and number > maximum):
        upper = f" and {maximum}" if maximum is not None else ""
        raise ValueError(f"{field} must be between {minimum}{upper}")
    return number
