from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from config import NetworkConfig, load_config
from errors import RobotsDeniedError, ScraperError, SourceBlockedError, SourceConfigurationError
from http_client import AsyncHttpClient
from main import _run_source
from scrapers import discover_scrapers
from scrapers._samo import SamoOptions, SamoScraper
from scrapers.ambotis import AmbotisScraper
from scrapers.paks import PaksScraper
from scrapers.russian_express import RussianExpressScraper
from scrapers.space_travel import SpaceTravelScraper
from storage import Storage

SOURCES = [PaksScraper, AmbotisScraper, RussianExpressScraper, SpaceTravelScraper]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contract() -> dict[str, Any]:
    return json.loads((ROOT / "tests/fixtures/samo.json").read_text(encoding="utf-8"))


class SamoTransport:
    def __init__(self, contract: dict[str, Any]) -> None:
        self.contract = contract
        self.requests: list[httpx.Request] = []
        self.pages: list[dict[str, list[str]]] = []
        self.robots = "User-agent: *\nAllow: /"
        self.api_status = 200
        self.price_error: int | None = None
        self.repeat_page = False
        self.grouped = False
        self.malformed = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={"error": "apiKey not provided"})
        assert request.method == "POST"
        fields = parse_qs(request.content.decode(), keep_blank_values=True)
        assert fields["oauth_token"] == ["synthetic-secret"]
        action = request.url.params["action"]
        if action == "SearchTour_PRICES":
            self.pages.append(fields)
            if self.malformed:
                return httpx.Response(200, json={action: {"unexpected": []}})
            if self.price_error:
                return httpx.Response(200, json={action: {"error": self.price_error, "message": "upstream details"}})
            if fields["PRICEPAGE"] != ["1"] and not self.repeat_page:
                return httpx.Response(200, json={action: {"error": 3, "message": "No more results"}})
            result = copy.deepcopy(self.contract[action])
            if self.grouped and "CATCLAIM" not in fields:
                result["prices"][0]["id"] = "group1"
                result["prices"][0]["grouped"] = "1"
            return httpx.Response(200, json={action: result})
        return httpx.Response(200, json={action: self.contract[action]})


def network() -> NetworkConfig:
    return NetworkConfig(delay_min=0, delay_max=0, retries=0, cache_ttl_seconds=60)


