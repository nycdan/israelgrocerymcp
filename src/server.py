"""Israel Grocery MCP Server — unified Shufersal + Tiv Taam + future stores."""
from __future__ import annotations

import traceback
from typing import Optional

from mcp.server.fastmcp import FastMCP

from auth.session_store import MultiStoreSessionStore
from comparison.engine import compare_recipe, format_comparison
from config import AppSettings
from query_hebrew import query_to_hebrew
from matching.product_search import search_ingredient_across_stores, search_ingredient_in_store
from models import AddItemRequest, IngredientMatch, UserPreferences
from recipes.parser import fetch_recipe_from_url, parse_recipe_text
from stores import StoreRegistry, build_registry
from stores.ramilevy import RamiLevyStore
from stores.tivtaam import TivTaamStore

mcp = FastMCP("Israel Grocery")

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_settings: Optional[AppSettings] = None
_store: Optional[MultiStoreSessionStore] = None
_registry: Optional[StoreRegistry] = None


def _get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = AppSettings()
        _settings.ensure_dirs()
    return _settings


def _get_store() -> MultiStoreSessionStore:
    global _store
    if _store is None:
        _store = MultiStoreSessionStore(_get_settings().state_dir)
    return _store


def _get_registry() -> StoreRegistry:
    global _registry
    if _registry is None:
        _registry = build_registry(_get_settings(), _get_store())
    return _registry


def _get_prefs() -> UserPreferences:
    return _get_store().load_preferences()


def _active_stores(store_ids: Optional[list[str]] = None):
    """Return the requested stores, or all stores if store_ids is None."""
    registry = _get_registry()
    if store_ids:
        stores = []
        for sid in store_ids:
            try:
                stores.append(registry.get_or_raise(sid))
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        return stores
    return registry.all()


# ---------------------------------------------------------------------------
# Auth tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def login_status() -> str:
    """Show the login status for all configured grocery stores."""
    registry = _get_registry()
    lines = ["**Grocery Store Login Status**\n"]
    for store in registry.all():
        cached = "✅ session cached" if store.is_logged_in_cached() else "❌ not logged in"
        lines.append(f"  {store.store_name:15s}  {cached}")
        if not store.is_logged_in_cached():
            lines.append(f"                   → {store.login_hint()}")
    return "\n".join(lines)


@mcp.tool()
async def login_tivtaam(email: str, password: str) -> str:
    """
    Log in to Tiv Taam with email and password.

    Args:
        email: Your Tiv Taam account email address.
        password: Your Tiv Taam account password.
    """
    registry = _get_registry()
    store = registry.get_or_raise("tivtaam")
    assert isinstance(store, TivTaamStore)
    return await store.login(email, password)


@mcp.tool()
async def login_shufersal() -> str:
    """
    Open a browser window for Shufersal login.
    Complete the sign-in on shufersal.co.il and the session will be captured automatically.
    Requires Playwright: run `uv run playwright install chromium` once.
    """
    registry = _get_registry()
    store = registry.get_or_raise("shufersal")
    settings = _get_settings()
    return await store.start_browser_login(str(settings.browser_dir))  # type: ignore[attr-defined]


def _ramilevi_browser_dir() -> str:
    settings = _get_settings()
    d = settings.browser_dir / "ramilevi"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@mcp.tool()
async def login_ramilevi_browser() -> str:
    """
    Log in to Rami Levy — NO email or password needed from the user.
    Opens a browser window. The user logs in (click התחברות, enter email, SMS code).
    The token is captured automatically once login completes — no second step needed.
    Use this as the primary Rami Levy login method.
    """
    registry = _get_registry()
    store = registry.get_or_raise("ramilevi")
    assert isinstance(store, RamiLevyStore)
    return await store.browser_login(_ramilevi_browser_dir())


