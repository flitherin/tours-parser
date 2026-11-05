from __future__ import annotations

import logging
import re
from xml.etree import ElementTree

from errors import ScraperError
from models import SearchParams, Tour
from scrapers.base import BaseScraper, as_float, parse_api_date, parse_stars

logger = logging.getLogger(__name__)

FEED_URL = "https://www.tourtrans.ru/tours.xml"


class TourtransScraper(BaseScraper):
    """Daily official catalog; flight supplement is included in the calculated total."""

    name = "tourtrans"

    async def search(self, params: SearchParams) -> list[Tour]:
        if params.children_ages:
            logger.info("%s: детские цены не рассчитываются из ежедневного фида", self.name)
            return []
        body = await self.client.get_text(
            str(self.settings.get("feed_url", FEED_URL)),
            check_robots=bool(self.settings.get("check_robots_txt", True)),
        )
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ScraperError(f"{self.name}: некорректный XML feed") from exc
        tours: list[Tour] = []
        for item in root.findall(".//Tour"):
            for tour in self._parse_tour(item, params):
                tours.append(tour)
                if len(tours) >= int(self.settings.get("max_offers", 500)):
                    return tours
        return tours

    def _parse_tour(self, item: ElementTree.Element, params: SearchParams) -> list[Tour]:
        countries = [self._clean(country.text or "") for country in item.findall("./Countries/Country")]
        if self._clean(params.country) not in countries:
            return []
        flight = self._flight_service(item, params.departure_city)
        if flight is None:
            return []
        service_name, flight_price = flight
        include = item.findtext("Include", "")
        currency = item.findtext("Currency", "RUB")
        result = []
        for date_item in item.findall("./TourDates/TourDate"):
            departure_date = parse_api_date(date_item.findtext("Date", "")).date()
            if not params.date_from <= departure_date <= params.date_to:
                continue
            if (as_float(date_item.findtext("PlacesLeft"), 1) or 0) <= 0:
                continue
            base_price = as_float(date_item.findtext("MinPrice"))
            if base_price is None:
                continue
            for hotel in date_item.findall("./Hotels/Hotel"):
                accommodation = self._accommodation(hotel, params.adults)
                if accommodation is None:
                    continue
                nights = int(as_float(accommodation.findtext("Nights")) or self._duration_nights(item))
                if not nights:
                    continue
                stars = parse_stars(hotel.findtext("HotelCategory", ""))
                total_per_person = base_price + flight_price
                result.append(
                    self.make_tour(
                        params,
                        total_per_person * params.traveler_count,
                        currency,
                        url=item.attrib["url"],
                        resort=self._resort(item.findtext("Route", "")),
                        hotel_name=hotel.findtext("HotelName", item.findtext("Title", "")),
                        stars=stars,
                        rating=None,
                        meal=self._meal(accommodation.findtext("Board", ""), include),
                        departure_date=departure_date,
                        nights=nights,
                        room_type=" ".join(
                            filter(None, [accommodation.findtext("Room", ""), accommodation.findtext("Category", "")])
                        ),
                        includes_flight=True,
                        includes_transfer="трансфер" in include.casefold(),
                        includes_insurance="медицинская страховка" in include.casefold(),
                        direct_flight=False if "пересад" in service_name.casefold() else None,
                    )
                )
        return result

    def _flight_service(self, item: ElementTree.Element, city: str) -> tuple[str, float] | None:
        first_day = item.find("./TourDays/TourDay")
        first_text = self._clean(f"{first_day.findtext('Title', '') if first_day is not None else ''} {first_day.findtext('Text', '') if first_day is not None else ''}")
        if self._clean(city) not in first_text:
            return None
        for service in item.findall("./Additional/Service"):
            name = service.findtext("ServiceName", "")
            normalized = self._clean(name)
            if "авиаперелет" in normalized and self._clean(city) in normalized:
                price = as_float(service.findtext("ServicePrice"), 0)
                return name, price or 0
        return None

    @staticmethod
    def _accommodation(hotel: ElementTree.Element, adults: int) -> ElementTree.Element | None:
        options = hotel.findall("./AccomodationTypes/AccomodationType")
        preferred = {1: "single", 2: "double", 3: "triple"}.get(adults, "double")
        return next((item for item in options if item.findtext("Room", "").casefold() == preferred), options[0] if options else None)

    @staticmethod
    def _duration_nights(item: ElementTree.Element) -> int:
        match = re.search(r"(\d+)\s*ноч", item.findtext("Duration", ""), re.IGNORECASE)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _resort(route: str) -> str:
        return re.split(r"\s+[–-]\s+", route, maxsplit=1)[0].strip()

    @staticmethod
    def _meal(board: str, include: str) -> str | None:
        if board and board.casefold() not in {"по программе", "нет"}:
            return board
        text = include.casefold()
        if "завтрак" in text and ("ужин" in text or "обед" in text):
            return "HB"
        if "завтрак" in text:
            return "BB"
        return None

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(value.casefold().replace("ё", "е").split())
