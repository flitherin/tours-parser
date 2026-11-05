from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlencode

from errors import ScraperError
from models import SearchParams, Tour
from scrapers.base import (
    BaseScraper,
    as_float,
    find_named_record,
    parse_api_date,
    parse_stars,
    payload_records,
    record_id,
)

logger = logging.getLogger(__name__)

# Official JSON gateway and aaData layout from wiki.sletat.ru.
BASE_URL = "https://module.sletat.ru/Main.svc"
OFFER_URL = "https://sletat.ru/tour/"
IDX_OPERATOR = 1
IDX_HOTEL = 7
IDX_STARS = 8
IDX_ROOM = 9
IDX_MEAL = 10
IDX_DATE = 12
IDX_NIGHTS = 14
IDX_PRICE_TEXT = 15
IDX_RESORT = 31
IDX_RATING = 35
IDX_PRICE = 42
IDX_CURRENCY = 43


class SletatScraper(BaseScraper):
    name = "sletat"

    def __init__(self, client: Any, settings: dict[str, Any]) -> None:
        super().__init__(client, settings)
        self.base_url = str(settings.get("base_url", BASE_URL)).rstrip("/")
        self._login = ""
        self._password = ""

    async def search(self, params: SearchParams) -> list[Tour]:
        self._login, self._password = self.require_credentials("SLETAT_API_LOGIN", "SLETAT_API_PASSWORD")
        cities_payload = await self._get("GetDepartCities")
        cities = payload_records(cities_payload, "GetDepartCitiesResult")
        city = find_named_record(cities, params.departure_city, self.name)
        city_id = record_id(city)
        countries_payload = await self._get("GetCountries", {"townFromId": city_id})
        countries = payload_records(countries_payload, "GetCountriesResult")
        country_id = record_id(find_named_record(countries, params.country, self.name))

        query: dict[str, Any] = {
            "cityFromId": city_id,
            "countryId": country_id,
            "s_adults": params.adults,
            "s_kids": len(params.children_ages),
            "s_kids_ages": ",".join(map(str, params.children_ages)),
            "s_nightsMin": params.nights_min,
            "s_nightsMax": params.nights_max,
            "s_dateFrom": params.date_from.strftime("%d.%m.%Y"),
            "s_dateTo": params.date_to.strftime("%d.%m.%Y"),
            "currencyAlias": "RUB",
            "s_hasTickets": "true",
            "s_ticketsIncluded": "true",
            "pageSize": int(self.settings.get("page_size", 100)),
        }
        if params.max_budget:
            query["priceMax"] = int(params.max_budget)
        result = await self._get("GetTours", query, use_cache=False)
        result = await self._wait_for_result(result)
        rows = result.get("Data", {}).get("aaData", [])
        request_id = result.get("RequestId", "")
        tours: list[Tour] = []
        for row in rows:
            try:
                tours.append(self._parse_row(row, params, request_id))
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                logger.warning("%s: пропущена некорректная запись: %s", self.name, exc)
        return tours

    async def _get(self, method: str, params: dict[str, Any] | None = None, use_cache: bool = True) -> Any:
        query = dict(params or {})
        query.update({"login": self._login, "password": self._password})
        payload = await self.client.get_json(
            f"{self.base_url}/{method}",
            params=query,
            use_cache=use_cache,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        if isinstance(payload, dict) and payload.get("Error"):
            raise ScraperError(f"{self.name}: {payload['Error']}")
        return payload

    async def _wait_for_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("GetToursResult", payload)
        max_polls = int(self.settings.get("max_polls", 20))
        interval = float(self.settings.get("poll_interval", 2))
        for _ in range(max_polls):
            if result.get("IsTimeout"):
                raise ScraperError(f"{self.name}: поиск завершился тайм-аутом на стороне API")
            if result.get("IsProcessed", True):
                return result
            request_id = result.get("RequestId")
            if not request_id:
                raise ScraperError(f"{self.name}: API не вернул RequestId")
            await asyncio.sleep(interval)
            payload = await self._get(
                "GetTours", {"requestId": request_id, "updateResult": 1}, use_cache=False
            )
            result = payload.get("GetToursResult", payload)
        raise ScraperError(f"{self.name}: результаты не готовы после {max_polls} попыток")

    def _parse_row(self, row: list[Any], params: SearchParams, request_id: Any) -> Tour:
        price = as_float(row[IDX_PRICE]) or as_float(row[IDX_PRICE_TEXT])
        if price is None:
            raise ValueError("price is missing")
        currency = str(row[IDX_CURRENCY]) if row[IDX_CURRENCY] else "RUB"
        if not currency.isalpha() or len(currency) != 3:
            currency = "RUB"
        url = f"{OFFER_URL}?{urlencode({'sourceId': row[IDX_OPERATOR], 'offerId': row[0], 'requestId': request_id, 'origin': 'search'})}"
        return self.make_tour(
            params,
            price,
            currency,
            url=url,
            resort=str(row[IDX_RESORT] or ""),
            hotel_name=str(row[IDX_HOTEL]),
            stars=parse_stars(row[IDX_STARS]),
            rating=as_float(row[IDX_RATING]),
            meal=str(row[IDX_MEAL] or "") or None,
            departure_date=parse_api_date(row[IDX_DATE]).date(),
            nights=int(row[IDX_NIGHTS]),
            room_type=str(row[IDX_ROOM] or ""),
            includes_flight=True,
            includes_transfer=None,
            includes_insurance=None,
        )
