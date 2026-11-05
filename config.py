from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class NetworkConfig(BaseModel):
    user_agent: str = "TourParser/1.0 (+personal research; contact: local-user)"
    delay_min: float = Field(default=1.0, ge=0)
    delay_max: float = Field(default=3.0, ge=0)
    max_concurrency: int = Field(default=3, ge=1, le=10)
    retries: int = Field(default=3, ge=0, le=6)
    timeout: float = Field(default=45, gt=0)
    cache_ttl_seconds: int = Field(default=5400, ge=0)
    respect_robots_txt: bool = True

    @model_validator(mode="after")
    def validate_delays(self) -> NetworkConfig:
        if self.delay_max < self.delay_min:
            raise ValueError("delay_max must be >= delay_min")
        return self


class StorageConfig(BaseModel):
    database: Path = Path("data/tours.db")


class LoggingConfig(BaseModel):
    file: Path = Path("logs/parser.log")
    level: str = "INFO"


class ReportConfig(BaseModel):
    output_dir: Path = Path("reports")
    top: int = Field(default=20, ge=1, le=200)
    sort: Literal["value", "price"] = "value"


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "price": 0.38,
            "hotel": 0.18,
            "meal": 0.12,
            "services": 0.10,
            "flight": 0.10,
            "below_market": 0.12,
        }
    )
    fuzzy_threshold: float = Field(default=92, ge=80, le=100)

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringConfig:
        if not self.weights or any(weight < 0 for weight in self.weights.values()):
            raise ValueError("scoring weights must be non-negative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one scoring weight must be positive")
        return self


class CurrencyConfig(BaseModel):
    rub_rates: dict[str, float] = Field(
        default_factory=lambda: {
            "RUB": 1.0,
            "USD": 90.0,
            "EUR": 100.0,
            "AED": 24.5,
            "EGP": 1.9,
            "THB": 2.6,
            "TRY": 2.2,
        }
    )


class AppConfig(BaseModel):
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    currency: CurrencyConfig = Field(default_factory=CurrencyConfig)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    search: dict[str, Any] = Field(default_factory=dict)


def load_config(path: Path) -> AppConfig:
    """Load YAML and resolve local paths relative to the config file."""

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    base = path.resolve().parent
    for section, key in (
        ("storage", "database"),
        ("logging", "file"),
        ("report", "output_dir"),
    ):
        value = Path(raw.get(section, {}).get(key, getattr(AppConfig.model_fields[section].default_factory(), key)))
        raw.setdefault(section, {})[key] = str(value if value.is_absolute() else base / value)
    return AppConfig.model_validate(raw)
