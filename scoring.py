from __future__ import annotations

from datetime import time
from typing import Iterable, Literal

import pandas as pd

from models import Tour

MEAL_SCORES = {"RO": 0, "BB": 30, "HB": 50, "FB": 65, "AI": 90, "UAI": 100}


def score_tours(tours: Iterable[Tour], weights: dict[str, float]) -> list[Tour]:
    """Score offers from 0 to 100 using relative price and hotel-quality factors."""

    items = list(tours)
    if not items:
        return []
    frame = pd.DataFrame(
        {
            "index": range(len(items)),
            "price_value": [tour.price_per_person_per_night for tour in items],
            "stars": [tour.stars for tour in items],
            "meal": [tour.meal or "UNKNOWN" for tour in items],
        }
    )
    mean_price = float(frame["price_value"].mean())
    median_price = float(frame["price_value"].median())
    benchmark = (mean_price + median_price) / 2
    group_medians = frame.groupby(["stars", "meal"])["price_value"].transform("median")
    group_counts = frame.groupby(["stars", "meal"])["price_value"].transform("count")
    similar_medians = group_medians.where(group_counts >= 3, median_price)
    factor_names = ("price", "hotel", "meal", "services", "flight", "below_market")
    total_weight = sum(weights.get(name, 0) for name in factor_names)

    for index, tour in enumerate(items):
        value = tour.price_per_person_per_night
        similar_median = float(similar_medians.iloc[index])
        factors = {
            "price": _price_score(value, benchmark),
            "hotel": _hotel_score(tour),
            "meal": MEAL_SCORES.get(tour.meal or "", 40),
            "services": _services_score(tour),
            "flight": _flight_score(tour),
            "below_market": _below_market_score(value, similar_median),
        }
        tour.score = round(
            sum(factors[name] * weights.get(name, 0) for name in factors) / total_weight,
            2,
        )
        tour.score_reasons = _reasons(tour, value, benchmark, similar_median, factors["below_market"])
    return items


def sort_tours(tours: Iterable[Tour], mode: Literal["value", "price"] = "value") -> list[Tour]:
    if mode == "price":
        return sorted(tours, key=lambda tour: (tour.price_per_person, tour.price_total, -tour.score))
    return sorted(tours, key=lambda tour: (-tour.score, tour.price_per_person, tour.price_total))


def _price_score(value: float, benchmark: float) -> float:
    if benchmark <= 0:
        return 50
    return _clip(100 * (2 * benchmark - value) / (2 * benchmark))


def _hotel_score(tour: Tour) -> float:
    components = []
    if tour.stars is not None:
        components.append((tour.stars - 1) / 4 * 100)
    if tour.rating is not None:
        components.append(tour.rating * 10)
    return sum(components) / len(components) if components else 50


def _services_score(tour: Tour) -> float:
    components = []
    for included in (tour.includes_transfer, tour.includes_insurance):
        if included is not None:
            components.append(100 if included else 0)
    if tour.luggage is not None:
        luggage = tour.luggage.strip().casefold()
        components.append(0 if luggage in {"", "0", "нет", "no", "не включён", "не включен"} else 100)
    return sum(components) / len(components) if components else 50


def _flight_score(tour: Tour) -> float:
    components = []
    if tour.direct_flight is not None:
        components.append(100 if tour.direct_flight else 0)
    if tour.departure_time is not None:
        components.append(_departure_time_score(tour.departure_time))
    return sum(components) / len(components) if components else 50


def _departure_time_score(value: time) -> float:
    hour = value.hour + value.minute / 60
    if 8 <= hour <= 22:
        return 100
    if 6 <= hour < 8 or 22 < hour < 24:
        return 65
    return 25


def _below_market_score(value: float, similar_median: float) -> float:
    if similar_median <= 0:
        return 50
    return _clip(50 + 250 * (similar_median - value) / similar_median)


def _reasons(
    tour: Tour,
    value: float,
    benchmark: float,
    similar_median: float,
    below_market_score: float,
) -> list[str]:
    reasons = []
    if value < benchmark * 0.85:
        reasons.append("цена за ночь ниже рынка")
    if below_market_score >= 65:
        reasons.append(f"ниже медианы похожих отелей ({similar_median:,.0f} ₽/чел./ночь)")
    if tour.rating is not None and tour.rating >= 8.5:
        reasons.append(f"высокий рейтинг {tour.rating:.1f}/10")
    if tour.meal in {"AI", "UAI"}:
        reasons.append("расширенное питание")
    if tour.direct_flight:
        reasons.append("прямой перелёт")
    return reasons


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))
