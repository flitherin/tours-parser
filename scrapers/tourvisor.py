from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

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

# Official paid gateway; endpoint paths are stable but response envelopes are versioned.
BASE_URL = "https://tourvisor.ru/xml"
PUBLIC_SEARCH_URL = "https://tourvisor.ru/search.php"


class TourvisorScraper(BaseScraper):
    name = "tourvisor"

    async def search(self, params: SearchParams) -> list[Tour]:
        if len(params.children_ages) > 3:
            raise ScraperError("tourvisor: шлюз поддерживает не более трёх детей")
        self._login, self._password = self.require_credentials("TOURVISOR_API_LOGIN", "TOURVISOR_API_PASSWORD")
        references = await self._request(
            "list.php", {"type": "departure,country"}, use_cache=True
        )
        cities = self._records(references, "departure")
        countries = self._records(references, "country")
        city_id = record_id(find_named_record(cities, params.departure_city, self.name))
        country_id = record_id(find_named_record(countries, params.country, self.name))

        tours: list[Tour] = []
        for date_from, date_to in self._date_chunks(params):
            request_id = await self._start_search(params, city_id, country_id, date_from, date_to)
            await self._wait_until_ready(request_id)
            tours.extend(await self._download_results(params, request_id))
        return tours

    async def _request(self, method: str, params: dict[str, Any], use_cache: bool = False) -> Any:
        query = {
            "authlogin": self._login,
            "authpass": self._password,
            "format": "json",
            **params,
        }
        return await self.client.get_json(
            f"{BASE_URL}/{method}",
            params=query,
            use_cache=use_cache,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )

    @staticmethod
    def _records(payload: Any, key: str) -> list[dict[str, Any]]:
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, dict) and key in data:
            value = data[key]
            if isinstance(value, dict):
                value = value.get("items", value.get("item", value))
            if isinstance(value, list):
                records = []
                for item in value:
                    if isinstance(item, dict):
                        record = dict(item)
                        if "n" in record and "name" not in record:
                            record["name"] = record["n"]
                        records.append(record)
                    elif isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
                        records.append({"id": item[0], **item[1], "name": item[1].get("n", item[1].get("name", ""))})
                return records
        return payload_records(data, key) or payload_records(data)

    async def _start_search(
        self,
        params: SearchParams,
        city_id: Any,
        country_id: Any,
        date_from: Any,
        date_to: Any,
    ) -> str:
        query: dict[str, Any] = {
            "departure": city_id,
            "country": country_id,
            "datefrom": date_from.strftime("%d.%m.%Y"),
            "dateto": date_to.strftime("%d.%m.%Y"),
            "nightsfrom": params.nights_min,
            "nightsto": params.nights_max,
            "adults": params.adults,
            "child": len(params.children_ages),
            "currency": 0,
            "stars": params.min_stars,
            "starsbetter": 1,
            "pricetype": 0,
        }
        if params.meal:
            query.update({"meal": params.meal, "mealbetter": 0})
        if params.max_budget:
            query["priceto"] = int(params.max_budget)
        if params.min_rating:
            thresholds = [(4.5, 5), (4.0, 4), (3.5, 3), (3.0, 2)]
            query["rating"] = next((code for value, code in thresholds if params.min_rating >= value), 0)
        for index, age in enumerate(params.children_ages, 1):
            query[f"childage{index}"] = max(1, age)
        payload = await self._request("search.php", query)
        request_id = self._request_id(payload)
        if not request_id:
            raise ScraperError(f"{self.name}: API не вернул requestid")
        return str(request_id)

    async def _wait_until_ready(self, request_id: str) -> None:
        await asyncio.sleep(float(self.settings.get("initial_poll_delay", 3)))
        for _ in range(int(self.settings.get("max_polls", 30))):
            payload = await self._request(
                "result.php", {"requestid": request_id, "type": "status", "operatorstatus": 0}
            )
            status = payload.get("data", {}).get("status", {})
            if status.get("state") == "finished":
                return
            await asyncio.sleep(float(self.settings.get("poll_interval", 2)))
        raise ScraperError(f"{self.name}: поиск не завершился за отведённое время")

    async def _download_results(self, params: SearchParams, request_id: str) -> list[Tour]:
        result: list[Tour] = []
        page_size = int(self.settings.get("page_size", 100))
        for page in range(1, int(self.settings.get("max_pages", 3)) + 1):
            payload = await self._request(
                "result.php",
                {
                    "requestid": request_id,
                    "type": "result",
                    "page": page,
                    "onpage": page_size,
                    "nodescription": 1,
                },
            )
            data = payload.get("data", {})
            hotels = self._as_list(data.get("result", {}).get("hotel", []))
            for hotel in hotels:
                for item in self._as_list(hotel.get("tours", {}).get("tour", [])):
                    try:
                        result.append(self._parse_tour(hotel, item, params, request_id))
                    except (ValueError, TypeError, KeyError) as exc:
                        logger.warning("%s: пропущена запись: %s", self.name, exc)
            if len(hotels) < page_size:
                break
        return result

    def _parse_tour(
        self,
        hotel: dict[str, Any],
        item: dict[str, Any],
        params: SearchParams,
        request_id: str,
    ) -> Tour:
        price = as_float(item.get("price"))
        if price is None:
            raise ValueError("price is missing")
        rating = as_float(hotel.get("hotelrating"))
        if rating is not None and 0 < rating <= 5:
            rating *= 2
        url = str(
            item.get("operatorlink")
            or item.get("url")
            or f"{PUBLIC_SEARCH_URL}?requestid={request_id}"
        )
        return self.make_tour(
            params,
            price,
            str(item.get("currency", "RUB")),
            url=url,
            resort=str(hotel.get("regionname") or hotel.get("subregionname") or ""),
            hotel_name=str(hotel["hotelname"]),
            stars=parse_stars(hotel.get("hotelstars")),
            rating=rating or None,
            meal=str(item.get("meal") or item.get("mealrussian") or "") or None,
            departure_date=parse_api_date(item["flydate"]).date(),
            nights=int(item["nights"]),
            room_type=str(item.get("room") or item.get("placement") or ""),
            includes_flight=True,
            includes_transfer=None,
            includes_insurance=None,
        )

    @staticmethod
    def _request_id(payload: Any) -> Any:
        if isinstance(payload, dict):
            if payload.get("requestid"):
                return payload["requestid"]
            for key in ("result", "data"):
                value = payload.get(key)
                if isinstance(value, dict) and value.get("requestid"):
                    return value["requestid"]
        return None

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return [value] if isinstance(value, dict) else []

    @staticmethod
    def _date_chunks(params: SearchParams) -> list[tuple[Any, Any]]:
        chunks = []
        start = params.date_from
        while start <= params.date_to:
            end = min(start + timedelta(days=14), params.date_to)
            chunks.append((start, end))
            start = end + timedelta(days=1)
        return chunks
