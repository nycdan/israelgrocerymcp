"""Tests for the unified product ranker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from matching.ranker import choose_best, score_product
from models import IngredientIntent, StoreProduct, UserPreferences

_PREFS = UserPreferences()
_PREFS_ORGANIC = UserPreferences(prefer_organic=True)
_PREFS_BUDGET = UserPreferences(prefer_budget=True)


def _product(name: str, price: float = 10.0, in_stock: bool = True, store: str = "tivtaam") -> StoreProduct:
    return StoreProduct(store_id=store, product_id="1", name=name, price=price, in_stock=in_stock)


def _ingredient(name: str) -> IngredientIntent:
    return IngredientIntent(raw=name, name=name)


def test_exact_token_match_scores_higher():
    ing = _ingredient("chicken breast")
    exact = _product("Chicken Breast 500g")
    unrelated = _product("Fish Fillet 300g")
    assert score_product(ing, exact, _PREFS) > score_product(ing, unrelated, _PREFS)


def test_out_of_stock_excluded():
    """choose_best must never return an out-of-stock product when an in-stock one exists."""
    ing = _ingredient("milk")
    in_stock = _product("Milk 1L", in_stock=True)
    out_of_stock = _product("Milk 1L Premium", in_stock=False)
    best, _ = choose_best(ing, [out_of_stock, in_stock], _PREFS)
    assert best is not None
    assert best.in_stock, "choose_best returned an out-of-stock product"


def test_organic_preference_bonus():
    ing = _ingredient("eggs")
    organic = _product("Organic Eggs 12pk")
    regular = _product("Eggs 12pk")
    assert score_product(ing, organic, _PREFS_ORGANIC) > score_product(ing, regular, _PREFS_ORGANIC)


def test_choose_best_returns_top_match():
    ing = _ingredient("tomato")
    products = [
        _product("Tomatoes 500g", price=5.0),
        _product("Cherry Tomatoes 250g", price=8.0),
        _product("Banana 1kg", price=4.0),
    ]
    best, confidence = choose_best(ing, products, _PREFS)
    assert best is not None
    assert "tomato" in best.name.lower() or "tomato" in best.name.lower()
    assert confidence > 0


def test_choose_best_empty_returns_none():
    ing = _ingredient("saffron")
    best, confidence = choose_best(ing, [], _PREFS)
    assert best is None
    assert confidence == 0.0


def test_banned_keyword_penalty():
    ing = _ingredient("cheese")
    normal = _product("Mozzarella Cheese 250g")
    prefs_banned = UserPreferences(banned_keywords=["mozzarella"])
    score_normal = score_product(ing, normal, _PREFS)
    score_banned = score_product(ing, normal, prefs_banned)
    assert score_banned < score_normal