@mcp.tool()
async def check_login(store_id: Optional[str] = None) -> str:
    """
    Perform a live login check for one or all stores.

    Args:
        store_id: "shufersal", "tivtaam", or "ramilevi" — omit for all.
    """
    stores = _active_stores([store_id] if store_id else None)
    lines = []
    for store in stores:
        ok = await store.check_login_status()
        status = "✅ authenticated" if ok else "❌ not authenticated"
        lines.append(f"{store.store_name}: {status}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_preferences(
    preferred_store: Optional[str] = None,
    shopping_strategy: str = "cheapest",
    prefer_organic: bool = False,
    prefer_budget: bool = False,
    prefer_store_brand: bool = False,
    preferred_brands: Optional[list[str]] = None,
    banned_keywords: Optional[list[str]] = None,
    skip_pantry: bool = True,
    tivtaam_branch_id: Optional[int] = None,
) -> str:
    """
    Set your shopping preferences.

    Args:
        preferred_store: Default store ("shufersal" or "tivtaam"). None = always compare.
        shopping_strategy: "cheapest" | "preferred_store" | "quality"
        prefer_organic: Prefer organic products when available.
        prefer_budget: Prefer sale/discounted products.
        prefer_store_brand: Prefer Shufersal-brand products.
        preferred_brands: Brand names to prefer (e.g. ["Tnuva", "Osem"]).
        banned_keywords: Words to avoid in product names.
        skip_pantry: Skip common pantry staples (salt, oil, spices) when parsing recipes.
        tivtaam_branch_id: Override Tiv Taam branch ID (default: 943).
    """
    prefs = UserPreferences(
        preferred_store=preferred_store,
        shopping_strategy=shopping_strategy,
        prefer_organic=prefer_organic,
        prefer_budget=prefer_budget,
        prefer_store_brand=prefer_store_brand,
        preferred_brands=preferred_brands or [],
        banned_keywords=banned_keywords or [],
        skip_pantry=skip_pantry,
        tivtaam_branch_id=tivtaam_branch_id,
    )
    _get_store().save_preferences(prefs)
    return (
        f"Preferences saved:\n"
        f"  preferred_store = {preferred_store or 'auto (compare all)'}\n"
        f"  strategy        = {shopping_strategy}\n"
        f"  organic         = {prefer_organic}\n"
        f"  budget / sale   = {prefer_budget}\n"
        f"  store brand     = {prefer_store_brand}\n"
        f"  brands          = {preferred_brands or []}\n"
        f"  skip_pantry     = {skip_pantry}"
    )


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_products(
    query: str,
    stores: Optional[list[str]] = None,
    max_results: int = 8,
) -> str:
    """
    Search for products across all (or selected) grocery stores.
    English queries are automatically translated to Hebrew for better Israeli store results.

    Args:
        query: Search term (English or Hebrew).
        stores: Limit to specific stores, e.g. ["tivtaam"] or ["shufersal", "tivtaam"].
        max_results: Max results per store (default: 8).
    """
    search_query = query_to_hebrew(query)
    active = _active_stores(stores)
    all_lines: list[str] = []
    for store in active:
        try:
            products = await store.search(search_query, max_results=max_results)
        except Exception as exc:
            all_lines.append(f"\n**{store.store_name}**: error — {exc}")
            continue
        if not products:
            all_lines.append(f"\n**{store.store_name}**: no results for '{search_query}'")
            continue
        all_lines.append(f"\n**{store.store_name}** ({len(products)} results):")
        for p in products:
            stock = "✓" if p.in_stock else "✗"
            brand = f" [{p.brand}]" if p.brand else ""
            all_lines.append(f"  [{stock}] ID:{p.product_id:<10}  {p.name}{brand}  {p.display_price}")
    if not all_lines:
        return f"No results found for '{search_query}'."
    return f"Search: **'{search_query}'**" + "\n".join(all_lines)


