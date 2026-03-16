"""Shufersal grocery store adapter."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from auth.session_store import MultiStoreSessionStore
from config import ShufersalConfig
from models import CartLine, CartMutationResult, CartView, StoreProduct
from stores.base import BaseStore

STORE_ID = "shufersal"
STORE_NAME = "Shufersal"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
}


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


class ShufersalStore(BaseStore):
    store_id = STORE_ID
    store_name = STORE_NAME

    def __init__(self, cfg: ShufersalConfig, session_store: MultiStoreSessionStore) -> None:
        self._cfg = cfg
        self._ss = session_store

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._cfg.base_url,
            cookies=self._ss.build_cookie_jar(STORE_ID),
            timeout=self._cfg.request_timeout,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with self._build_client() as client:
            return await client.request(
                method, endpoint, params=params, data=data, headers=extra_headers
            )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def check_login_status(self) -> bool:
        endpoints = [
            f"{self._cfg.storefront_prefix}/authentication/status",
            f"{self._cfg.storefront_prefix}/authentication/get-status-includes-otp",
            f"{self._cfg.storefront_prefix}/cart/cartsummary",
        ]
        for ep in endpoints:
            try:
                resp = await self._request("GET", ep)
            except httpx.HTTPError:
                continue
            if "application/json" in resp.headers.get("content-type", ""):
                try:
                    payload = resp.json()
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    authenticated = bool(
                        payload.get("authenticated")
                        or payload.get("loggedIn")
                        or payload.get("isAuthenticated")
                        or payload.get("customer")
                    )
                else:
                    # Shufersal /authentication/status returns a plain JSON value
                    # (e.g. "true", true) when the user is authenticated.
                    authenticated = bool(payload) and resp.status_code < 400
                self._ss.mark_validation(STORE_ID, authenticated, f"Checked via {ep}")
                return authenticated
            # HTML fallback
            text = resp.text.lower()
            authenticated = resp.status_code < 400 and (
                "login" not in text or "cartsummary" in text or "עגלת" in resp.text
            )
            self._ss.mark_validation(STORE_ID, authenticated, f"HTML check via {ep}")
            return authenticated
        self._ss.mark_validation(STORE_ID, False, "All auth endpoints failed")
        return False

    def is_logged_in_cached(self) -> bool:
        session = self._ss.load_session(STORE_ID)
        return bool(session and session.get("authenticated"))

    def login_hint(self) -> str:
        return (
            "Run login_shufersal() — it opens a Chromium browser window. "
            "Complete the login on shufersal.co.il and the session will be captured automatically."
        )

    async def start_browser_login(self, browser_dir: str) -> str:
        """Open a browser for the user to log in. Returns a status message."""
        try:
            from playwright.async_api import (
                Error as PlaywrightError,
            )
            from playwright.async_api import (
                TimeoutError as PlaywrightTimeoutError,
            )
            from playwright.async_api import async_playwright
        except ImportError:
            return (
                "Playwright is not installed. "
                "Run: uv run playwright install chromium"
            )

        try:
            async with async_playwright() as pw:
                launch_args: dict[str, Any] = {
                    "user_data_dir": browser_dir,
                    "headless": False,
                }
                if self._cfg.browser_channel:
                    launch_args["channel"] = self._cfg.browser_channel
                ctx = await pw.chromium.launch_persistent_context(**launch_args)
                try:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(self._cfg.login_url, wait_until="domcontentloaded")
                    deadline_ms = int(self._cfg.login_timeout * 1000)
                    # Wait for the user to complete login: Shufersal redirects away from
                    # /login and /authentication pages once authentication succeeds.
                    await page.wait_for_function(
                        """() => {
                          const url = window.location.href.toLowerCase();
                          return !url.includes('/login') && !url.includes('/authentication');
                        }""",
                        timeout=deadline_ms,
                    )
                    state = await ctx.storage_state()
                    self._ss.save_storage_state(STORE_ID, state)
                    ok = await self.check_login_status()
                    cookies = state.get("cookies", []) if isinstance(state, dict) else []
                    domains = sorted({
                        c.get("domain", "") for c in cookies
                        if isinstance(c, dict) and c.get("domain")
                    })
                    status = "✅ Logged in" if ok else "⚠️ Session saved but auth check inconclusive"
                    return f"{status}. Cookies from: {', '.join(domains) or 'none'}."
                finally:
                    await ctx.close()
        except Exception as exc:  # PlaywrightTimeoutError etc.
            return f"Login failed: {exc}"

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def _product_from_dict(self, record: dict, search_term: str, source: str) -> Optional[StoreProduct]:
        product_id = (
            record.get("code") or record.get("productCode")
            or record.get("id") or record.get("productId")
        )
        name = (
            record.get("name") or record.get("displayName")
            or record.get("productName") or record.get("title")
        )
        if not product_id or not name:
            return None
        url = record.get("url") or record.get("productUrl") or ""
        if url and str(url).startswith("/"):
            url = f"{self._cfg.base_url}{url}"
        price = _as_float(
            record.get("price") or record.get("formattedPrice")
            or record.get("salePrice") or record.get("value")
        )
        brand = str(record.get("brand") or record.get("brandName") or "")
        size_text = str(record.get("packageSize") or record.get("size") or record.get("unitText") or "")
        # Treat unknown stock status as out-of-stock to avoid adding unavailable items.
        # Shufersal JSON uses inStock=true/false; some API responses omit the field.
        in_stock_raw = record.get("inStock")
        if in_stock_raw is None:
            # Fall back to checking explicit out-of-stock / purchasable flags
            out_flags = ("purchasable", "available", "isAvailable")
            in_stock = any(record.get(f) for f in out_flags) or False
        else:
            in_stock = bool(in_stock_raw)

        return StoreProduct(
            store_id=STORE_ID,
            product_id=str(product_id),
            name=str(name),
            price=price,
            brand=brand,
            size_text=size_text,
            in_stock=in_stock,
            currency=self._cfg.currency,
            product_url=str(url),
        )

    def _extract_candidates(self, payload: Any, search_term: str, source: str) -> list[StoreProduct]:
        seen: dict[str, StoreProduct] = {}

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                p = self._product_from_dict(node, search_term, source)
                if p:
                    seen.setdefault(p.product_id, p)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)
        return list(seen.values())

    def _extract_from_html(self, html: str, search_term: str, source: str) -> list[StoreProduct]:
        soup = BeautifulSoup(html, "html.parser")
        seen: dict[str, StoreProduct] = {}
        for selector in ["[data-product-code]", "[data-code]", ".tile", ".product"]:
            for node in soup.select(selector):
                pid = node.get("data-product-code") or node.get("data-code")
                name = node.get("data-product-name")
                if not name:
                    t = node.select_one("h2, h3, .name, .productName")
                    name = t.get_text(" ", strip=True) if t else None
                if not pid or not name:
                    continue
                price_node = node.select_one(".price, .linePrice, [data-price]")
                price = _as_float(price_node.get_text(" ", strip=True) if price_node else None)
                # Detect out-of-stock from common Shufersal HTML class markers
                oos_markers = ("out-of-stock", "outOfStock", "not-available", "soldOut", "disabled")
                node_classes = " ".join(node.get("class") or [])
                is_oos = any(m.lower() in node_classes.lower() for m in oos_markers)
                p = StoreProduct(
                    store_id=STORE_ID,
                    product_id=str(pid),
                    name=str(name),
                    price=price,
                    currency=self._cfg.currency,
                    in_stock=not is_oos,
                )
                seen.setdefault(p.product_id, p)
        return list(seen.values())

    async def search(self, query: str, max_results: int = 8) -> list[StoreProduct]:
        endpoint_specs = [
            ("/occ/v2/smp/products/search", {
                "query": query, "fields": "FULL", "pageSize": max_results,
                "lang": self._cfg.locale, "curr": self._cfg.currency,
                "searchQueryContext": "TEXT_SEARCH",
            }),
            (f"{self._cfg.storefront_prefix}/search/autocompleteSecure", {"term": query, "q": query}),
            (f"{self._cfg.storefront_prefix}/search", {"q": query}),
        ]
        for endpoint, params in endpoint_specs:
            try:
                resp = await self._request("GET", endpoint, params=params)
            except httpx.HTTPError:
                continue
            if "application/json" in resp.headers.get("content-type", ""):
                products = self._extract_candidates(resp.json(), query, endpoint)
                if products:
                    return products[:max_results]
            else:
                products = self._extract_from_html(resp.text, query, endpoint)
                if products:
                    return products[:max_results]
        return []

    async def raw_search(self, query: str) -> dict:
        try:
            resp = await self._request(
                "GET",
                "/occ/v2/smp/products/search",
                params={"query": query, "fields": "FULL", "pageSize": 2,
                        "lang": self._cfg.locale, "curr": self._cfg.currency,
                        "searchQueryContext": "TEXT_SEARCH"},
            )
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:500]
            return {
                "store": STORE_ID,
                "status": resp.status_code,
                "url": str(resp.url)[:120],
                "body_keys": list(body.keys()) if isinstance(body, dict) else f"type={type(body).__name__}",
                "sample": {k: v for k, v in list(body.items())[:4]} if isinstance(body, dict) else body,
            }
        except Exception as exc:
            return {"store": STORE_ID, "error": str(exc)}

    # ------------------------------------------------------------------
    # Cart
    # ------------------------------------------------------------------

    async def get_cart(self) -> CartView:
        try:
            resp = await self._request("GET", f"{self._cfg.storefront_prefix}/cart/cartsummary")
            soup = BeautifulSoup(resp.text, "html.parser")
            lines: list[CartLine] = []
            for node in soup.select(".cart-item, .item, .product, .cartItem"):
                name_node = node.select_one("h2, h3, .name, .productName")
                if not name_node:
                    continue
                qty_node = node.select_one(".qty, .quantity")
                price_node = node.select_one(".price, .linePrice")
                price_text = price_node.get_text(" ", strip=True) if price_node else ""
                lines.append(CartLine(
                    product_id="",
                    product_name=name_node.get_text(" ", strip=True),
                    quantity=float(qty_node.get_text(" ", strip=True).strip() or 1)
                    if qty_node else 1.0,
                    price=_as_float(price_text),
                ))
            subtotal_node = soup.select_one(".subtotal, .cartSummary .price, [data-subtotal]")
            subtotal = _as_float(subtotal_node.get_text(" ", strip=True) if subtotal_node else None)
            warnings = [] if resp.status_code < 400 else [f"Cart page returned HTTP {resp.status_code}."]
            return CartView(
                store_id=STORE_ID,
                lines=lines,
                subtotal=subtotal,
                total=subtotal,
                item_count=len(lines),
                warnings=warnings,
            )
        except Exception as exc:
            return CartView(store_id=STORE_ID, warnings=[f"Cart fetch failed: {exc}"])

    async def _fetch_csrf_token(self) -> Optional[str]:
        # 1. Check browser cookies saved during login — most reliable source.
        #    SAP Commerce sets a CSRFToken cookie that must mirror the form/header value.
        state = self._ss.load_storage_state(STORE_ID)
        for cookie in state.get("cookies", []):
            if cookie.get("name", "").upper() in ("CSRFTOKEN", "CSRF-TOKEN", "_CSRF"):
                val = cookie.get("value", "")
                if val:
                    return val

        # 2. Fallback: scrape from a page (homepage tends to stay stable).
        for endpoint in (
            f"{self._cfg.storefront_prefix}/",
            f"{self._cfg.storefront_prefix}/cart/cartsummary",
        ):
            try:
                resp = await self._request("GET", endpoint)
                soup = BeautifulSoup(resp.text, "html.parser")
                node = soup.select_one(
                    'input[name="CSRFToken"], input[name="_csrf"], meta[name="csrf-token"]'
                )
                if node:
                    val = node.get("value") or node.get("content")
                    if val:
                        return val
            except Exception:
                continue
        return None

    async def add_to_cart(
        self,
        product_id: str,
        quantity: float = 1.0,
        sold_by: str = "unit",
    ) -> CartMutationResult:
        csrf = await self._fetch_csrf_token()
        qty_int = max(1, int(quantity))
        payload_variants = [
            {"productCodePost": product_id, "qty": str(qty_int)},
            {"productCode": product_id, "quantity": str(qty_int)},
        ]
        if csrf:
            for p in payload_variants:
                p["CSRFToken"] = csrf
                p["_csrf"] = csrf
        endpoints = [
            "/cart/add",
            f"{self._cfg.storefront_prefix}/cart/add",
            "/cart/addGrid",
        ]
        extra_headers: dict[str, str] = {
            "Referer": self._cfg.cart_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf:
            extra_headers["X-CSRF-Token"] = csrf
        excerpt = ""
        for endpoint in endpoints:
            for payload in payload_variants:
                try:
                    resp = await self._request(
                        "POST", endpoint, data=payload,
                        extra_headers=extra_headers,
                    )
                    excerpt = resp.text[:400]
                except httpx.HTTPError as exc:
                    excerpt = str(exc)
                    continue
                if resp.status_code >= 400:
                    continue
                # Detect silent failures: Hybris returns JSON with error codes
                # even on HTTP 200 when CSRF is wrong or user is not logged in.
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct:
                    try:
                        body = resp.json()
                        if isinstance(body, dict):
                            # Success: SAP Commerce returns quantityAdded or entry
                            if body.get("quantityAdded") or body.get("entry"):
                                return CartMutationResult(
                                    success=True, store_id=STORE_ID,
                                    product_id=product_id, quantity=quantity,
                                    message="Item added to Shufersal cart.",
                                )
                            # Any explicit error code → real failure
                            err = body.get("errorCode") or body.get("statusCode") or body.get("reason")
                            if err:
                                excerpt = f"API error: {err}"
                                continue
                    except Exception:
                        pass
                # HTML / unknown response: treat as success only if no login/error signal
                text_lower = resp.text.lower()
                if "login" not in text_lower and "error" not in text_lower:
                    return CartMutationResult(
                        success=True, store_id=STORE_ID,
                        product_id=product_id, quantity=quantity,
                        message="Item added to Shufersal cart.",
                    )
        return CartMutationResult(
            success=False, store_id=STORE_ID,
            product_id=product_id, quantity=quantity,
            message=f"Could not add item to Shufersal cart. Last response: {excerpt[:200]}",
        )
