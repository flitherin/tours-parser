from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from errors import ScraperError
from models import SearchParams, Tour
from normalizer import normalize_meal
from scrapers.base import BaseScraper, as_float, parse_api_date, parse_stars

logger = logging.getLogger(__name__)

# Official JSON price gateway; only the separate login method uses form encoding.
EXPORT_URL = "https://export.bgoperator.ru"
LOGIN_URL = "https://login.bgoperator.ru/auth"
CITY_REFERENCE_URL = f"{EXPORT_URL}/auto/homepage-124331253701.js"
GZIP_HEADERS = {"Accept-Encoding": "gzip"}


class BiblioGlobusScraper(BaseScraper):
    name = "biblio_globus"

    async def search(self, params: SearchParams) -> list[Tour]:
        await self._authenticate()
        countries, cities = await self._references()
        country = self._find_place(countries, params.country)
        city = self._find_place(cities, params.departure_city)
        country_index = countries.index(country)
        transport_flags = cities[cities.index(city)][1].get("t", [])
        if transport_flags and transport_flags[country_index] == 1:
            raise ScraperError(f"{self.name}: для маршрута доступны только наземные цены")

        files = await self._get_json(
            "/yandex",
            params={
                "action": "files",
                "flt": country[0],
                "flt2": city[0],
                "xml": 11,
            },
        )
        entries = [
            item
            for item in files.get("entries", [])
            if self._price_list_matches(item, params) and not item.get("stop")
        ][: int(self.settings.get("max_price_lists", 20))]
        tours: list[Tour] = []
        for entry in entries:
            url = self._price_url(str(entry["url"]), params)
            payload = await self._get_json(url)
            for tour in await self._parse_price_list(payload, params):
                tours.append(tour)
                if len(tours) >= int(self.settings.get("max_offers", 500)):
                    return tours
        return tours

    async def _authenticate(self) -> None:
        login, password = self.require_credentials("BIBLIO_GLOBUS_LOGIN", "BIBLIO_GLOBUS_PASSWORD")
        await self.client.request_text(
            "POST",
            LOGIN_URL,
            form_data={"login": login, "pwd": password},
            headers=GZIP_HEADERS,
            use_cache=False,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if url.startswith("http://"):
            url = "https://" + url.removeprefix("http://")
        return await self.client.get_json(
            url,
            params=params,
            headers=GZIP_HEADERS,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )

    async def _references(self) -> tuple[list[Any], list[Any]]:
        body = await self.client.get_text(
            CITY_REFERENCE_URL,
            headers=GZIP_HEADERS,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        match = re.search(r"var\s+BG_FLTX\s*=\s*(.*?)\s*;", body, re.DOTALL)
        if not match:
            raise ScraperError(f"{self.name}: не найден объект BG_FLTX")
        data = json.loads(match.group(1))
        return data["flt"], data["flt2"]

    def _find_place(self, records: list[Any], name: str) -> Any:
        target = self._clean(name)
        aliases = {
            "оаэ": {"оаэ", "объединенные арабские эмираты", "uae"},
            "сша": {"сша", "соединенные штаты", "usa"},
            "доминикана": {"доминикана", "доминиканская республика"},
        }
        accepted = aliases.get(target, {target})
        for record in records:
            if self._clean(str(record[1].get("n", ""))) in accepted:
                return record
        raise ScraperError(f"{self.name}: «{name}» не найдено в справочнике")

    def _price_list_matches(self, item: dict[str, Any], params: SearchParams) -> bool:
        try:
            departure = parse_api_date(item.get("date")).date()
        except ScraperError:
            return False
        if not params.date_from <= departure <= params.date_to:
            return False
        nights = {int(value) for value in re.findall(r"\d+", str(item.get("duration", "")))}
        return not nights or any(params.nights_min <= value <= params.nights_max for value in nights)

    def _price_url(self, url: str, params: SearchParams) -> str:
        parts = urlsplit(url.replace("http://", "https://", 1))
        query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"xml", "entr", "p"}]
        query.extend(
            [
                ("xml", 31),
                ("entr", int(self.settings.get("page_size", 100))),
                ("p", self._party_token(params)),
            ]
        )
        query.extend(("f7", nights) for nights in range(params.nights_min, params.nights_max + 1))
        query.extend(("f3", f"{stars}*") for stars in range(params.min_stars, params.max_stars + 1))
        if params.meal:
            query.append(("f8", params.meal))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _party_token(self, params: SearchParams) -> str:
        tokens = []
        for age in [30] * params.adults + params.children_ages:
            birthday = self._birthday(params.date_from, age)
            tokens.append(f"0{birthday:%d%m%Y}0")
        return ".".join(tokens)

    async def _parse_price_list(self, payload: dict[str, Any], params: SearchParams) -> list[Tour]:
        result = []
        for entry in payload.get("entries", []):
            hotel_details: dict[str, Any] = {}
            hotel_name = str(entry.get("hotel") or entry.get("hotelname") or "")
            if not hotel_name or entry.get("stars") is None:
                hotel_details = await self._hotel_details(str(entry.get("id_hotel", "")))
                hotel_name = hotel_name or str(hotel_details.get("name") or "")
            if not hotel_name:
                logger.warning("%s: пропущена запись без названия отеля", self.name)
                continue
            for price in entry.get("prices", []):
                if not self._matches_party(str(price.get("ag", "")), params) or self._stopped(price.get("isstop")):
                    continue
                amount = as_float(price.get("RUR"))
                currency = "RUB"
                if amount is None:
                    amount = as_float(price.get("amount"))
                    currency = str(payload.get("cur", "RUB"))
                if amount is None or amount <= 0:
                    continue
                url = f"{payload.get('href0', '')}{entry.get('href1', '')}{price.get('href2', '')}"
                url = url.replace("http://", "https://", 1)
                composition = entry.get("tourComposition", payload.get("tourComposition", []))
                types = {str(item.get("type")) for item in composition if isinstance(item, dict)}
                rating = as_float(entry.get("rating", hotel_details.get("raiting")))
                if rating is not None and 0 < rating <= 5:
                    rating *= 2
                result.append(
                    self.make_tour(
                        params,
                        amount,
                        currency,
                        url=url,
                        resort=str(entry.get("town") or ""),
                        hotel_name=hotel_name,
                        stars=parse_stars(entry.get("stars", hotel_details.get("stars", hotel_name))),
                        rating=rating,
                        meal=self._meal(str(entry.get("room") or "")),
                        departure_date=parse_api_date(entry.get("tour_date", entry.get("dt"))).date(),
                        nights=int(entry["duration"]),
                        room_type=str(entry.get("room") or ""),
                        includes_flight="flight" in types if types else True,
                        includes_transfer="transfer" in types if types else None,
                        includes_insurance="medins" in types if types else None,
                    )
                )
        return result

    async def _hotel_details(self, hotel_id: str) -> dict[str, Any]:
        if not hotel_id:
            return {}
        payload = await self._get_json("/yandex", params={"action": "hotelsJson", "id": hotel_id})
        if isinstance(payload, list):
            return payload[0] if payload and isinstance(payload[0], dict) else {}
        if isinstance(payload, dict):
            for key in ("hotel", "data", "result"):
                value = payload.get(key)
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value[0]
                if isinstance(value, dict):
                    return value
            return payload
        return {}

    @staticmethod
    def _matches_party(value: str, params: SearchParams) -> bool:
        if not value:
            return True
        ranges = []
        for token in value.replace("][", "],[").split(","):
            token = token.strip().strip("[]")
            match = re.fullmatch(r"(\d+)(?:-(\d+)?|\+)?", token)
            if not match:
                continue
            low = int(match.group(1))
            high = int(match.group(2)) if match.group(2) else (150 if "+" in token else low)
            ranges.append((low, high))
        if len(ranges) != params.traveler_count:
            return False
        adults, children = ranges[: params.adults], ranges[params.adults :]
        return all(high >= 18 for _, high in adults) and all(
            low <= age <= high for (low, high), age in zip(children, params.children_ages)
        )

    @staticmethod
    def _stopped(value: Any) -> bool:
        if isinstance(value, dict):
            return any(str(item).casefold() in {"1", "true", "yes"} for item in value.values())
        if isinstance(value, list):
            return any(str(item).casefold() in {"1", "true", "yes"} for item in value)
        return str(value).casefold() in {"1", "true", "yes"}

    @staticmethod
    def _meal(room: str) -> str | None:
        upper = room.upper()
        match = re.search(r"\b(UAI|AI|HB|BB|FB|RO)\b", upper)
        if match:
            return match.group(1)
        return normalize_meal(room)

    @staticmethod
    def _birthday(value: date, age: int) -> date:
        try:
            return value.replace(year=value.year - age)
        except ValueError:
            return value.replace(year=value.year - age, day=28)

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(value.casefold().replace("ё", "е").split())