@mcp.tool()
async def compare_prices(query: str) -> str:
    """
    Compare prices for a product across all connected stores.
    English queries are translated to Hebrew for better Israeli store results.

    Args:
        query: What you're looking for (e.g. "eggs", "chicken breast", "lady apples").
    """
    from models import IngredientIntent
    search_query = query_to_hebrew(query)
    ing = IngredientIntent(raw=query, name=search_query)
    prefs = _get_prefs()
    stores = _get_registry().all()
    match = await search_ingredient_across_stores(ing, stores, prefs)

    lines = [f"**Price comparison: '{query}'**\n"]
    for store_id, product in match.best_by_store.items():
        conf = match.confidence_by_store.get(store_id, 0)
        rec = " ← recommended" if store_id == match.recommended_store else ""
        if product:
            lines.append(
                f"  🏪 {store_id.title():12s}  {product.name}  {product.display_price}"
                f"  (conf={conf:.0%}){rec}"
            )
        else:
            lines.append(f"  🏪 {store_id.title():12s}  no match found")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cart tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def show_cart(store_id: Optional[str] = None) -> str:
    """
    Show the current cart contents.

    Args:
        store_id: "shufersal", "tivtaam", or "ramilevi". If omitted, shows all carts.
    """
    stores = _active_stores([store_id] if store_id else None)
    parts: list[str] = []
    for store in stores:
        cart = await store.get_cart()
        if cart.warnings:
            parts.append(f"**{store.store_name}**: {'; '.join(cart.warnings)}")
            continue
        if not cart.lines:
            parts.append(f"**{store.store_name}**: cart is empty")
            continue
        total_str = f"{cart.total:.2f}₪" if cart.total is not None else "—"
        sub = [f"**{store.store_name} cart** ({cart.item_count} items, total: {total_str}):"]
        for line in cart.lines:
            price_str = f"{line.price:.2f}₪/ea" if line.price is not None else ""
            line_total_str = f"= {line.total:.2f}₪" if line.total is not None else ""
            sub.append(f"  • {line.product_name}  x{line.quantity:g}  {price_str}  {line_total_str}".rstrip())
        sub.append(f"  ─────────────────")
        sub.append(f"  Total: {total_str}")
        parts.append("\n".join(sub))
    return "\n\n".join(parts) if parts else "No carts found."


@mcp.tool()
async def add_to_cart(store_id: str, product_id: str, quantity: float = 1.0) -> str:
    """
    Add a specific product to a store's cart.

    Args:
        store_id: "shufersal", "tivtaam", or "ramilevi".
        product_id: The product ID from search results.
        quantity: Number of units (default: 1).
    """
    store = _get_registry().get_or_raise(store_id)
    result = await store.add_to_cart(product_id, quantity)
    if result.success:
        cart_info = (
            f" Cart now has {result.cart.item_count} items"
            + (f", total: {result.cart.total:.2f}₪" if result.cart.total is not None else "")
            + "."
            if result.cart else ""
        )
        return f"✅ {result.message}{cart_info}"
    return f"❌ {result.message}"


