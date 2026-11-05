from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from errors import ScraperError
from models import SearchParams, Tour
from normalizer import normalize_meal
from scrapers.base import (
    BaseScraper,
    as_float,
    find_named_record,
    parse_api_date,
    parse_stars,
    record_id,
    record_name,
)

logger = logging.getLogger(__name__)

# Official public JSON gateway; positional response fields follow the published schema.
BASE_URL = "https://search.tez-tour.com/tariffsearch"
BOOKING_BASE_URL = "https://www.tez-tour.com/"


class TezTourScraper(BaseScraper):
    name = "tez_tour"

    def __init__(self, client: Any, settings: dict[str, Any]) -> None:
        super().__init__(client, settings)
        self.base_url = str(settings.get("base_url", BASE_URL)).rstrip("/")

    async def search(self, params: SearchParams) -> list[Tour]:
        references = await self._get("references")
        city_id = record_id(find_named_record(references.get("cities", []), params.departure_city, self.name))
        country_id = record_id(find_named_record(references.get("countries", []), params.country, self.name))
        currency_id = self._currency_id(references.get("currencies", []))
        hotel_class_id = self._hotel_class_id(references.get("hotelClasses", []), params.min_stars)
        board_id = self._board_id(references.get("rAndBs", []), params.meal)
        accommodations = await self._accommodations(country_id, city_id)
        accommodation_id = self._accommodation_id(accommodations, params)

        tours: list[Tour] = []
        for date_from, date_to in self._date_chunks(params):
            for nights_min, nights_max in self._night_chunks(params):
                query: dict[str, Any] = {
                    "countryId": country_id,
                    "cityId": city_id,
                    "priceMin": 0,
                    "priceMax": int(params.max_budget or self.settings.get("default_price_max", 10_000_000)),
                    "after": date_from.strftime("%d.%m.%Y"),
                    "before": date_to.strftime("%d.%m.%Y"),
                    "currency": currency_id,
                    "nightsMin": nights_min,
                    "nightsMax": nights_max,
                    "accommodationId": accommodation_id,
                    "hotelClassId": hotel_class_id,
                    "hotelClassBetter": "true",
                    "rAndBId": board_id,
                    "rAndBBetter": "false" if params.meal else "true",
                    "tourType": 1,
                    "locale": "ru",
                    "xml": "false",
                    "formatResult": "false",
                    "showArrivalRegion": "true",
                }
                if params.children_ages:
                    query["birthdays"] = ",".join(
                        (params.date_from - timedelta(days=365 * age)).strftime("%d.%m.%Y")
                        for age in params.children_ages
                    )
                payload = await self._get("getResult", query)
                if payload.get("success") is False:
                    raise ScraperError(f"{self.name}: API вернул success=false")
                for row in payload.get("data", []):
                    try:
                        tours.append(self._parse_row(row, params))
                    except (ValueError, TypeError, KeyError, IndexError) as exc:
                        logger.warning("%s: пропущена некорректная запись: %s", self.name, exc)
        return tours

    async def _get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        query = {"xml": "false", "formatResult": "false", "locale": "ru", **(params or {})}
        return await self.client.get_json(
            f"{self.base_url}/{method}",
            params=query,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )

    async def _accommodations(self, country_id: Any, city_id: Any) -> list[dict[str, Any]]:
        body = await self.client.get_text(
            f"{self.base_url}/accommodations",
            params={
                "countryId": country_id,
                "cityId": city_id,
                "locale": "ru",
                "xml": "true",
                "formatResult": "false",
            },
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ScraperError(f"{self.name}: неверный XML справочника размещения") from exc
        return [
            {
                "accommodationId": item.findtext("accommodationId"),
                "name": item.findtext("name", ""),
                "adult": item.findtext("adult", "0"),
                "children": item.findtext("children", "0"),
            }
            for item in root.findall(".//accommodation")
        ]

    @staticmethod
    def _currency_id(records: list[dict[str, Any]]) -> Any:
        for record in records:
            text = f"{record_name(record)} {record.get('code', '')}".casefold()
            if "rub" in text or "руб" in text:
                return record_id(record)
        raise ScraperError("tez_tour: рубли отсутствуют в справочнике валют")

    @staticmethod
    def _hotel_class_id(records: list[dict[str, Any]], min_stars: int) -> Any:
        suitable = [item for item in records if (parse_stars(record_name(item)) or 0) >= min_stars]
        candidates = suitable or records
        if not candidates:
            raise ScraperError("tez_tour: пустой справочник категорий отелей")
        return record_id(min(candidates, key=lambda item: (parse_stars(record_name(item)) or 0, item.get("weight", 0))))

    def _board_id(self, records: list[dict[str, Any]], meal: str | None) -> Any:
        if meal:
            for record in records:
                text = f"{record_name(record)} {record.get('code', '')}"
                if normalize_meal(text) == meal:
                    return record_id(record)
            raise ScraperError(f"tez_tour: питание {meal} отсутствует в справочнике")
        if not records:
            raise ScraperError("tez_tour: пустой справочник питания")
        return record_id(min(records, key=lambda item: item.get("weight", 0)))

    @staticmethod
    def _accommodation_id(records: list[dict[str, Any]], params: SearchParams) -> Any:
        children_count = len(params.children_ages)
        for record in records:
            adults = int(float(record.get("adult") or 0))
            children = int(float(record.get("children") or 0))
            if adults == params.adults and children == children_count:
                return record_id(record)
        candidates: list[tuple[int, int, int, Any]] = []
        for record in records:
            adults = int(float(record.get("adult") or 0))
            children = int(float(record.get("children") or 0))
            capacity = adults + children
            if capacity >= params.traveler_count and children >= children_count:
                candidates.append((capacity - params.traveler_count, children - children_count, capacity, record_id(record)))
        if candidates:
            return min(candidates)[3]
        if params.traveler_count == 2 and not params.children_ages:
            return 2
        raise ScraperError("tez_tour: не найдено размещение для указанного состава туристов")

    @staticmethod
    def _date_chunks(params: SearchParams) -> list[tuple[Any, Any]]:
        chunks = []
        start = params.date_from
        while start <= params.date_to:
            end = min(start + timedelta(days=30), params.date_to)
            chunks.append((start, end))
            start = end + timedelta(days=1)
        return chunks

    @staticmethod
    def _night_chunks(params: SearchParams) -> list[tuple[int, int]]:
        return [
            (start, min(start + 8, params.nights_max))
            for start in range(params.nights_min, params.nights_max + 1, 9)
        ]

    def _parse_row(self, row: list[Any], params: SearchParams) -> Tour:
        price_data = row[10]
        price = as_float(price_data.get("total"))
        if price is None:
            raise ValueError("price is missing")
        booking_links = row[11]
        if not booking_links:
            raise ValueError("booking URL is missing")
        url = urljoin(BOOKING_BASE_URL, str(booking_links[0][0]))
        price_types = price_data.get("priceTypes", [])
        return self.make_tour(
            params,
            price,
            str(price_data.get("currency", "RUB")),
            url=url,
            resort=str(row[5][0] or row[5][4] or ""),
            hotel_name=str(row[6][1]),
            stars=parse_stars(row[6][1]),
            rating=None,
            meal=str(row[7][0] or row[7][1] or "") or None,
            departure_date=parse_api_date(row[0]).date(),
            nights=int(row[3]),
            room_type=str(row[8][1] or ""),
            includes_flight=self._price_type(price_types, 1),
            includes_transfer=self._price_type(price_types, 2),
            includes_insurance=self._price_type(price_types, 3),
        )

    @staticmethod
    def _price_type(values: Any, index: int) -> bool | None:
        if isinstance(values, list) and len(values) > index:
            return bool(values[index])
        if isinstance(values, dict):
            key = ("residence", "flight", "transfer", "insurance")[index]
            return bool(values[key]) if key in values else None
        return None
