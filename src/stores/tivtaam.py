"""Tiv Taam grocery store adapter."""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from auth.session_store import MultiStoreSessionStore
from config import TivTaamConfig
from models import CartLine, CartMutationResult, CartView, StoreProduct
from stores.base import BaseStore

STORE_ID = "tivtaam"
STORE_NAME = "Tiv Taam"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.tivtaam.co.il",
    "Referer": "https://www.tivtaam.co.il/",
    # Required by the Tiv Taam API — without this the products endpoint returns 403
    "X-Requested-With": "XMLHttpRequest",
}

# The products search endpoint requires a `filters` param (Elasticsearch-style).
# Omitting it returns 403. An empty filter object unlocks all results; `q` drives ranking.
_EMPTY_FILTERS = json.dumps({"must": {}, "mustNot": {}})


class TivTaamStore(BaseStore):
    store_id = STORE_ID
    store_name = STORE_NAME

    def __init__(self, cfg: TivTaamConfig, session_store: MultiStoreSessionStore) -> None:
        self._cfg = cfg
        self._ss = session_store
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> httpx.AsyncClient:
        headers = dict(_BROWSER_HEADERS)
        session = self._ss.load_session(STORE_ID)
        if session and session.get("token"):
            headers["Authorization"] = f"Bearer {session['token']}"
        return httpx.AsyncClient(
            headers=headers,
            timeout=self._cfg.request_timeout,
            follow_redirects=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _invalidate_client(self) -> None:
        self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        """Authenticate with Tiv Taam. Returns a status message."""
        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS,
            timeout=self._cfg.request_timeout,
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.post(
                    self._cfg.sessions_url,
                    json={"username": email, "password": password},
                )
            except Exception as exc:
                return f"Login failed: {exc}"

        if resp.status_code not in (200, 201):
            return f"Login failed: HTTP {resp.status_code} — {resp.text[:200]}"

        data = resp.json()
        token = data.get("token") or data.get("access_token", "")
        user_obj = data.get("user") or {}
        user_id = data.get("userId") or data.get("user_id") or user_obj.get("id")
        first_name = user_obj.get("firstName", "")
        last_name = user_obj.get("lastName", "")

        if not (token and user_id):
            return f"Login succeeded but missing token/userId. Response keys: {list(data.keys())}"

        self._ss.save_session(STORE_ID, {
            "user_id": int(user_id),
            "token": token,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "cart_id": None,
            "authenticated": True,
        })
        self._invalidate_client()
        name = f"{first_name} {last_name}".strip() or email
        return f"✅ Logged in to Tiv Taam as {name}."

    async def check_login_status(self) -> bool:
        session = self._ss.load_session(STORE_ID)
        if not session or not session.get("token"):
            return False
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._cfg.sessions_url}/session")
            ok = resp.status_code == 200
            self._ss.mark_validation(STORE_ID, ok, f"Session check returned HTTP {resp.status_code}")
            return ok
        except Exception:
            return False

    def is_logged_in_cached(self) -> bool:
        session = self._ss.load_session(STORE_ID)
        return bool(session and session.get("token"))

    def login_hint(self) -> str:
        return "Run login_tivtaam(email, password) with your Tiv Taam account credentials."

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def _search_params(self, query: str, max_results: int) -> dict:
        """Build product search params the Stor.ai API actually accepts.

        Key findings from reverse-engineering the frontend:
        - `query` drives full-text relevance (not `q`)
        - `languageId` must match the script of the query for best results
        - `isSearch=true` activates the search-ranking pipeline
        - `filters` (JSON) is required; without it the endpoint returns 403
        - `mustNot.term["branch.isOutOfStock"]` hides out-of-stock items
        """
        filters = json.dumps({
            "must": {},
            "mustNot": {"term": {"branch.isOutOfStock": True}},
        })
        return {
            "query": query,
            "from": "0",
            "size": str(max_results),
            "languageId": str(self._cfg.language_id),
            "isSearch": "true",
            "filters": filters,
        }

    async def search(self, query: str, max_results: int = 8) -> list[StoreProduct]:
        client = await self._get_client()
        try:
            resp = await client.get(self._cfg.products_url, params=self._search_params(query, max_results))
            if resp.status_code != 200:
                return []
            return self._parse_products(resp.json(), max_results)
        except Exception:
            return []

    async def raw_search(self, query: str) -> dict:
        client = await self._get_client()
        try:
            resp = await client.get(self._cfg.products_url, params=self._search_params(query, 3))
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:500]
            return {
                "store": STORE_ID,
                "status": resp.status_code,
                "url": str(resp.url)[:120],
                "auth_header": "Authorization" in resp.request.headers,
                "body_keys": list(body.keys()) if isinstance(body, dict) else f"type={type(body).__name__}",
                "sample": {k: (v[:2] if isinstance(v, list) else v)
                           for k, v in list(body.items())[:6]} if isinstance(body, dict) else body,
            }
        except Exception as exc:
            return {"store": STORE_ID, "error": str(exc)}

    def _parse_products(self, data: Any, max_results: int) -> list[StoreProduct]:
        items: list[Any] = []
        if isinstance(data, dict):
            items = data.get("products") or data.get("items") or data.get("data") or []
        elif isinstance(data, list):
            items = data

        products = []
        for item in items[:max_results]:
            try:
                products.append(self._parse_product(item))
            except Exception:
                pass
        return products

    def _parse_product(self, item: dict) -> StoreProduct:
        # Prices live in item.branch (per-branch pricing from the v2 API)
        branch = item.get("branch") or {}

        # Weighable (loose produce): isWeighable=True, price is stored per-gram in the API
        # so we multiply by 1000 to get the human-readable price-per-kg.
        is_weighable = bool(item.get("isWeighable", False))
        unit_resolution = float(item.get("unitResolution") or 0)

        # regularPrice for weighable items is already ₪/kg (the JS divides it by 1000
        # internally to get price-per-gram, so the raw value is the human-readable per-kg price).
        price_raw = branch.get("regularPrice") or item.get("price")
        price = float(price_raw) if price_raw else None

        # Sale price from branch specials array
        specials = branch.get("specials") or []
        sale_price: Optional[float] = None
        if specials and isinstance(specials[0], dict):
            sp = specials[0].get("price")
            sale_price = float(sp) if sp else None

        in_stock = not branch.get("isOutOfStock", False)

        # Names: {"1": {"short": "...", "long": "..."}, "2": {...}} where 1=Hebrew, 2=English
        name_data = item.get("names") or item.get("name") or {}
        if isinstance(name_data, dict):
            he = name_data.get("1") or {}
            en = name_data.get("2") or {}
            name = (
                (he.get("short") or he.get("long") if isinstance(he, dict) else str(he))
                or (en.get("short") or en.get("long") if isinstance(en, dict) else str(en))
                or ""
            )
        else:
            name = str(name_data)

        # Brand names: {"names": {"1": "תנובה", "2": "Tnuva"}}
        brand_data = item.get("brand") or {}
        if isinstance(brand_data, dict):
            brand_names = brand_data.get("names") or {}
            brand = brand_names.get("2") or brand_names.get("1") or brand_data.get("name", "")
        else:
            brand = str(brand_data) if brand_data else ""

        # Department / category
        dept = item.get("department") or {}
        if isinstance(dept, dict):
            dept_names = dept.get("names") or {}
            category = dept_names.get("2") or dept_names.get("1") or ""
        else:
            category = ""

        # Image
        image_obj = item.get("image") or {}
        image_url = image_obj.get("url", "") if isinstance(image_obj, dict) else ""

        # unitOfMeasure is a dict like {"id":5,"defaultName":"מ\"ל","names":{"1":"מ\"ל","2":"ml"}}
        uom = item.get("unitOfMeasure") or {}
        if isinstance(uom, dict):
            uom_names = uom.get("names") or {}
            weight_unit = (
                uom_names.get("2")  # English name preferred
                or uom_names.get("1")
                or uom.get("defaultName", "")
            )
        else:
            weight_unit = str(uom)

        return StoreProduct(
            store_id=STORE_ID,
            product_id=str(item.get("id") or item.get("retailerProductId") or 0),
            name=str(name),
            price=price,
            sale_price=sale_price,
            is_on_sale=bool(sale_price and price and sale_price < price),
            brand=str(brand),
            category=str(category),
            in_stock=in_stock,
            image_url=image_url,
            weight=item.get("weight"),
            weight_unit=weight_unit,
            is_weighable=is_weighable,
            unit_resolution=unit_resolution,
        )

    # ------------------------------------------------------------------
    # Cart
    # ------------------------------------------------------------------

    async def get_cart(self) -> CartView:
        client = await self._get_client()
        try:
            resp = await client.get(self._cfg.orders_url)
            if resp.status_code != 200:
                return CartView(store_id=STORE_ID)
            return self._parse_cart(resp.json())
        except Exception:
            return CartView(store_id=STORE_ID)

    async def _fetch_product_meta(self, product_id: str) -> dict:
        """Fetch a single product to read isWeighable and unitResolution."""
        client = await self._get_client()
        try:
            r = await client.get(
                f"{self._cfg.products_url}/{product_id}",
                params={"filters": _EMPTY_FILTERS},
            )
            if r.status_code == 200:
                return r.json() or {}
        except Exception:
            pass
        return {}

    async def add_to_cart(
        self,
        product_id: str,
        quantity: float = 1.0,
        sold_by: str = "unit",
    ) -> CartMutationResult:
        session = self._ss.load_session(STORE_ID)
        if not session or not session.get("token"):
            return CartMutationResult(
                success=False, store_id=STORE_ID, product_id=product_id, quantity=quantity,
                message="Not logged in to Tiv Taam. Use login_tivtaam(email, password).",
            )

        # Determine the correct soldBy and minimum quantity for this product.
        # Weighable items (loose produce) are sold by kg; the API stores price per-gram,
        # and the cart quantity must be in kg (e.g. 0.5 = 500 g).
        meta = await self._fetch_product_meta(product_id)
        is_weighable = bool(meta.get("isWeighable", False))
        unit_resolution = float(meta.get("unitResolution") or 0)

        if is_weighable:
            sold_by = "weight"
            # If caller passed a whole-unit count (1, 2, …) and the item is loose produce,
            # treat it as kg directly (1 → 1 kg). Clamp to the minimum resolution.
            min_qty = unit_resolution if unit_resolution > 0 else 0.1
            quantity = max(quantity, min_qty)

        current = await self.get_cart()
        cart_id = current.cart_id or (session.get("cart_id") or None)

        merged: list[dict] = []
        for line in current.lines:
            merged.append({
                "retailerProductId": int(line.product_id) if line.product_id.isdigit() else line.product_id,
                "quantity": line.quantity,
                "soldBy": "weight" if line.is_weighted else "unit",
                "type": 1,
                "isCase": False,
            })
        # Merge or append new item
        pid_int = int(product_id) if product_id.isdigit() else product_id
        for line in merged:
            if line["retailerProductId"] == pid_int:
                line["quantity"] += quantity
                break
        else:
            merged.append({
                "retailerProductId": pid_int,
                "quantity": quantity,
                "soldBy": sold_by,
                "type": 1,
                "isCase": False,
            })

        client = await self._get_client()
        try:
            if cart_id:
                resp = await client.patch(
                    f"{self._cfg.carts_url}/{cart_id}",
                    json={"lines": merged},
                )
                if resp.status_code not in (200, 201):
                    resp = await client.post(self._cfg.carts_url, json={"lines": merged})
            else:
                resp = await client.post(self._cfg.carts_url, json={"lines": merged})

            if resp.status_code not in (200, 201):
                return CartMutationResult(
                    success=False, store_id=STORE_ID, product_id=product_id, quantity=quantity,
                    message=f"Tiv Taam cart API returned HTTP {resp.status_code}.",
                )
            data = resp.json()
            new_cart_id = data.get("id") or data.get("cartId") or data.get("serverCartId")
            if new_cart_id:
                session["cart_id"] = str(new_cart_id)
                self._ss.save_session(STORE_ID, session)

            qty_label = f"{quantity} kg" if is_weighable else f"×{quantity}"
            return CartMutationResult(
                success=True, store_id=STORE_ID,
                product_id=product_id, quantity=quantity,
                message=f"Added {qty_label} to Tiv Taam cart (cart_id={new_cart_id}).",
                cart=self._parse_cart(data),
            )
        except Exception as exc:
            return CartMutationResult(
                success=False, store_id=STORE_ID, product_id=product_id, quantity=quantity,
                message=f"Cart error: {exc}",
            )

    def _parse_cart(self, data: Any) -> CartView:
        if isinstance(data, list):
            active = next((o for o in data if o.get("status") in ("open", "new", 1)), None)
            data = active or (data[0] if data else {})
        if not isinstance(data, dict):
            return CartView(store_id=STORE_ID)

        cart_id = (
            data.get("cartId") or data.get("serverCartId")
            or (str(data["id"]) if not data.get("status") and data.get("id") else None)
        )
        order_id = str(data.get("id")) if data.get("status") else None
        total = float(data.get("totalAmount") or data.get("total") or 0)
        subtotal = float(data.get("subTotal") or data.get("subtotal") or total)

        lines = []
        for raw in data.get("lines") or []:
            try:
                product = raw.get("product") or {}
                name_data = product.get("names") or product.get("name") or {}
                if isinstance(name_data, dict):
                    name = name_data.get("he") or name_data.get("en") or ""
                else:
                    name = str(name_data)
                price_data = raw.get("price") or product.get("price") or {}
                price = float(price_data.get("base", 0) if isinstance(price_data, dict) else (price_data or 0))
                pid = str(product.get("id") or raw.get("retailerProductId") or "")
                qty = float(raw.get("quantity", 1))
                lines.append(CartLine(
                    product_id=pid,
                    product_name=name,
                    quantity=qty,
                    price=price,
                    total=float(raw.get("totalPrice") or (price * qty)),
                    line_id=str(raw.get("id", "")),
                    is_weighted=product.get("soldBy") == "weight" or raw.get("soldBy") == "weight",
                ))
            except Exception:
                pass

        return CartView(
            store_id=STORE_ID,
            order_id=order_id,
            cart_id=cart_id,
            lines=lines,
            subtotal=subtotal,
            total=total,
            item_count=len(lines),
        )
