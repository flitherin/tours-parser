import pytest

from main import parse_range


def test_parse_range() -> None:
    assert parse_range("7-10", "nights") == (7, 10)
    assert parse_range("4", "stars") == (4, 4)
    with pytest.raises(ValueError):
        parse_range("10-7", "nights")
