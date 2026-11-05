from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlencode

from pydantic import BaseModel, Field, ValidationError

from errors import ScraperError
from models import SearchParams, Tour
from normalizer import filter_tours
from scrapers.base import BaseScraper, parse_stars

logger = logging.getLogger(__name__)

# SAMO API 1.0: https://dokuwiki.samo.ru/doku.php?id=onlinest:api:searchtour:prices
EMPTY_PRICE_CODES = {2, 3, 2101, 2105, 2110}
RANGE_ERROR_CODES = {101, 102, 105}
RUB_CODES = {"RUB", "RUR"}


class SamoOptions(BaseModel):
    date_chunk_days: int = Field(default=7, ge=1, le=14)
    nights_chunk_size: int = Field(default=4, ge=1, le=8)
    max_pages: int = Field(default=5, ge=1, le=50)
    max_requests: int = Field(default=60, ge=4, le=500)


class SamoAPIError(ScraperError):
    def __init__(self, source: str, action: str, code: int | str) -> None:
        self.code = code
        hint = "; уменьшите диапазон дат/ночей" if code in RANGE_ERROR_CODES else ""
        super().__init__(f"{source}: {action}, ошибка API {code}{hint}")


class SamoScraper(BaseScraper):
    """Read-only package search through an operator's licensed SAMO API."""

    base_url: ClassVar[str]
    token_env: ClassVar[str]

    async def search(self, params: SearchParams) -> list[Tour]:
        if len(params.children_ages) > 3:
            raise ScraperError(f"{self.name}: API принимает не более трёх возрастов детей")
        self._token = self.require_credentials(self.token_env)[0]
        self._options = SamoOptions.model_validate(self.settings)
        self._requests = 0
        departures = await self._directory("SearchTour_TOWNFROMS", {})
        origin = self._find_place(departures, params.departure_city)
        country = self._find_place(
            await self._directory("SearchTour_STATES", {"TOWNFROMINC": origin["id"]}),
            params.country,
        )
        route = {"TOWNFROMINC": origin["id"], "STATEINC": country["id"]}
        currencies = await self._directory("SearchTour_CURRENCIES", route)
        rub = next(
            (item for item in currencies if str(item.get("currencyISO") or item.get("alias")).upper() in RUB_CODES),
            None,
        )
        if rub is None:
            raise ScraperError(f"{self.name}: API не предоставляет цены в RUB")

        offers: dict[str, Tour] = {}
        for start, end in self._date_chunks(params.date_from, params.date_to):
            for nights in range(params.nights_min, params.nights_max + 1, self._options.nights_chunk_size):
                query = {
                    **route,
                    "CHECKIN_BEG": start.strftime("%Y%m%d"),
                    "CHECKIN_END": end.strftime("%Y%m%d"),
                    "NIGHTS_FROM": nights,
                    "NIGHTS_TILL": min(params.nights_max, nights + self._options.nights_chunk_size - 1),
                    "ADULT": params.adults,
                    "CHILD": len(params.children_ages),
                    "AGES": ",".join(map(str, params.children_ages)),
                    "CURRENCY": rub["id"],
                    "FREIGHT": 1,
                    "FILTER": 1,
                    "PARTITION_PRICE": 255,
                    "DYN_SEPARATE": 0,
                    "SORT": "ASC",
                }
                if params.max_budget is not None:
                    query["COSTMAX"] = params.max_budget
                for item in await self._expanded_prices(query):
                    try:
                        tour = self._parse_offer(item, params, query)
                        if tour is not None:
                            offers[str(item["id"])] = tour
                    except (ValueError, KeyError, TypeError, ValidationError):
                        raise ScraperError(f"{self.name}: изменилась схема предложения SearchTour_PRICES") from None
        return filter_tours(offers.values(), params)

    async def _call(self, action: str, fields: dict[str, Any]) -> Any:
        if self._requests >= self._options.max_requests:
            raise ScraperError(f"{self.name}: достигнут лимит запросов; сузьте поиск или увеличьте max_requests")
        self._requests += 1
        payload = await self.client.request_json(
            "POST",
            f"{self.base_url}/export/default.php",
            params={"samo_action": "api", "version": "1.0", "type": "json", "action": action},
            form_data={**fields, "oauth_token": self._token},
            use_cache=True,
            check_robots=True,
        )
        if not isinstance(payload, dict):
            raise ScraperError(f"{self.name}: вместо объекта API получен другой тип данных")
        result = payload.get(action, payload)
        if isinstance(result, dict) and result.get("error") not in (None, "", 0, "0"):
            raw_code = result["error"]
            try:
                code: int | str = int(raw_code)
            except (ValueError, TypeError):
                code = "unknown"
            raise SamoAPIError(self.name, action, code)
        if action not in payload:
            raise ScraperError(f"{self.name}: отсутствует ключ {action} в ответе API")
        return result

    async def _directory(self, action: str, fields: dict[str, Any]) -> list[dict[str, Any]]:
        records = await self._call(action, fields)
        if not isinstance(records, list) or any(not isinstance(item, dict) or "id" not in item for item in records):
            raise ScraperError(f"{self.name}: неверная схема справочника {action}")
        return records

    async def _pages(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, self._options.max_pages + 1):
            try:
                result = await self._call("SearchTour_PRICES", {**query, "PRICEPAGE": page})
            except SamoAPIError as exc:
                if exc.code in EMPTY_PRICE_CODES:
                    return records
                raise
            if not isinstance(result, dict) or not isinstance(result.get("prices"), list):
                raise ScraperError(f"{self.name}: API не вернул массив prices")
            rows = result["prices"]
            if not rows:
                return records
            if any(not isinstance(item, dict) or not item.get("id") for item in rows):
                raise ScraperError(f"{self.name}: предложение без идентификатора")
            ids = {str(item["id"]) for item in rows}
            if ids <= seen:
                raise ScraperError(f"{self.name}: API повторяет страницы; результаты нельзя считать полными")
            records.extend(item for item in rows if str(item["id"]) not in seen)
            seen.update(ids)
        logger.warning("%s: получены первые %d страниц; выборка может быть неполной", self.name, self._options.max_pages)
        return records

    async def _expanded_prices(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        queue = [(item, query) for item in await self._pages(query)]
        groups: set[str] = set()
        offers: list[dict[str, Any]] = []
        while queue:
            item, parent_query = queue.pop(0)
            if str(item.get("grouped", 0)) != "1":
                offers.append(item)
                continue
            key = str(item["id"])
            if key in groups or not item.get("hotelKey"):
                raise ScraperError(f"{self.name}: некорректная группа цен в API")
            groups.add(key)
            expanded_query = {**parent_query, "CATCLAIM": key, "HOTELS": item["hotelKey"]}
            expanded_query.pop("PARTITION_PRICE", None)
            queue.extend((child, expanded_query) for child in await self._pages(expanded_query))
        return offers

    def _parse_offer(self, item: dict[str, Any], params: SearchParams, query: dict[str, Any]) -> Tour | None:
        # Ground-only, rail/bus and multi-hotel itineraries are not comparable to air packages.
        if str(item.get("packetType")) != "0" or str(item.get("transportCodeKey")) != "1":
            return None
        if str(item.get("crossTour", 0)) == "1" or int(item.get("hotelCount") or 1) != 1:
            return None
        if str(item.get("bron", 0)) != "1":
            return None
        if str(item.get("stopSale", "")).lower() in {"1", "true"}:
            return None
        if str(item.get("townFromKey")) != str(query["TOWNFROMINC"]):
            return None
        if str(item.get("stateKey")) != str(query["STATEINC"]):
            return None
        if int(item["adult"]) != params.adults or int(item["child"]) != len(params.children_ages):
            return None
        departure = self._date(item["checkIn"])
        nights = int(item["nights"])
        if not params.date_from <= departure <= params.date_to or not params.nights_min <= nights <= params.nights_max:
            return None
        price = self._rub_price(item, query["CURRENCY"])
        link_fields = {
            "CATCLAIM": str(item["id"]),
            "TOWNFROMINC": query["TOWNFROMINC"],
            "STATEINC": query["STATEINC"],
            "CURRENCY": query["CURRENCY"],
            "PACKET": 0,
        }
        if item.get("stateFromKey") is not None:
            link_fields["STATEFROM"] = item["stateFromKey"]
        hotel_name = str(item["hotel"]).strip()
        if not hotel_name:
            raise ValueError("empty hotel")
        return self.make_tour(
            params,
            price,
            "RUB",
            url=f"{self.base_url}/bron?{urlencode(link_fields)}",
            hotel_name=hotel_name,
            resort=str(item.get("town") or ""),
            stars=self._stars(item.get("star")),
            rating=None,
            meal=str(item.get("meal") or "") or None,
            departure_date=departure,
            nights=nights,
            room_type=" / ".join(filter(None, [str(item.get("room") or ""), str(item.get("htPlace") or "")])),
            includes_flight=True,
            includes_transfer=None,
            includes_insurance=None,
            luggage=None,
            direct_flight=None,
            departure_time=None,
        )

    @staticmethod
    def _rub_price(item: dict[str, Any], currency_id: Any) -> float:
        converted = item.get("convertedPrice")
        if converted not in (None, "") and str(item.get("convertedCurrencyKey")) == str(currency_id):
            if isinstance(converted, (float, int)):
                return SamoScraper._amount(converted)
            match = re.fullmatch(r"\s*([\d\s.,]+)\s*(?:RUB|RUR|₽|руб\.?)?\s*", str(converted), re.IGNORECASE)
            if match:
                return SamoScraper._amount(match.group(1))
            raise ValueError("unexpected converted currency")
        if str(item.get("currency")).upper() in RUB_CODES:
            return SamoScraper._amount(item["price"])
        raise ValueError("missing operator RUB price")

    @staticmethod
    def _amount(value: Any) -> float:
        number = float(re.sub(r"\s", "", str(value)).replace(",", "."))
        if not math.isfinite(number) or number <= 0:
            raise ValueError("invalid price")
        return number

    @staticmethod
    def _date(value: Any) -> date:
        text = str(value)
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text)

    @staticmethod
    def _stars(value: Any) -> int | None:
        text = str(value or "").strip()
        if re.fullmatch(r"\*{1,5}", text):
            return len(text)
        return parse_stars(text)

    def _date_chunks(self, start: date, end: date) -> list[tuple[date, date]]:
        chunks = []
        while start <= end:
            last = min(end, start + timedelta(days=self._options.date_chunk_days - 1))
            chunks.append((start, last))
            start = last + timedelta(days=1)
        return chunks

    def _find_place(self, records: list[dict[str, Any]], name: str) -> dict[str, Any]:
        canonical = self._place_key(name)
        matches = [
            item for item in records
            if any(self._place_key(str(item.get(field) or "")) == canonical for field in ("name", "nameAlt"))
        ]
        if len(matches) != 1:
            raise ScraperError(f"{self.name}: справочник не содержит однозначного соответствия «{name}»")
        return matches[0]

    @staticmethod
    def _place_key(value: str) -> str:
        key = re.sub(r"[\s.-]+", " ", value.casefold().replace("ё", "е")).strip()
        aliases = {
            "moscow": "москва", "st petersburg": "санкт петербург",
            "saint petersburg": "санкт петербург", "екатеринбург": "екатеринбург",
            "yekaterinburg": "екатеринбург", "турция": "турция", "turkey": "турция",
            "египет": "египет", "egypt": "египет", "thailand": "таиланд", "тайланд": "таиланд",
            "tunisia": "тунис", "uae": "оаэ", "u a e": "оаэ",
            "united arab emirates": "оаэ", "объединенные арабские эмираты": "оаэ",
        }
        return aliases.get(key, key)
