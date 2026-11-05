from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scrapers.sletat import SletatScraper
from scrapers.tez_tour import TezTourScraper
from scrapers.travelata import TravelataScraper

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"Unexpected URL: {url}")

    async def get_text(self, url: str, **kwargs: Any) -> str:
        if url.endswith("/accommodations"):
            return """<root><accommodations>
                <accommodation><accommodationId>2</accommodationId><name>DBL</name><adult>2</adult><children>0</children></accommodation>
                <accommodation><accommodationId>14317</accommodationId><name>DBL+CHD</name><adult>2</adult><children>1</children></accommodation>
            </accommodations></root>"""
        raise AssertionError(f"Unexpected URL: {url}")


@pytest.mark.asyncio
async def test_travelata_adapter(params, monkeypatch) -> None:
    monkeypatch.setenv("TRAVELATA_API_USERNAME", "user")
    monkeypatch.setenv("TRAVELATA_API_PASSWORD", "password")
    client = FakeClient(
        {
            "/directory/countries": {"result": [{"id": 92, "name": "Турция"}]},
            "/directory/departureCities": {"result": [{"id": 2, "name": "Москва"}]},
            "/directory/resorts": {"result": [{"id": "resort1", "name": "Анталья"}]},
            "/directory/hotelCategories": {"result": [{"id": "cat4", "name": "4*"}]},
            "/directory/meals": {"result": [{"id": "mealAI", "name": "AI"}]},
            "/statistic/cheapestTours": fixture("travelata.json"),
        }
    )
    tours = await TravelataScraper(client, {}).search(params)
    assert len(tours) == 1
    assert tours[0].hotel_name == "Sunrise Resort Hotel"
    assert tours[0].stars == 4
    assert tours[0].rating == 8.8
    assert tours[0].price_total == 140000


@pytest.mark.asyncio
async def test_sletat_adapter(params) -> None:
    row = fixture("sletat.json")["GetToursResult"]["Data"]["aaData"][0]
    tour = SletatScraper(FakeClient({}), {})._parse_row(row, params, 12345)
    assert tour.hotel_name == "Sunrise Resort Hotel"
    assert tour.rating == 8.8
    assert tour.resort == "Анталья"
    assert "offerId=987654" in tour.url


@pytest.mark.asyncio
async def test_tez_tour_adapter(params) -> None:
    client = FakeClient(
        {
            "/references": {
                "cities": [{"cityId": 345, "name": "Москва"}],
                "countries": [{"countryId": 1104, "name": "Турция"}],
                "currencies": [{"currencyId": 5561, "name": "RUB"}],
                "hotelClasses": [{"classId": 4, "name": "4*", "weight": 4}],
                "rAndBs": [{"rAndBId": 2424, "name": "AI", "weight": 2}],
            },
            "/byCountry": {"accommodations": [{"accommodationId": 2, "name": "2 ADT", "children": True}]},
            "/getResult": fixture("tez_tour.json"),
        }
    )
    tours = await TezTourScraper(client, {}).search(params)
    assert len(tours) == 1
    assert tours[0].hotel_name == "Sunrise Resort Hotel 4*"
    assert tours[0].url == "https://www.tez-tour.com/book/abc"
    assert tours[0].includes_transfer is True
    assert tours[0].includes_insurance is False


def test_tez_family_accommodation_and_rur(params) -> None:
    params.children_ages = [5]
    records = [
        {"accommodationId": 2, "adult": 2, "children": 0},
        {"accommodationId": 14317, "adult": 2, "children": 1},
    ]
    scraper = TezTourScraper(FakeClient({}), {})
    assert scraper._accommodation_id(records, params) == 14317
    assert scraper.to_rub(100, "RUR") == 100
    assert scraper.to_rub(100, "Р") == 100
