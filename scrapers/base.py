from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable

from errors import ScraperError, SourceConfigurationError
from http_client import AsyncHttpClient
from models import SearchParams, Tour


class BaseScraper(ABC):
    """Contract for auto-discovered source adapters."""

    name = "base"
    enabled = True

    def __init__(self, client: AsyncHttpClient, settings: dict[str, Any]) -> None:
        self.client = client
        self.settings = settings

    @abstractmethod
    async def search(self, params: SearchParams) -> list[Tour]:
        """Return offers matching the supplied search parameters."""

    def require_credentials(self, *env_names: str) -> list[str]:
        values = [os.getenv(name, "").strip() for name in env_names]
        if not all(values):
            raise SourceConfigurationError(
                f"{self.name}: задайте переменные окружения {', '.join(env_names)}"
            )
        return values

    def to_rub(self, amount: float, currency: str) -> float:
        code = currency.upper().replace("₽", "RUB").strip()
        code = {"RUR": "RUB", "Р": "RUB", "РУБ": "RUB", "РУБЛЬ": "RUB"}.get(code, code)
        rates: dict[str, float] = {"RUB": 1.0, **self.settings.get("rub_rates", {})}
        rate = rates.get(code)
        if rate is None:
            raise ScraperError(f"{self.name}: не задан курс RUB для валюты {code}")
        return round(amount * rate, 2)

    def make_tour(self, params: SearchParams, price: float, currency: str, **values: Any) -> Tour:
        total = self.to_rub(price, currency)
        return Tour(
            source=self.name,
            departure_city=params.departure_city,
            country=params.country,
            price_total=total,
            price_per_person=round(total / params.traveler_count, 2),
            currency="RUB",
            **values,
        )


def payload_records(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Extract a record list from common official-API response envelopes."""

    current = payload
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return []
    if isinstance(current, list):
        return [item for item in current if isinstance(item, dict)]
    if isinstance(current, dict):
        for key in ("items", "data", "result", "tours", "results"):
            if key in current:
                return payload_records(current, key)
        if current and all(isinstance(value, dict) for value in current.values()):
            records = []
            for key, value in current.items():
                record = dict(value)
                record.setdefault("id", key)
                records.append(record)
            return records
    return []


def record_id(record: dict[str, Any]) -> Any:
    for key in ("id", "Id", "ID", "cityId", "countryId", "resortId", "classId", "rAndBId", "currencyId", "accommodationId"):
        if key in record:
            return record[key]
    return None


def record_name(record: dict[str, Any]) -> str:
    for key in ("name", "Name", "title", "Title", "value", "text"):
        if record.get(key) is not None:
            return str(record[key])
    return ""


def find_named_record(records: Iterable[dict[str, Any]], name: str, source: str) -> dict[str, Any]:
    target = name.strip().casefold().replace("ё", "е")
    for record in records:
        candidate = record_name(record).strip().casefold().replace("ё", "е")
        if candidate == target:
            return record
    available = ", ".join(sorted(filter(None, (record_name(item) for item in records)))[:15])
    raise ScraperError(f"{source}: «{name}» не найдено в справочнике. Доступно: {available}")


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        value = re.sub(r"[^0-9,.-]", "", value).replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int | None = None) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else default


def parse_stars(value: Any) -> int | None:
    if isinstance(value, (int, float)) and 1 <= value <= 5:
        return int(value)
    match = re.search(r"([1-5])(?:\s*(?:\*|звез|star))", str(value or ""), re.IGNORECASE)
    if match:
        return int(match.group(1))
    number = as_int(value)
    return number if number is not None and 1 <= number <= 5 else None


def parse_api_date(value: Any) -> datetime:
    text = str(value)
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text[:19], pattern)
        except ValueError:
            pass
    raise ScraperError(f"Неизвестный формат даты: {text!r}")
