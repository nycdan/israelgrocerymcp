"""Unified product ranker — works across all stores."""
from __future__ import annotations

import re
from typing import Optional

from models import IngredientIntent, StoreProduct, UserPreferences


def _tokenize(text: str) -> set[str]:
    """Tokenize text supporting English, Hebrew, and digits."""
    return {t for t in re.findall(r"[A-Za-z\u0590-\u05FF0-9]+", text.lower()) if t}


def score_product(
    ingredient: IngredientIntent,
    product: StoreProduct,
    prefs: UserPreferences,
) -> float:
    """Return a confidence score in [0.0, 1.0] for how well product matches ingredient."""
    ing_tokens = _tokenize(ingredient.name)
    prod_tokens = _tokenize(product.name)

    # Token overlap (most important signal)
    overlap = len(ing_tokens & prod_tokens)
    score = overlap * 10.0

    # Exact substring bonus
    if ingredient.name.lower() in product.name.lower():
        score += 15.0

    # Sale price bonus (budget shoppers love it)
    if product.is_on_sale:
        score += 8.0 if prefs.prefer_budget else 3.0

    # Brand preference
    if prefs.preferred_brands:
        brand_lower = product.brand.lower()
        if any(b.lower() in brand_lower for b in prefs.preferred_brands):
            score += 12.0

    # Store brand preference (Shufersal-specific)
    if prefs.prefer_store_brand and "shufersal" in product.brand.lower():
        score += 8.0

    # Organic preference
    if prefs.prefer_organic and "organic" in product.name.lower():
        score += 6.0

    # Banned keywords penalty
    if prefs.banned_keywords:
        name_lower = product.name.lower()
        if any(kw.lower() in name_lower for kw in prefs.banned_keywords):
            score -= 25.0

    return max(0.0, min(1.0, score / 45.0))


def choose_best(
    ingredient: IngredientIntent,
    products: list[StoreProduct],
    prefs: UserPreferences,
) -> tuple[Optional[StoreProduct], float]:
    """Return (best_product, confidence) for the given ingredient."""
    if not products:
        return None, 0.0

    # Hard-exclude out-of-stock items. Only fall back to them if every candidate
    # is out of stock (shouldn't happen given the search filter, but be safe).
    in_stock = [p for p in products if p.in_stock]
    candidates = in_stock if in_stock else products

    scored = sorted(
        [(p, score_product(ingredient, p, prefs)) for p in candidates],
        key=lambda x: x[1],
        reverse=True,
    )
    best, best_score = scored[0]

    # Low confidence if: score itself is weak OR two candidates are very close
    needs_confirmation = (
        best_score < 0.35
        or (len(scored) > 1 and abs(best_score - scored[1][1]) < 0.1 and best_score < 0.7)
    )
    if needs_confirmation:
        best_score = min(best_score, 0.49)

    return best, best_score
