"""Placeholder tests until domain logic is implemented."""

from fiskal import __version__
from fiskal.domain import seed


def test_version_is_set() -> None:
    """The package exposes a version string."""
    assert isinstance(__version__, str)
    assert __version__


def test_seed_categories_use_keys() -> None:
    """Default categories are defined by translation keys, not display text."""
    for category in seed.DEFAULT_CATEGORIES:
        assert category.key.startswith("category.")
        assert category.kind in {"income", "expense"}


def test_seed_cost_centers_use_keys() -> None:
    """Default cost centers are defined by translation keys."""
    for cost_center in seed.DEFAULT_COST_CENTERS:
        assert cost_center.key.startswith("costcenter.")
