from datetime import datetime, timedelta, timezone

import pytest

from storage import Storage


def test_cache_expires(tmp_path) -> None:
    storage = Storage(tmp_path / "tours.db")
    storage.set_cached("key", '{"ok":true}')
    assert storage.get_cached("key", 60) == '{"ok":true}'
    assert storage.get_cached("key", 0) is None


def test_price_history_uses_previous_run(tmp_path, make_tour) -> None:
    storage = Storage(tmp_path / "tours.db")
    tour = make_tour(dedup_key="stable")
    first = datetime.now(timezone.utc) - timedelta(hours=1)
    storage.save_price_history([tour], "first", first)
    second = datetime.now(timezone.utc)
    assert storage.previous_prices(["stable"], second) == {"stable": tour.price_total}
    cheaper = make_tour(dedup_key="stable", price_total=120000, price_per_person=60000)
    cheaper.previous_price = storage.previous_prices(["stable"], second)["stable"]
    assert cheaper.price_dropped
    assert cheaper.price_drop_percent == pytest.approx((1 - 120000 / 140000) * 100)
