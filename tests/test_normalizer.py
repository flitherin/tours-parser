from datetime import date

from normalizer import deduplicate_tours, filter_tours, normalize_hotel_name, normalize_meal


def test_normalizes_hotel_and_meal() -> None:
    assert normalize_hotel_name("Hotel Sunrise Resort 4*") == "sunrise resort"
    assert normalize_meal("Ultra All Inclusive") == "UAI"
    assert normalize_meal("Завтрак и ужин") == "HB"
    assert normalize_meal("Без питания") == "RO"


def test_deduplicates_fuzzy_hotel_and_keeps_cheapest(make_tour) -> None:
    expensive = make_tour(price_total=150000, price_per_person=75000)
    cheap = make_tour(
        source="source_b",
        url="https://example.org/offer/2",
        hotel_name="Hotel Sunrise Resort 4*",
        price_total=135000,
        price_per_person=67500,
    )
    result = deduplicate_tours([expensive, cheap])
    assert len(result) == 1
    assert result[0].source == "source_b"
    assert result[0].price_total == 135000
    assert result[0].available_on == ["source_a", "source_b"]
    assert result[0].dedup_key


def test_does_not_merge_different_dates(make_tour) -> None:
    first = make_tour()
    second = make_tour(source="source_b", departure_date=date(2027, 6, 6))
    assert len(deduplicate_tours([first, second])) == 2


def test_applies_all_filters(make_tour, params) -> None:
    matching = make_tour()
    wrong_meal = make_tour(meal="RO")
    too_expensive = make_tour(price_total=500000, price_per_person=250000)
    params.meal = "AI"
    params.max_budget = 200000
    assert filter_tours([matching, wrong_meal, too_expensive], params) == [matching]
