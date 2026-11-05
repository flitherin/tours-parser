from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from models import SearchParams, Tour


@pytest.fixture
def params() -> SearchParams:
    return SearchParams(
        departure_city="Москва",
        country="Турция",
        date_from=date(2027, 6, 1),
        date_to=date(2027, 6, 15),
        nights_min=7,
        nights_max=10,
        adults=2,
        min_stars=3,
        max_stars=5,
    )


@pytest.fixture
def make_tour(params: SearchParams):
    def factory(**overrides: Any) -> Tour:
        values: dict[str, Any] = {
            "source": "source_a",
            "url": "https://example.com/tour/1",
            "departure_city": params.departure_city,
            "country": params.country,
            "resort": "Анталья",
            "hotel_name": "Sunrise Resort Hotel",
            "stars": 4,
            "rating": 8.6,
            "meal": "AI",
            "departure_date": date(2027, 6, 5),
            "nights": 7,
            "room_type": "Standard",
            "price_total": 140000,
            "price_per_person": 70000,
            "includes_flight": True,
            "includes_transfer": True,
            "includes_insurance": True,
            "luggage": "20 кг",
            "direct_flight": True,
        }
        values.update(overrides)
        return Tour(**values)

    return factory
