from __future__ import annotations

from typing import Any

import pytest

from scrapers import discover_scrapers
from scrapers.biblio_globus import BiblioGlobusScraper
from scrapers.level_travel import LevelTravelFeedScraper
from scrapers.tourtrans import TourtransScraper
from scrapers.tourvisor import TourvisorScraper


class FakeClient:
    def __init__(self) -> None:
        self.form_data: dict[str, Any] | None = None

    async def request_text(self, method: str, url: str, **kwargs: Any) -> str:
        self.form_data = dict(kwargs.get("form_data") or {})
        return ""

    async def get_text(self, url: str, **kwargs: Any) -> str:
        if "homepage" in url:
            return 'var BG_FLTX = {"flt":[[1,{"n":"Турция"}]],"flt2":[[2,{"n":"Москва","t":[3]}]]};'
        if "level" in url:
            return """<yml><offers><offer id="123"><hotel>Sunrise Resort Hotel</hotel><country>Турция</country><resort>Анталья</resort><stars>4*</stars><rating>4.4</rating><price>140000</price><departure_date>2027-06-05</departure_date><nights>7</nights><meal>AI</meal><room>Standard</room></offer></offers></yml>"""
        if "tours.xml" in url:
            return """<Tours><Tour url="https://www.tourtrans.ru/tours/turkey/1.html"><Title>Тур</Title><Duration>7 ночей / 8 дней</Duration><Currency>EUR</Currency><Route>Анталья</Route><Countries><Country>Турция</Country></Countries><Include>В стоимость включено: трансфер, медицинская страховка</Include><Additional><Service><ServiceName>Авиаперелет Москва – Анталья – Москва</ServiceName><ServicePrice>500</ServicePrice></Service></Additional><TourDays><TourDay><Title>Москва - Анталья</Title><Text>Вылет из Москвы</Text></TourDay></TourDays><TourDates><TourDate><Date>2027-06-05</Date><PlacesLeft>20</PlacesLeft><Hotels><Hotel><HotelName>Sunrise Resort Hotel</HotelName><HotelCategory>4*</HotelCategory><AccomodationTypes><AccomodationType><Room>Double</Room><Category>Standard</Category><Nights>7</Nights><Board>AI</Board><Price>2000</Price></AccomodationType></AccomodationTypes></Hotel></Hotels><MinPrice>1000</MinPrice></TourDate></TourDates></Tour></Tours>"""
        raise AssertionError(f"Unexpected text URL: {url}")

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/list.php"):
            return {
                "data": {
                    "departure": [{"id": 2, "name": "Москва"}],
                    "country": [{"id": 1, "name": "Турция"}],
                }
            }
        if url.endswith("/search.php"):
            return {"result": {"requestid": "req1"}}
        if url.endswith("/result.php"):
            params = kwargs.get("params", {})
            if params.get("type") == "status":
                return {"data": {"status": {"state": "finished"}}}
            return {
                "data": {
                    "result": {
                        "hotel": [
                            {
                                "hotelname": "Sunrise Resort Hotel",
                                "hotelstars": "4*",
                                "hotelrating": 4.5,
                                "regionname": "Анталья",
                                "tours": {
                                    "tour": [
                                        {
                                            "flydate": "05.06.2027",
                                            "nights": 7,
                                            "price": 140000,
                                            "currency": "RUB",
                                            "meal": "AI",
                                            "room": "Standard",
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        if url.endswith("/yandex"):
            return {
                "entries": [
                    {
                        "date": "05.06.2027",
                        "duration": "/7/8/",
                        "url": "https://export.bgoperator.ru/partners?action=price&id_price=1&data=05.06.2027&xml=11",
                        "id_price": "1",
                    }
                ]
            }
        if "partners" in url:
            return {
                "href0": "https://export.bgoperator.ru/tozaya?prx=1",
                "cur": "EUR",
                "entries": [
                    {
                        "tour_date": "05.06.2027",
                        "town": "Анталья",
                        "hotel": "Sunrise Resort Hotel",
                        "stars": "4*",
                        "room": "STANDARD AI",
                        "href1": "&templ=1",
                        "id_hotel": "1",
                        "duration": "7",
                        "prices": [
                            {
                                "amount": "1400",
                                "RUR": "140000",
                                "href2": "&p=1",
                                "ag": "12+,12+",
                            }
                        ],
                        "tourComposition": [{"type": "flight"}, {"type": "transfer"}, {"type": "medins"}],
                    }
                ],
            }
        raise AssertionError(f"Unexpected JSON URL: {url}")


@pytest.mark.asyncio
async def test_tourvisor_search(params, monkeypatch) -> None:
    monkeypatch.setenv("TOURVISOR_API_LOGIN", "user")
    monkeypatch.setenv("TOURVISOR_API_PASSWORD", "password")
    settings = {"initial_poll_delay": 0, "poll_interval": 0, "max_pages": 1}
    tours = await TourvisorScraper(FakeClient(), settings).search(params)
    assert len(tours) == 1
    assert tours[0].rating == 9
    assert tours[0].price_total == 140000
    assert "requestid=req1" in tours[0].url


@pytest.mark.asyncio
async def test_level_feed_search(params, monkeypatch) -> None:
    monkeypatch.setenv("LEVEL_TRAVEL_FEED_URL", "https://feed.test/level.xml")
    tours = await LevelTravelFeedScraper(FakeClient(), {}).search(params)
    assert len(tours) == 1
    assert tours[0].rating == 8.8
    assert tours[0].url == "https://level.travel/packages/id_123/"
    assert tours[0].includes_transfer is True


@pytest.mark.asyncio
async def test_tourtrans_search(params) -> None:
    settings = {"rub_rates": {"EUR": 100.0}}
    tours = await TourtransScraper(FakeClient(), settings).search(params)
    assert len(tours) == 1
    assert tours[0].price_total == 300000
    assert tours[0].price_per_person == 150000
    assert tours[0].includes_flight is True
    assert tours[0].includes_insurance is True


@pytest.mark.asyncio
async def test_biblio_globus_search(params, monkeypatch) -> None:
    monkeypatch.setenv("BIBLIO_GLOBUS_LOGIN", "user")
    monkeypatch.setenv("BIBLIO_GLOBUS_PASSWORD", "password")
    client = FakeClient()
    tours = await BiblioGlobusScraper(client, {}).search(params)
    assert client.form_data == {"login": "user", "pwd": "password"}
    assert len(tours) == 1
    assert tours[0].price_total == 140000
    assert tours[0].meal == "AI"
    assert tours[0].includes_flight is True
    assert tours[0].includes_transfer is True
    assert tours[0].url.endswith("&p=1")


def test_discovers_eleven_sources() -> None:
    names = {
        "travelata": {},
        "sletat": {},
        "tez_tour": {},
        "tourvisor": {},
        "biblio_globus": {},
        "level_travel": {},
        "tourtrans": {},
        "paks": {},
        "ambotis": {},
        "russian_express": {},
        "space_travel": {},
    }
    scrapers = discover_scrapers(FakeClient(), names)
    assert {scraper.name for scraper in scrapers} == set(names)
