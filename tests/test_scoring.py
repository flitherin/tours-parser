from scoring import score_tours, sort_tours

WEIGHTS = {
    "price": 0.38,
    "hotel": 0.18,
    "meal": 0.12,
    "services": 0.10,
    "flight": 0.10,
    "below_market": 0.12,
}


def test_score_range_and_relative_price(make_tour) -> None:
    cheap = make_tour(price_total=100000, price_per_person=50000)
    middle = make_tour(hotel_name="Middle Hotel", price_total=150000, price_per_person=75000)
    expensive = make_tour(hotel_name="Expensive Hotel", price_total=220000, price_per_person=110000)
    result = score_tours([expensive, cheap, middle], WEIGHTS)
    assert all(0 <= tour.score <= 100 for tour in result)
    assert cheap.score > expensive.score
    assert sort_tours(result, "value")[0] == cheap
    assert sort_tours(result, "price")[0] == cheap


def test_missing_optional_data_is_neutral(make_tour) -> None:
    tour = make_tour(
        rating=None,
        includes_transfer=None,
        includes_insurance=None,
        luggage=None,
        direct_flight=None,
    )
    score_tours([tour], WEIGHTS)
    assert 0 <= tour.score <= 100
