from __future__ import annotations

import asyncio
import logging
from datetime import time
from typing import Any

from models import SearchParams, Tour
from scrapers.base import (
    BaseScraper,
    as_float,
    find_named_record,
    parse_api_date,
    parse_stars,
    payload_records,
    record_id,
    record_name,
)

logger = logging.getLogger(__name__)

# Official partner API paths; update these constants if Travelata publishes a new version.
BASE_URL = "https://api-gateway.travelata.ru/partners"
DIRECTORIES = {
    "countries": "/directory/countries",
    "cities": "/directory/departureCities",
    "resorts": "/directory/resorts",
    "categories": "/directory/hotelCategories",
    "meals": "/directory/meals",
}
CHEAPEST_TOURS = "/statistic/cheapestTours"


class TravelataScraper(BaseScraper):
    name = "travelata"

    def __init__(self, client: Any, settings: dict[str, Any]) -> None:
        super().__init__(client, settings)
        self.base_url = str(settings.get("base_url", BASE_URL)).rstrip("/")
        self._auth: tuple[str, str] | None = None

    async def search(self, params: SearchParams) -> list[Tour]:
        username, password = self.require_credentials("TRAVELATA_API_USERNAME", "TRAVELATA_API_PASSWORD")
        self._auth = (username, password)
        countries, cities = await asyncio.gather(
            self._directory("countries"), self._directory("cities")
        )
        country_id = record_id(find_named_record(countries, params.country, self.name))
        city_id = record_id(find_named_record(cities, params.departure_city, self.name))
        resorts, categories, meals = await asyncio.gather(
            self._directory("resorts"), self._directory("categories"), self._directory("meals")
        )
        resort_names = {str(record_id(item)): record_name(item) for item in resorts}
        category_names = {str(record_id(item)): record_name(item) for item in categories}
        meal_names = {str(record_id(item)): record_name(item) for item in meals}

        query: list[tuple[str, Any]] = [
            ("countries[]", country_id),
            ("departureCity", city_id),
            ("nightRange[from]", params.nights_min),
            ("nightRange[to]", params.nights_max),
            ("touristGroup[adults]", params.adults),
            ("touristGroup[kids]", sum(age >= 2 for age in params.children_ages)),
            ("touristGroup[infants]", sum(age < 2 for age in params.children_ages)),
            ("checkInDateRange[from]", params.date_from.isoformat()),
            ("checkInDateRange[to]", params.date_to.isoformat()),
        ]
        query.extend(
            ("touristGroup[kidsAges][]", age) for age in params.children_ages if age >= 2
        )
        payload = await self.client.get_json(
            f"{self.base_url}{CHEAPEST_TOURS}",
            params=query,
            auth=self._auth,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        records = self._records(payload)
        tours: list[Tour] = []
        for item in records:
            try:
                tours.append(self._parse_tour(item, params, resort_names, category_names, meal_names))
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("%s: пропущена некорректная запись: %s", self.name, exc)
        return tours

    async def _directory(self, name: str) -> list[dict[str, Any]]:
        payload = await self.client.get_json(
            f"{self.base_url}{DIRECTORIES[name]}",
            auth=self._auth,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        return self._records(payload)

    @staticmethod
    def _records(payload: Any) -> list[dict[str, Any]]:
        return (
            payload_records(payload, "result")
            or payload_records(payload, "data")
            or payload_records(payload)
        )

    def _parse_tour(
        self,
        item: dict[str, Any],
        params: SearchParams,
        resorts: dict[str, str],
        categories: dict[str, str],
        meals: dict[str, str],
    ) -> Tour:
        price = as_float(item.get("price", item.get("priceTotal")))
        if price is None:
            raise ValueError("price is missing")
        category_value = item.get("hotelCategory")
        category_name = categories.get(str(category_value), str(category_value or ""))
        meal_value = item.get("mealId", item.get("meal"))
        rating = as_float(item.get("hotelRating", item.get("rating")))
        if rating is not None and rating <= 5:
            rating *= 2
        departure_time = self._time(item.get("departureTime"))
        return self.make_tour(
            params,
            price,
            str(item.get("currency", "RUB")),
            url=str(item.get("tourPageUrl") or item.get("searchPageUrl") or ""),
            resort=str(item.get("resortName") or resorts.get(str(item.get("resortId")), "")),
            hotel_name=str(item["hotelName"]),
            stars=parse_stars(category_name),
            rating=rating,
            meal=str(item.get("mealName") or meals.get(str(meal_value), meal_value or "")) or None,
            departure_date=parse_api_date(
                item.get("checkinDate", item.get("checkInDate", item.get("dateFrom")))
            ).date(),
            nights=int(item.get("nights", item.get("nightCount"))),
            room_type=str(item.get("roomType") or item.get("roomName") or ""),
            includes_flight=True,
            includes_transfer=bool(item.get("transferIncluded", True)),
            includes_insurance=self._bool(item.get("insuranceIncluded")),
            luggage=str(item["baggage"]) if item.get("baggage") is not None else None,
            direct_flight=self._bool(item.get("directFlight")),
            departure_time=departure_time,
        )

    @staticmethod
    def _bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if str(value).casefold() in {"1", "true", "yes", "да"}:
            return True
        if str(value).casefold() in {"0", "false", "no", "нет"}:
            return False
        return None

    @staticmethod
    def _time(value: Any) -> time | None:
        if not value:
            return None
        try:
            return time.fromisoformat(str(value)[:8])
        except ValueError:
            return None
