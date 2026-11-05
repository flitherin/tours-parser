from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Iterable

from rapidfuzz import fuzz

from models import SearchParams, Tour


def _clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", value).strip()


def normalize_hotel_name(value: str) -> str:
    """Create a stable comparison form without stars, punctuation and generic hotel words."""

    value = _clean_text(value)
    value = re.sub(r"(?:^|\s)[1-5]\s*(?:\*|звез\w*|stars?)(?=\s|$)", " ", value)
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    words = [word for word in value.split() if word not in {"hotel", "отель", "гостиница"}]
    return " ".join(words)


def normalize_resort(value: str) -> str:
    value = _clean_text(value)
    return re.sub(r"[^0-9a-zа-я]+", " ", value).strip()


def normalize_meal(value: str | None) -> str | None:
    if not value:
        return None
    text = _clean_text(value)
    text = re.sub(r"[^a-zа-я]+", " ", text).strip()
    if not text:
        return None
    if text in {"uai", "ultra all inclusive", "ультра все включено"} or "ultra all" in text or "ультра" in text:
        return "UAI"
    if text in {"ai", "all inclusive", "все включено"} or "all inclusive" in text:
        return "AI"
    if text in {"fb", "full board", "полный пансион", "трехразовое питание"} or (
        "завтрак" in text and "обед" in text and "ужин" in text
    ):
        return "FB"
    if text in {"hb", "half board", "полупансион", "завтрак и ужин", "завтрак ужин"} or (
        "завтрак" in text and "ужин" in text and "обед" not in text
    ):
        return "HB"
    if text in {"bb", "bed and breakfast", "завтрак", "только завтрак", "только завтраки"}:
        return "BB"
    if text in {"ro", "room only", "без питания", "не включено"} or "без питания" in text:
        return "RO"
    aliases = {
        "ai": "AI",
        "all": "AI",
        "все включено": "AI",
        "завтраки": "BB",
        "полупансион": "HB",
        "полный пансион": "FB",
        "без питания": "RO",
    }
    return aliases.get(text, text.upper())


def normalize_tour(tour: Tour) -> Tour:
    tour.hotel_name = re.sub(r"\s+", " ", tour.hotel_name).strip()
    tour.hotel_name = re.sub(
        r"\s+[1-5]\s*(?:\*|звез\w*|stars?)(?=\s|$)", "", tour.hotel_name, flags=re.IGNORECASE
    ).strip()
    tour.normalized_hotel = normalize_hotel_name(tour.hotel_name)
    tour.meal = normalize_meal(tour.meal)
    tour.resort = re.sub(r"\s+", " ", tour.resort).strip()
    tour.available_on = sorted(set(tour.available_on or [tour.source]))
    return tour


def filter_tours(tours: Iterable[Tour], params: SearchParams) -> list[Tour]:
    """Apply all user filters locally, including filters unsupported by an API."""

    result = []
    for raw_tour in tours:
        tour = normalize_tour(raw_tour)
        if not params.date_from <= tour.departure_date <= params.date_to:
            continue
        if not params.nights_min <= tour.nights <= params.nights_max:
            continue
        if tour.stars is None or not params.min_stars <= tour.stars <= params.max_stars:
            continue
        if params.meal and tour.meal != params.meal:
            continue
        if params.max_budget and tour.price_total > params.max_budget:
            continue
        if params.min_rating is not None and (tour.rating is None or tour.rating < params.min_rating):
            continue
        if not tour.normalized_hotel:
            continue
        result.append(tour)
    return result


def deduplicate_tours(tours: Iterable[Tour], fuzzy_threshold: float = 92) -> list[Tour]:
    """Merge likely duplicates and retain the cheapest offer plus all source names."""

    items = [normalize_tour(tour) for tour in tours]
    parents = list(range(len(items)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parents[right_root] = left_root

    blocks: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for index, tour in enumerate(items):
        blocks[(tour.departure_date, tour.nights, tour.meal)].append(index)
    for indices in blocks.values():
        for position, left_index in enumerate(indices):
            left = items[left_index]
            for right_index in indices[position + 1 :]:
                right = items[right_index]
                if _same_offer(left, right, fuzzy_threshold):
                    union(left_index, right_index)

    groups: dict[int, list[Tour]] = defaultdict(list)
    for index, tour in enumerate(items):
        groups[root(index)].append(tour)
    result = []
    for group in groups.values():
        best = min(group, key=lambda tour: (tour.price_total, tour.source, tour.url))
        best.available_on = sorted({source for tour in group for source in tour.available_on})
        stable_name = min(tour.normalized_hotel for tour in group)
        best.normalized_hotel = stable_name
        best.dedup_key = _dedup_key(best, stable_name)
        result.append(best)
    return result


def _same_offer(left: Tour, right: Tour, threshold: float) -> bool:
    if left.stars and right.stars and left.stars != right.stars:
        return False
    left_resort, right_resort = normalize_resort(left.resort), normalize_resort(right.resort)
    if left_resort and right_resort and fuzz.token_sort_ratio(left_resort, right_resort) < 80:
        return False
    left_name, right_name = left.normalized_hotel, right.normalized_hotel
    if not left_name or not right_name:
        return False
    length_ratio = min(len(left_name), len(right_name)) / max(len(left_name), len(right_name))
    if length_ratio < 0.75:
        return False
    return (
        fuzz.token_set_ratio(left_name, right_name) >= threshold
        and fuzz.token_sort_ratio(left_name, right_name) >= threshold - 5
    )


def _dedup_key(tour: Tour, stable_name: str) -> str:
    material = "|".join(
        (
            stable_name,
            tour.departure_date.isoformat(),
            str(tour.nights),
            tour.meal or "",
            _clean_text(tour.departure_city),
            _clean_text(tour.country),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