def narrow(params):
    return params.model_copy(update={"date_to": date(2027, 6, 7)})


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_search_contract_all_operators(source, params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(source.token_env, "synthetic-secret")
    transport = SamoTransport(contract)
    storage = Storage(tmp_path / "cache.db")
    async with AsyncHttpClient(network(), storage, httpx.MockTransport(transport)) as client:
        scraper = source(client, {})
        tours = await scraper.search(narrow(params))
        requests_before = len(transport.requests)
        assert len(await scraper.search(narrow(params))) == 1
        assert len(transport.requests) == requests_before
    assert len(tours) == 1
    tour = tours[0]
    assert tour.source == source.name
    assert tour.price_total == 140000
    assert tour.price_per_person == 70000
    assert tour.stars == 4
    assert tour.departure_date == date(2027, 6, 5)
    assert tour.includes_flight is True
    assert tour.includes_transfer is None
    assert tour.rating is None
    assert tour.departure_time is None
    assert tour.direct_flight is None
    parsed = urlsplit(tour.url)
    assert parsed.netloc == urlsplit(source.base_url).netloc
    assert parsed.path == "/bron"
    assert parse_qs(parsed.query)["CATCLAIM"] == ["0xTEST1"]
    assert "synthetic-secret" not in tour.model_dump_json()
    assert all("synthetic-secret" not in str(request.url) for request in transport.requests)
    assert transport.pages[0]["CURRENCY"] == ["5"]
    assert transport.pages[0]["FREIGHT"] == ["1"]
    assert transport.pages[0]["FILTER"] == ["1"]
    assert transport.pages[0]["DYN_SEPARATE"] == ["0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_missing_key_makes_no_network_request(source, params, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(source.token_env, raising=False)

    def fail(request):
        pytest.fail("Network called without credentials")

    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(fail)) as client:
        with pytest.raises(SourceConfigurationError, match=source.token_env):
            await source(client, {}).search(params)


@pytest.mark.asyncio
async def test_children_group_expansion_and_no_secret_in_cache(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    params = narrow(params).model_copy(update={"children_ages": [1, 9], "meal": "AI"})
    contract["SearchTour_PRICES"]["prices"][0].update(child="2", infant="1")
    transport = SamoTransport(contract)
    transport.grouped = True
    storage = Storage(tmp_path / "cache.db")
    async with AsyncHttpClient(network(), storage, httpx.MockTransport(transport)) as client:
        tours = await PaksScraper(client, {}).search(params)
    assert tours[0].price_per_person == 35000
    assert all(page["AGES"] == ["1,9"] for page in transport.pages)
    expanded = [page for page in transport.pages if "CATCLAIM" in page]
    assert expanded and expanded[0]["HOTELS"] == ["16531"]
    assert all("PARTITION_PRICE" not in page for page in expanded)
    with storage._connect() as db:
        cached = db.execute("SELECT cache_key, body FROM response_cache").fetchall()
    assert all("synthetic-secret" not in str(tuple(row)) for row in cached)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"packetType": "2"}, {"packetType": "1"}, {"packetType": None},
    {"transportCodeKey": "2"}, {"transportCodeKey": "3"}, {"transportCodeKey": None},
    {"bron": "0"}, {"crossTour": "1"}, {"hotelCount": "2"}, {"stopSale": "1"},
    {"townFromKey": "16"}, {"stateKey": "66"}, {"adult": "3"}, {"child": "1"},
    {"checkIn": "20270705"}, {"nights": "20"}, {"meal": "RO"}, {"star": "**"},
])
async def test_rejects_incomparable_or_nonmatching_offers(patch, params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    contract["SearchTour_PRICES"]["prices"][0].update(patch)
    params = narrow(params).model_copy(update={"meal": "AI"})
    transport = SamoTransport(contract)
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        assert await PaksScraper(client, {}).search(params) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_access_failure_isolated_by_source_runner(status, params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.api_status = status
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        tours, error = await _run_source(PaksScraper(client, {}), params)
    assert tours == []
    assert error and error.source == "paks"
    assert "synthetic-secret" not in error.error
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_robots_blocks_api(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.robots = "User-agent: *\nDisallow: /export/"
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        with pytest.raises(RobotsDeniedError):
            await PaksScraper(client, {}).search(params)
    assert len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [2, 3, 2101, 2105, 2110])
async def test_documented_no_results_codes(code, params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.price_error = code
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        assert await PaksScraper(client, {}).search(narrow(params)) == []


@pytest.mark.asyncio
async def test_errors_do_not_become_empty_results(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.price_error = 101
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        with pytest.raises(ScraperError, match="101"):
            await PaksScraper(client, {}).search(narrow(params))


@pytest.mark.asyncio
async def test_schema_change_is_reported(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.malformed = True
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        with pytest.raises(ScraperError, match="prices"):
            await PaksScraper(client, {}).search(narrow(params))


@pytest.mark.asyncio
async def test_repeating_pages_cannot_loop(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    transport.repeat_page = True
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        with pytest.raises(ScraperError, match="повторяет"):
            await PaksScraper(client, {}).search(narrow(params))
    assert len(transport.pages) == 2


@pytest.mark.asyncio
async def test_budget_filter_and_request_limit(params, contract, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAKS_API_TOKEN", "synthetic-secret")
    transport = SamoTransport(contract)
    async with AsyncHttpClient(network(), Storage(tmp_path / "c.db"), httpx.MockTransport(transport)) as client:
        assert await PaksScraper(client, {}).search(narrow(params).model_copy(update={"max_budget": 100000})) == []
        assert transport.pages[0]["COSTMAX"] == ["100000"]
        with pytest.raises(ScraperError, match="лимит запросов"):
            await PaksScraper(client, {"max_requests": 4}).search(narrow(params))


def test_reference_aliases_dates_stars_and_currency() -> None:
    scraper = PaksScraper(None, {})
    scraper._options = SamoOptions()
    assert scraper._date_chunks(date(2027, 6, 1), date(2027, 6, 15)) == [
        (date(2027, 6, 1), date(2027, 6, 7)),
        (date(2027, 6, 8), date(2027, 6, 14)),
        (date(2027, 6, 15), date(2027, 6, 15)),
    ]
    assert scraper._find_place([{"id": 66, "name": "Объединенные Арабские Эмираты"}], "ОАЭ")["id"] == 66
    assert scraper._stars("*****") == 5
    assert scraper._date(20270605) == date(2027, 6, 5)
    assert scraper._rub_price({"convertedPrice": "123 456,70 RUB", "convertedCurrencyKey": 5}, 5) == 123456.7
    assert scraper._rub_price({"price": 50000, "currency": "RUR"}, 5) == 50000
    with pytest.raises(ValueError):
        scraper._rub_price({"price": 100, "currency": "USD"}, 5)
    with pytest.raises(ValueError):
        scraper._rub_price({"convertedPrice": "100 USD", "convertedCurrencyKey": 5}, 5)


def test_new_sources_present_in_config_and_can_be_disabled() -> None:
    config = load_config(ROOT / "config.yaml")
    for source in SOURCES:
        assert config.sources[source.name]["enabled"]
        assert source in [type(scraper) for scraper in discover_scrapers(None, config.sources)]
    settings = {source.name: {"enabled": False} for source in SOURCES}
    assert len(discover_scrapers(None, settings)) == 7


def test_cli_reports_all_four_missing_keys(tmp_path) -> None:
    import os
    import subprocess
    import sys

    import yaml

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["storage"]["database"] = str(tmp_path / "tours.db")
    config["logging"]["file"] = str(tmp_path / "parser.log")
    config["report"]["output_dir"] = str(tmp_path / "reports")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    for source in SOURCES:
        environment.pop(source.token_env, None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--config", str(path),
         "--sources", ",".join(source.name for source in SOURCES)],
        env=environment, capture_output=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    files = list((tmp_path / "reports").iterdir())
    assert {file.suffix for file in files} == {".html", ".csv", ".xlsx"}
    report = next(file for file in files if file.suffix == ".html").read_text(encoding="utf-8")
    for source in SOURCES:
        assert source.name in report
        assert source.token_env in report
