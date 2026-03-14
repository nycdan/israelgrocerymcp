"""Shared data models for the Israel Grocery MCP server."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class UserPreferences(BaseModel):
    preferred_store: Optional[str] = None          # "shufersal" | "tivtaam" | None (auto)
    shopping_strategy: Literal["cheapest", "preferred_store", "quality"] = "cheapest"
    prefer_organic: bool = False
    prefer_budget: bool = False
    prefer_store_brand: bool = False                # Shufersal "Shufersal" brand
    preferred_brands: list[str] = Field(default_factory=list)
    banned_keywords: list[str] = Field(default_factory=list)
    skip_pantry: bool = True
    tivtaam_branch_id: Optional[int] = None        # Override default Tiv Taam branch


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


class StoreProduct(BaseModel):
    """A product from any grocery store — unified across Shufersal and Tiv Taam."""
    store_id: str                                   # "shufersal" | "tivtaam"
    product_id: str                                 # always string
    name: str
    price: Optional[float] = None
    sale_price: Optional[float] = None
    is_on_sale: bool = False
    brand: str = ""
    category: str = ""
    in_stock: bool = True
    currency: str = "ILS"
    # Shufersal-specific
    size_text: str = ""
    product_url: str = ""
    # Tiv Taam-specific
    image_url: str = ""
    weight: Optional[float] = None          # grams per unit (for packaged) or None
    weight_unit: str = ""
    unit_price: Optional[float] = None
    # Weighable produce (loose items sold by kg)
    is_weighable: bool = False              # True → quantity in cart = kg, price = per-kg
    unit_resolution: float = 0.0           # minimum cart increment (kg), e.g. 0.1 = 100g steps

    @property
    def effective_price(self) -> Optional[float]:
        """Sale price if available, otherwise regular price."""
        return self.sale_price if self.is_on_sale and self.sale_price else self.price

    @property
    def display_price(self) -> str:
        ep = self.effective_price
        if ep is None:
            return "price unknown"
        unit_label = "/kg" if self.is_weighable else ""
        if self.is_on_sale and self.sale_price and self.price:
            return f"{self.sale_price:.2f}₪{unit_label} (was {self.price:.2f}₪{unit_label})"
        return f"{ep:.2f}₪{unit_label}"


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------


class CartLine(BaseModel):
    product_id: str
    product_name: str
    quantity: float = 1.0
    price: Optional[float] = None
    total: Optional[float] = None
    line_id: Optional[str] = None
    is_weighted: bool = False


class CartView(BaseModel):
    store_id: str
    order_id: Optional[str] = None
    cart_id: Optional[str] = None
    lines: list[CartLine] = Field(default_factory=list)
    subtotal: Optional[float] = None
    total: Optional[float] = None
    item_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class CartMutationResult(BaseModel):
    success: bool
    store_id: str
    product_id: str = ""
    quantity: float = 1.0
    message: str = ""
    cart: Optional[CartView] = None


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


class IngredientIntent(BaseModel):
    raw: str                   # original text
    name: str                  # normalised item name
    quantity: float = 1.0
    unit: str = ""
    is_pantry: bool = False
    notes: str = ""
    search_terms: list[str] = Field(default_factory=list)


class RecipePlan(BaseModel):
    title: str = "Recipe"
    ingredients: list[IngredientIntent] = Field(default_factory=list)
    servings: int = 1


# ---------------------------------------------------------------------------
# Matching (per-store + cross-store)
# ---------------------------------------------------------------------------


class IngredientMatch(BaseModel):
    """Best product match per store for a single ingredient."""
    ingredient: IngredientIntent
    best_by_store: dict[str, Optional[StoreProduct]] = Field(default_factory=dict)
    confidence_by_store: dict[str, float] = Field(default_factory=dict)
    recommended_store: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""

    @property
    def best_overall(self) -> Optional[StoreProduct]:
        if self.recommended_store:
            return self.best_by_store.get(self.recommended_store)
        # Fallback: cheapest in-stock
        candidates = [
            p for p in self.best_by_store.values()
            if p and p.in_stock and p.effective_price is not None
        ]
        return min(candidates, key=lambda p: p.effective_price, default=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


class SplitItem(BaseModel):
    """Best-per-ingredient split-cart recommendation."""
    ingredient_name: str
    recommended_store: str
    product: StoreProduct
    savings: float = 0.0    # vs. most expensive option across stores


class RecipeComparison(BaseModel):
    recipe_title: str
    matches: list[IngredientMatch] = Field(default_factory=list)
    cost_by_store: dict[str, float] = Field(default_factory=dict)
    cheapest_store: Optional[str] = None
    split_recommendation: list[SplitItem] = Field(default_factory=list)
    split_total_savings: float = 0.0


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class AddItemRequest(BaseModel):
    store_id: str
    product_id: str
    quantity: float = 1.0
    sold_by: str = "unit"
