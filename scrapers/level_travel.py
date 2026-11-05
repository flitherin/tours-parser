from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree

from errors import ScraperError
from models import SearchParams, Tour
from scrapers.base import BaseScraper, as_float, parse_api_date, parse_stars

logger = logging.getLogger(__name__)

# The partner supplies a city-specific feed URL. Tag aliases cover its YML variants.
FIELD_ALIASES = {
    "hotel": ("hotel", "hotel_name", "hotelname", "name"),
    "country": ("country", "country_name", "countryname"),
    "resort": ("resort", "region", "region_name", "town", "city"),
    "stars": ("stars", "hotel_stars", "hotelstars", "category"),
    "rating": ("rating", "hotel_rating", "hotelrating"),
    "price": ("price", "price_rub", "price_rur", "cost", "value"),
    "date": ("departure_date", "departure", "flydate", "date", "start_date"),
    "nights": ("nights", "night_count", "duration"),
    "meal": ("meal", "meal_name", "mealtype", "board"),
    "room": ("room", "room_type", "roomtype"),
    "url": ("url", "link", "tour_url"),
    "tour_id": ("tour_id", "tourid", "id"),
}


class LevelTravelFeedScraper(BaseScraper):
    name = "level_travel"

    async def search(self, params: SearchParams) -> list[Tour]:
        if params.traveler_count != 2 or params.nights_min < 7 or params.nights_max > 14:
            logger.info("%s: фид поддерживает только двух туристов и 7–14 ночей", self.name)
            return []
        feed_url = self.require_credentials("LEVEL_TRAVEL_FEED_URL")[0]
        body = await self.client.get_text(
            feed_url,
            check_robots=bool(self.settings.get("check_robots_txt", False)),
        )
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ScraperError(f"{self.name}: некорректный YML feed") from exc
        tours: list[Tour] = []
        for offer in root.iter():
            if self._tag(offer.tag) not in {"offer", "tour"}:
                continue
            try:
                tours.append(self._parse_offer(offer, params))
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("%s: пропущена запись: %s", self.name, exc)
        return tours

    def _parse_offer(self, offer: ElementTree.Element, params: SearchParams) -> Tour:
        price = as_float(self._value(offer, "price"))
        if price is None:
            raise ValueError("price is missing")
        rating = as_float(self._value(offer, "rating"))
        if rating is not None and 0 < rating <= 5:
            rating *= 2
        tour_id = self._value(offer, "tour_id") or offer.attrib.get("id", "")
        url = self._value(offer, "url") or f"https://level.travel/packages/id_{tour_id}/"
        return self.make_tour(
            params,
            price,
            "RUB",
            url=url,
            resort=self._value(offer, "resort"),
            hotel_name=self._value(offer, "hotel"),
            stars=parse_stars(self._value(offer, "stars")),
            rating=rating,
            meal=self._value(offer, "meal") or None,
            departure_date=parse_api_date(self._value(offer, "date")).date(),
            nights=int(as_float(self._value(offer, "nights")) or 0),
            room_type=self._value(offer, "room"),
            includes_flight=True,
            includes_transfer=True,
            includes_insurance=None,
        )

    def _value(self, offer: ElementTree.Element, field: str) -> str:
        custom = self.settings.get("field_map", {}).get(field)
        aliases = (custom,) if custom else FIELD_ALIASES[field]
        values: dict[str, str] = {}
        for key, value in offer.attrib.items():
            values[self._tag(key)] = value.strip()
        for child in offer:
            text = " ".join("".join(child.itertext()).split())
            values[self._tag(child.tag)] = text
        for alias in aliases:
            key = self._tag(alias)
            if values.get(key):
                return values[key]
        return ""

    @staticmethod
    def _tag(value: str) -> str:
        value = value.rsplit("}", 1)[-1]
        return re.sub(r"[^a-z0-9]+", "", value.casefold())