@mcp.tool()
async def verify_cart(store_id: str = "tivtaam") -> str:
    """
    Re-check every item in the cart for live stock availability and automatically
    swap out-of-stock items with the best available replacement.

    Call this after adding all items to a cart to catch anything that went
    out of stock between search and checkout.

    Args:
        store_id: Which store cart to verify. Currently supports "tivtaam".
    """
    if store_id != "tivtaam":
        return f"Cart verification is not yet supported for {store_id}."

    store = _get_registry().get_or_raise(store_id)
    if not isinstance(store, TivTaamStore):
        return "Store is not a TivTaamStore instance."

    result = await store.verify_and_fix_cart()

    lines = ["**Cart verification complete:**\n"]

    if result["verified"]:
        lines.append(f"✅ In stock ({len(result['verified'])}):")
        for name in result["verified"]:
            lines.append(f"   • {name}")

    if result["swapped"]:
        lines.append(f"\n🔄 Swapped out-of-stock items ({len(result['swapped'])}):")
        for s in result["swapped"]:
            lines.append(f"   • {s['old']}  →  {s['new']}  {s['price']}")

    if result["failed"]:
        lines.append(f"\n⚠️  Could not find a replacement for ({len(result['failed'])}):")
        for name in result["failed"]:
            lines.append(f"   • {name}  (remove manually or try a different search)")

    cart = result.get("cart")
    if cart and cart.total:
        lines.append(f"\nCart total: {cart.total:.2f}₪  ({cart.item_count} items)")

    if not result["swapped"] and not result["failed"]:
        lines.append("\nAll items are in stock — no changes needed.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recipe tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def plan_recipe_ingredients(recipe_text: str) -> str:
    """
    Parse a recipe and list the ingredients that will be searched for.

    Args:
        recipe_text: Recipe text with ingredients list, or a URL to a recipe page.
    """
    prefs = _get_prefs()
    if recipe_text.strip().startswith("http"):
        plan = await fetch_recipe_from_url(recipe_text.strip())
        if not plan:
            return "Could not fetch or parse the recipe from that URL."
    else:
        plan = parse_recipe_text(recipe_text, skip_pantry=prefs.skip_pantry)

    if not plan.ingredients:
        return "No ingredients found in the recipe text."

    lines = [f"**{plan.title}** — {len(plan.ingredients)} ingredients:"]
    for ing in plan.ingredients:
        qty_str = f"{ing.quantity:g} {ing.unit}".strip()
        pantry = " *(pantry — will skip)*" if ing.is_pantry else ""
        lines.append(f"  • {ing.name}  ({qty_str}){pantry}")
    return "\n".join(lines)


@mcp.tool()
async def compare_recipe(recipe_text: str) -> str:
    """
    Parse a recipe and compare ingredient prices across all connected stores.
    Shows which store is cheapest overall and where to buy each item for the best deal.

    Args:
        recipe_text: Recipe text or URL.
    """
    prefs = _get_prefs()
    if recipe_text.strip().startswith("http"):
        plan = await fetch_recipe_from_url(recipe_text.strip())
        if not plan:
            return "Could not fetch recipe from that URL."
    else:
        plan = parse_recipe_text(recipe_text, skip_pantry=prefs.skip_pantry)

    if not plan.ingredients:
        return "No ingredients found in the recipe text."

    stores = _get_registry().all()
    matches: list[IngredientMatch] = []
    for ing in plan.ingredients:
        match = await search_ingredient_across_stores(ing, stores, prefs)
        matches.append(match)

    comp = compare_recipe(plan.title, matches)
    return format_comparison(comp)


@mcp.tool()
async def add_recipe_to_cart(
    recipe_text: str,
    store_id: Optional[str] = None,
    strategy: str = "cheapest",
    confirm_all: bool = False,
) -> str:
    """
    Parse a recipe, find best-matching products, and add them to a cart.

    Args:
        recipe_text: Recipe text or URL.
        store_id: Which store to add to ("shufersal" or "tivtaam").
                  If omitted, uses your preferred_store or the cheapest per item.
        strategy: "cheapest" — buy each item from its cheapest store.
                  "preferred_store" — use your preferred_store for everything.
                  "single_store" — use store_id for everything (requires store_id).
        confirm_all: If True, add low-confidence matches without pausing.
    """
    prefs = _get_prefs()
    # Determine effective strategy
    effective_strategy = strategy
    if store_id:
        effective_strategy = "single_store"
    elif prefs.preferred_store and not store_id:
        effective_strategy = prefs.shopping_strategy

    if recipe_text.strip().startswith("http"):
        plan = await fetch_recipe_from_url(recipe_text.strip())
        if not plan:
            return "Could not fetch recipe from that URL."
    else:
        plan = parse_recipe_text(recipe_text, skip_pantry=prefs.skip_pantry)

    if not plan.ingredients:
        return "No ingredients found in the recipe text."

    stores = _get_registry().all()
    matches: list[IngredientMatch] = []
    for ing in plan.ingredients:
        match = await search_ingredient_across_stores(ing, stores, prefs)
        matches.append(match)

    # Decide which store to use per ingredient
    added = []
    skipped = []
    needs_review = []
    errors = []

    for match in matches:
        if match.skipped:
            skipped.append(f"{match.ingredient.name} ({match.skip_reason})")
            continue

        # Pick target store
        if effective_strategy == "single_store" and store_id:
            target_sid = store_id
            product = match.best_by_store.get(target_sid)
            confidence = match.confidence_by_store.get(target_sid, 0)
        else:
            target_sid = match.recommended_store
            product = match.best_overall
            confidence = match.confidence_by_store.get(target_sid or "", 0) if target_sid else 0

        if not product or not target_sid:
            skipped.append(f"{match.ingredient.name} (no match found)")
            continue

        if confidence < 0.35 and not confirm_all:
            needs_review.append(
                f"{match.ingredient.name}  →  [{target_sid}] {product.name}  {product.display_price}  "
                f"(ID:{product.product_id}, conf={confidence:.0%})"
            )
            continue

        target_store = _get_registry().get_or_raise(target_sid)
        result = await target_store.add_to_cart(product.product_id, match.ingredient.quantity)
        if result.success:
            added.append(
                f"[{target_sid}] {match.ingredient.name}  →  {product.name}  {product.display_price}"
            )
        else:
            errors.append(f"{match.ingredient.name}: {result.message}")

    lines = [f"**{plan.title}** — cart update:"]
    if added:
        lines.append(f"\n✅ Added ({len(added)}):")
        lines.extend(f"   • {a}" for a in added)
    if skipped:
        lines.append(f"\n⏭  Skipped ({len(skipped)}):")
        lines.extend(f"   • {s}" for s in skipped)
    if needs_review:
        lines.append(f"\n⚠️  Needs review — use confirm_all=true to add these ({len(needs_review)}):")
        lines.extend(f"   • {n}" for n in needs_review)
    if errors:
        lines.append(f"\n❌ Errors ({len(errors)}):")
        lines.extend(f"   • {e}" for e in errors)

    # Auto-verify stock after all adds — swap out anything that went OOS
    if added:
        stores_with_adds = {a.split("]")[0].lstrip("[") for a in added if a.startswith("[")}
        for sid in stores_with_adds:
            store_obj = _get_registry().get_or_raise(sid)
            if isinstance(store_obj, TivTaamStore):
                verify_result = await store_obj.verify_and_fix_cart()
                if verify_result["swapped"]:
                    lines.append(f"\n🔄 Auto-swapped out-of-stock items:")
                    for s in verify_result["swapped"]:
                        lines.append(f"   • {s['old']}  →  {s['new']}  {s['price']}")
                if verify_result["failed"]:
                    lines.append(f"\n⚠️  No replacement found for:")
                    for name in verify_result["failed"]:
                        lines.append(f"   • {name}")
                if not verify_result["swapped"] and not verify_result["failed"]:
                    lines.append("\n✅ All cart items verified in stock.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@mcp.tool()
async def diagnose(store_id: Optional[str] = None, test_query: str = "eggs") -> str:
    """
    Run a full diagnostic for one or all stores.
    Shows session status, raw API response, and parsed result count.

    Args:
        store_id: "shufersal", "tivtaam", or "ramilevi" — omit to check all.
        test_query: Simple English word to test search (default: "eggs").
    """
    search_query = query_to_hebrew(test_query)
    stores = _active_stores([store_id] if store_id else None)
    settings = _get_settings()
    lines = ["=== Israel Grocery MCP Diagnostics ===\n"]
    lines.append(f"State dir : {settings.state_dir}")
    lines.append(f"Debug dir : {settings.debug_dir}\n")

    for store in stores:
        lines.append(f"── {store.store_name} ──")
        session = _get_store().load_session(store.store_id)
        if session:
            if store.store_id == "tivtaam":
                lines.append(f"  Session  : user_id={session.get('user_id')}  email={session.get('email')}")
                lines.append(f"             token={'yes' if session.get('token') else 'NO'}  cart_id={session.get('cart_id')}")
            elif store.store_id == "ramilevi":
                lines.append(f"  Session  : token={'yes' if session.get('token') else 'NO'}  user_id={session.get('user_id', 'N/A')}")
            else:
                lines.append(f"  Session  : authenticated={session.get('authenticated')}  cookies={session.get('cookie_count', 0)}")
        else:
            lines.append("  Session  : none — not logged in")

        try:
            raw = await store.raw_search(search_query)
            lines.append(f"  Search   : '{search_query}' → HTTP {raw.get('status', '?')}  url={raw.get('url', '')[:80]}")
            lines.append(f"  Body     : keys={raw.get('body_keys', '?')}")
            if "error" in raw:
                lines.append(f"  Error    : {raw['error']}")
        except Exception as exc:
            lines.append(f"  Search   : FAILED — {exc}")
            lines.append(traceback.format_exc()[-300:])

        try:
            products = await store.search(search_query, max_results=3)
            lines.append(f"  Results  : {len(products)} products parsed")
            for p in products[:2]:
                lines.append(f"    • {p.name}  {p.display_price}  (id={p.product_id})")
        except Exception as exc:
            lines.append(f"  Results  : FAILED — {exc}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Startup: auto-login if credentials are in the environment
# ---------------------------------------------------------------------------


async def _auto_login() -> None:
    """
    Silently log in to stores at startup if credentials are configured in .env.
    Skips login if a valid session already exists.
    """
    import sys

    # Tiv Taam — email + password from env
    tt_cfg = _get_settings().tivtaam
    if tt_cfg.email and tt_cfg.password:
        session = _get_store().load_session("tivtaam")
        already_ok = bool(session and session.get("token"))
        if not already_ok:
            registry = _get_registry()
            store = registry.get("tivtaam")
            if store:
                msg = await store.login(tt_cfg.email, tt_cfg.password)  # type: ignore[attr-defined]
                print(f"[israelgrocery] Tiv Taam auto-login: {msg}", file=sys.stderr)
        else:
            print("[israelgrocery] Tiv Taam: using existing session.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import asyncio

    # Run startup tasks (auto-login) before handing off to the MCP event loop
    asyncio.run(_auto_login())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
