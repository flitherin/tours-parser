from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchParams(BaseModel):
    """Validated tour search parameters shared by all adapters."""

    model_config = ConfigDict(validate_assignment=True)

    departure_city: str
    country: str
    date_from: date
    date_to: date
    nights_min: int = Field(ge=1, le=30)
    nights_max: int = Field(ge=1, le=30)
    adults: int = Field(default=2, ge=1, le=6)
    children_ages: list[int] = Field(default_factory=list, max_length=4)
    min_stars: int = Field(default=3, ge=1, le=5)
    max_stars: int = Field(default=5, ge=1, le=5)
    meal: Literal["RO", "BB", "HB", "FB", "AI", "UAI"] | None = None
    max_budget: float | None = Field(default=None, gt=0)
    min_rating: float | None = Field(default=None, ge=0, le=10)

    @field_validator("departure_city", "country")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("children_ages")
    @classmethod
    def validate_children(cls, value: list[int]) -> list[int]:
        if any(age < 0 or age > 17 for age in value):
            raise ValueError("children ages must be between 0 and 17")
        return value

    @model_validator(mode="after")
    def validate_ranges(self) -> SearchParams:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if self.nights_max < self.nights_min:
            raise ValueError("nights_max must be >= nights_min")
        if self.max_stars < self.min_stars:
            raise ValueError("max_stars must be >= min_stars")
        return self

    @property
    def traveler_count(self) -> int:
        return self.adults + len(self.children_ages)


class Tour(BaseModel):
    """Normalized package-tour offer; all monetary values are in RUB."""

    model_config = ConfigDict(validate_assignment=True)

    source: str
    url: str
    departure_city: str
    country: str
    resort: str = ""
    hotel_name: str
    stars: int | None = Field(default=None, ge=1, le=5)
    rating: float | None = Field(default=None, ge=0, le=10)
    meal: str | None = None
    departure_date: date
    nights: int = Field(ge=1)
    room_type: str = ""
    price_total: float = Field(gt=0)
    price_per_person: float = Field(gt=0)
    currency: str = "RUB"
    includes_flight: bool | None = None
    includes_transfer: bool | None = None
    includes_insurance: bool | None = None
    luggage: str | None = None
    direct_flight: bool | None = None
    departure_time: time | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    normalized_hotel: str = ""
    dedup_key: str = ""
    available_on: list[str] = Field(default_factory=list)
    score: float = Field(default=0, ge=0, le=100)
    score_reasons: list[str] = Field(default_factory=list)
    previous_price: float | None = Field(default=None, gt=0)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("offer URL must be absolute HTTP(S)")
        return value

    @field_validator("meal")
    @classmethod
    def normalize_meal_case(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def fill_sources(self) -> Tour:
        if not self.available_on:
            self.available_on = [self.source]
        return self

    @property
    def price_per_person_per_night(self) -> float:
        return self.price_per_person / self.nights

    @property
    def price_dropped(self) -> bool:
        return self.previous_price is not None and self.price_total < self.previous_price * 0.999

    @property
    def price_drop_percent(self) -> float | None:
        if not self.price_dropped or self.previous_price is None:
            return None
        return (1 - self.price_total / self.previous_price) * 100


class SourceFailure(BaseModel):
    source: str
    error: str
