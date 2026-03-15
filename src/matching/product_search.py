"""Multi-store ingredient search."""
from __future__ import annotations

from models import IngredientIntent, IngredientMatch, StoreProduct, UserPreferences
from matching.ranker import choose_best
from query_hebrew import query_to_hebrew
from stores.base import BaseStore

# Synonym expansion for better search coverage
_EN_SYNONYMS: dict[str, list[str]] = {
    "chicken": ["עוף", "chicken breast", "chicken thigh"],
    "beef": ["בקר", "ground beef", "minced beef"],
    "lamb": ["כבש", "lamb chops"],
    "fish": ["דג", "salmon", "tuna"],
    "eggs": ["ביצים", "eggs"],
    "milk": ["חלב", "milk"],
    "butter": ["חמאה", "butter"],
    "cheese": ["גבינה", "cheese"],
    "cream": ["שמנת", "cream"],
    "yogurt": ["יוגורט", "yogurt"],
    "bread": ["לחם", "bread"],
    "pasta": ["פסטה", "pasta"],
    "rice": ["אורז", "rice"],
    "onion": ["בצל", "onion"],
    "garlic": ["שום", "garlic"],
    "tomato": ["עגבנייה", "tomatoes"],
    "potato": ["תפוח אדמה", "potatoes"],
    "carrot": ["גזר", "carrots"],
    "lemon": ["לימון", "lemon"],
    "mushroom": ["פטריות", "mushrooms"],
    "spinach": ["תרד", "spinach"],
    "zucchini": ["קישוא", "zucchini"],
    "eggplant": ["חציל", "eggplant"],
    "pepper": ["פלפל", "bell pepper"],
    "apple": ["תפוח", "apples"],
    "banana": ["בננה", "bananas"],
}


def _search_queries(ingredient: IngredientIntent) -> list[str]:
    """Generate 1-3 search queries for an ingredient. Always prefer Hebrew for Israeli stores."""
    # Translate to Hebrew first — Israeli store APIs index in Hebrew
    hebrew_name = query_to_hebrew(ingredient.name)
    queries = [hebrew_name]
    # Add custom search terms (translated)
    for term in ingredient.search_terms:
        translated = query_to_hebrew(term)
        if translated not in queries:
            queries.append(translated)
    # Add English synonyms / Hebrew variants for coverage
    lower = ingredient.name.lower()
    for key, variants in _EN_SYNONYMS.items():
        if key in lower:
            queries.extend(v for v in variants if v not in queries)
            break
    return queries[:3]


async def search_ingredient_in_store(
    ingredient: IngredientIntent,
    store: BaseStore,
    prefs: UserPreferences,
    max_results: int = 8,
) -> tuple[StoreProduct | None, float]:
    """Search one store for an ingredient. Returns (best_product, confidence)."""
    queries = _search_queries(ingredient)
    all_products: list[StoreProduct] = []
    for query in queries:
        products = await store.search(query, max_results=max_results)
        # Avoid duplicates
        seen_ids = {p.product_id for p in all_products}
        all_products.extend(p for p in products if p.product_id not in seen_ids)
        if all_products:
            break  # stop early if first query returns results
    return choose_best(ingredient, all_products, prefs)


async def search_ingredient_across_stores(
    ingredient: IngredientIntent,
    stores: list[BaseStore],
    prefs: UserPreferences,
    max_results: int = 8,
) -> IngredientMatch:
    """Search all given stores for an ingredient and return a cross-store IngredientMatch."""
    if ingredient.is_pantry and prefs.skip_pantry:
        return IngredientMatch(
            ingredient=ingredient,
            skipped=True,
            skip_reason="pantry item",
        )

    best_by_store: dict[str, StoreProduct | None] = {}
    confidence_by_store: dict[str, float] = {}

    for store in stores:
        best, conf = await search_ingredient_in_store(ingredient, store, prefs, max_results)
        best_by_store[store.store_id] = best
        confidence_by_store[store.store_id] = conf

    recommended = _pick_recommended_store(best_by_store, confidence_by_store, prefs)
    return IngredientMatch(
        ingredient=ingredient,
        best_by_store=best_by_store,
        confidence_by_store=confidence_by_store,
        recommended_store=recommended,
    )


def _pick_recommended_store(
    best_by_store: dict[str, StoreProduct | None],
    confidence_by_store: dict[str, float],
    prefs: UserPreferences,
) -> str | None:
    """Pick the recommended store based on user strategy."""
    available = {
        sid: p for sid, p in best_by_store.items()
        if p and p.in_stock and p.effective_price is not None
    }
    if not available:
        return next(iter(best_by_store), None)

    if prefs.shopping_strategy == "preferred_store" and prefs.preferred_store in available:
        return prefs.preferred_store

    if prefs.shopping_strategy == "cheapest":
        return min(available, key=lambda sid: available[sid].effective_price)  # type: ignore[arg-type]

    # "quality" — highest confidence
    return max(confidence_by_store, key=lambda sid: confidence_by_store.get(sid, 0))
