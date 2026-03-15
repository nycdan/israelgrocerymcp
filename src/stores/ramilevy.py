"""Rami Levy grocery store adapter."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from auth.session_store import MultiStoreSessionStore
from config import RamiLevyConfig
from models import CartLine, CartMutationResult, CartView, StoreProduct
from stores.base import BaseStore

STORE_ID = "ramilevi"
STORE_NAME = "Rami Levy"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.rami-levy.co.il",
    "Referer": "https://www.rami-levy.co.il/",
    "Content-Type": "application/json;charset=UTF-8",
    "locale": "he",
}

# JavaScript that extracts the logged-in user's auth token from the Rami Levy browser.
# Strategy (ordered by priority):
#   1. Rami Levy Vuex store: localStorage['ramilevy'].authuser.user.token  (most reliable)
#   2. Generic JWT scan of localStorage / sessionStorage
#   3. Cookies
# A valid user token must have exactly 3 dot-separated parts (header.payload.signature).
_LS_TOKEN_JS = """
() => {
    function isValidJWT(tok) {
        if (!tok || typeof tok !== 'string') return false;
        const parts = tok.split('.');
        if (parts.length !== 3) return false;
        return parts.every(p => p.length > 4);
    }

    // 1. Rami Levy Vuex / Pinia store key (highest priority — most specific)
    const rlRaw = localStorage.getItem('ramilevy');
    if (rlRaw) {
        try {
            const rl = JSON.parse(rlRaw);
            // authuser.user.token is where the logged-in user's JWT lives
            const tok = rl?.authuser?.user?.token || rl?.authuser?.user?.api_token
                     || rl?.user?.token || rl?.token;
            if (isValidJWT(tok))
                return {key: 'ramilevy.authuser.user.token', token: tok, source: 'localStorage'};
        } catch (_) {}
    }

    // 2. Generic scan — look for any stored value that is a valid 3-part JWT
    const TOKEN_KEYS = ['ecomtoken', 'token', 'access_token', 'auth_token', 'user_token'];
    for (const store of [localStorage, sessionStorage]) {
        const src = store === localStorage ? 'localStorage' : 'sessionStorage';
        for (const k of TOKEN_KEYS) {
            const v = store.getItem(k);
            if (isValidJWT(v)) return {key: k, token: v, source: src};
        }
        for (let i = 0; i < store.length; i++) {
            const k = store.key(i);
            const v = store.getItem(k);
            if (!v) continue;
            if (isValidJWT(v)) return {key: k, token: v, source: src};
            try {
                const obj = JSON.parse(v);
                if (obj && typeof obj === 'object') {
                    for (const tk of TOKEN_KEYS) {
                        if (isValidJWT(obj[tk]))
                            return {key: k + '.' + tk, token: obj[tk], source: src};
                    }
                }
            } catch (_) {}
        }
    }

    // 3. Cookies
    for (const c of document.cookie.split(';')) {
        const [name, ...rest] = c.trim().split('=');
        const val = rest.join('=');
        if (TOKEN_KEYS.includes(name.trim()) && isValidJWT(val))
            return {key: name.trim(), token: val, source: 'cookie'};
    }

    return null;
}
"""

# Debug JS: show full ramilevy Vuex state so we can trace where the token is stored.
_RL_STATE_DEBUG_JS = """
() => {
    const raw = localStorage.getItem('ramilevy');
    if (!raw) return {error: 'ramilevy key not found in localStorage'};
    try {
        const obj = JSON.parse(raw);
        const authuser = obj?.authuser || {};
        const user = authuser?.user;
        return {
            hasUser: !!user,
            userKeys: user ? Object.keys(user) : [],
            userIdSnippet: user?.id ? String(user.id).substring(0, 10) : null,
            tokenPresent: !!(user?.token || user?.api_token),
            tokenLength: (user?.token || user?.api_token || '').length,
            tokenDots: (user?.token || user?.api_token || '').split('.').length - 1,
        };
    } catch(e) { return {error: String(e)}; }
}
"""

# Dump all storage keys for debugging when token detection fails
_LS_DEBUG_JS = """
() => {
    const result = {localStorage: {}, sessionStorage: {}, cookies: []};
    for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        const v = localStorage.getItem(k);
        result.localStorage[k] = v ? v.substring(0, 80) + (v.length > 80 ? '...' : '') : null;
    }
    for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        const v = sessionStorage.getItem(k);
        result.sessionStorage[k] = v ? v.substring(0, 80) + (v.length > 80 ? '...' : '') : null;
    }
    result.cookies = document.cookie.split(';').map(c => c.trim().split('=')[0]).filter(Boolean);
    return result;
}
"""


def _slug_to_name(slug: str) -> str:
    """Convert a URL slug like 'ביצים-יח-l' to a readable name 'ביצים יח L'."""
    return re.sub(r"-+", " ", slug).strip().title() if slug else ""


class RamiLevyStore(BaseStore):
    store_id = STORE_ID
    store_name = STORE_NAME

    def __init__(self, cfg: RamiLevyConfig, session_store: MultiStoreSessionStore) -> None:
        self._cfg = cfg
        self._ss = session_store

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        headers = dict(_BASE_HEADERS)
        session = self._ss.load_session(STORE_ID)
        if session and session.get("token"):
            # Rami Levy uses a custom `ecomtoken` header (not Authorization: Bearer)
            headers["ecomtoken"] = session["token"]
        return headers

    def _build_client(self, auth: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._auth_headers() if auth else _BASE_HEADERS,
            timeout=self._cfg.request_timeout,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, email: str) -> str:
        """
        Step 1 of Rami Levy login — email only, no password.

        Rami Levy authenticates via email + SMS OTP (no password).
        This sends the OTP SMS. Follow up with verify_ramilevi_otp(otp_code).
        """
        async with httpx.AsyncClient(
            headers=_BASE_HEADERS, timeout=self._cfg.request_timeout, follow_redirects=True
        ) as client:
            try:
                resp = await client.post(
                    self._cfg.login_url,
                    json={
                        "username": email,
                        "password": None,
                        "restore_account": False,
                        "recaptcha": None,
                        "phone": "",
                        "delivery_method": "sms",
                        "id_delivery_times": None,
                    },
                )
            except Exception as exc:
                return f"Login request failed: {exc}"

        if resp.status_code not in (200, 201):
            return f"Login failed: HTTP {resp.status_code} — {resp.text[:300]}"

        data = resp.json()

        # OTP SMS dispatched
        if data.get("otp_required") or data.get("status") == 1:
            last = data.get("phone_last_digits", "****")
            session = self._ss.load_session(STORE_ID) or {}
            session["pending_email"] = email
            self._ss.save_session(STORE_ID, session)
            return (
                f"SMS sent to phone ending in {last}. "
                f"Call verify_ramilevi_otp(otp_code) with the 6-digit code you received."
            )

        # Logged in without OTP (rare but possible)
        return self._save_login_response(data)

    async def verify_otp(self, otp_code: str) -> str:
        """Step 2: submit the SMS OTP to complete Rami Levy login."""
        session = self._ss.load_session(STORE_ID) or {}
        email = session.get("pending_email", "")
        if not email:
            return "No pending login found. Call login_ramilevi(email) first."

        async with httpx.AsyncClient(
            headers=_BASE_HEADERS, timeout=self._cfg.request_timeout, follow_redirects=True
        ) as client:
            try:
                resp = await client.post(
                    self._cfg.login_url,
                    json={
                        "username": email,
                        "password": None,
                        "restore_account": False,
                        "recaptcha": None,
                        "phone": None,
                        "delivery_method": "sms",
                        "id_delivery_times": None,
                        "otp_code": otp_code,  # correct field name from HAR
                    },
                )
            except Exception as exc:
                return f"OTP verification failed: {exc}"

        if resp.status_code not in (200, 201):
            return f"OTP verification failed: HTTP {resp.status_code} — {resp.text[:300]}"

        data = resp.json()
        if data.get("otp_required") or data.get("status") == 1:
            return "OTP was incorrect or expired. Try again with verify_ramilevi_otp(otp_code)."

        return self._save_login_response(data)

    def _save_login_response(self, data: dict) -> str:
        user = data.get("user") or data
        token = user.get("token", "")
        user_id = user.get("id") or user.get("user_id") or ""
        email = user.get("email", "")
        if not token:
            return f"Login failed — no token in response: {str(data)[:200]}"
        session = self._ss.load_session(STORE_ID) or {}
        session.update({
            "token": token,
            "user_id": str(user_id),
            "email": email,
            "items": session.get("items", {}),
            "cart_store_id": self._cfg.cart_store_id,
        })
        session.pop("pending_email", None)
        self._ss.save_session(STORE_ID, session)
        return f"Logged in to Rami Levy as {email} (user_id={user_id})."

    async def browser_login(self, browser_dir: str, timeout_ms: int = 300_000) -> str:
        """
        Open a visible browser to Rami Levy, wait for the user to complete login,
        automatically capture the token, then close the browser.
        Single-step — no follow-up call needed.

        Detection strategy (first match wins):
        1. Intercept outgoing API requests for ecomtoken / Authorization headers
        2. Scan localStorage / sessionStorage / cookies
        3. Check DOM for logged-in indicators (user name in nav)
        """
        import sys
        from pathlib import Path as _P

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return (
                "Playwright is not installed.\n"
                "From the israelgrocery folder run: uv run playwright install chromium"
            )

        _P(browser_dir).mkdir(parents=True, exist_ok=True)
        self._clear_profile_locks(browser_dir)

        login_url = f"{self._cfg.base_url}/he"
        captured_token: dict[str, str] = {}   # mutable so the listener can write to it
        captured_store: dict[str, int] = {}   # store ID extracted from cart requests

        # Authenticated API paths — the user token will only appear in requests to these.
        # Anonymous/public catalog requests use a separate client credential, not a user JWT.
        _AUTH_PATHS = ("/api/v2/cart", "/api/v2/user", "/api/v2/orders",
                       "/api/v2/profile", "/api/v2/auth", "/api/profile")

        def _is_valid_jwt(tok: str) -> bool:
            """A valid JWT has exactly three dot-separated base64url parts."""
            parts = tok.split(".")
            return len(parts) == 3 and all(len(p) > 4 for p in parts)

        def _on_request(request):
            """Intercept outgoing requests and grab user auth token.

            Only captures tokens from authenticated endpoints to avoid
            mistaking the site's anonymous client-API-key for a user JWT.
            """
            if captured_token:
                return
            url = request.url
            # Only look at requests to authenticated endpoints
            if not any(path in url for path in _AUTH_PATHS):
                return
            headers = request.headers
            for hdr in ("ecomtoken", "authorization"):
                val = headers.get(hdr, "")
                if not val:
                    continue
                tok = val.replace("Bearer ", "") if hdr == "authorization" else val
                if not _is_valid_jwt(tok):
                    print(
                        f"[israelgrocery] Skipping non-JWT from '{hdr}' on {url[:80]} "
                        f"(len={len(tok)}, dots={tok.count('.')})",
                        file=sys.stderr,
                    )
                    continue
                captured_token["token"] = tok
                captured_token["source"] = f"request header '{hdr}'"
                print(
                    f"[israelgrocery] ✅ User token captured from '{hdr}' on {url[:80]}",
                    file=sys.stderr,
                )
                # Also try to capture the store ID from the cart request body
                if "/api/v2/cart" in url and not captured_store:
                    try:
                        import json as _json
                        body = request.post_data
                        if body:
                            body_obj = _json.loads(body)
                            sid = body_obj.get("store")
                            if sid and isinstance(sid, int):
                                captured_store["store_id"] = sid
                                print(
                                    f"[israelgrocery] ✅ Cart store_id captured: {sid}",
                                    file=sys.stderr,
                                )
                    except Exception:
                        pass

        token = None
        debug_msg = ""

        try:
            async with async_playwright() as pw:
                ctx = await pw.chromium.launch_persistent_context(
                    browser_dir,
                    headless=False,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                    # Clear any stale / corrupted tokens from localStorage before navigating.
                    # This prevents the old bad token from being re-captured immediately.
                    try:
                        await page.goto("about:blank")
                        await page.evaluate("""
                            () => {
                                const TOKEN_KEYS = ['ecomtoken', 'token', 'access_token', 'auth_token', 'user_token'];
                                for (const k of TOKEN_KEYS) { localStorage.removeItem(k); sessionStorage.removeItem(k); }
                            }
                        """)
                    except Exception:
                        pass  # safe to ignore — page may not support localStorage yet

                    # Listen for outgoing requests to sniff auth headers
                    page.on("request", _on_request)

                    await page.goto(login_url, wait_until="domcontentloaded")

                    # Wait for the page to settle and make its initial API calls
                    await page.wait_for_timeout(4000)

                    # Check network-intercepted token first (must be a valid 3-part JWT)
                    cand = captured_token.get("token", "")
                    cand_parts = cand.split(".")
                    if cand and len(cand_parts) == 3 and all(len(p) > 4 for p in cand_parts):
                        token = cand
                        self._save_browser_token(token, cart_store_id=captured_store.get("store_id"))
                        return (
                            f"✅ Rami Levy: already logged in (token from {captured_token.get('source')}). "
                            "Session saved."
                        )

                    # Fallback: check storage (also validate JWT structure)
                    try:
                        result = await page.evaluate(_LS_TOKEN_JS)
                        if result and result.get("token"):
                            cand2 = result["token"]
                            cand2_parts = cand2.split(".")
                            if len(cand2_parts) == 3 and all(len(p) > 4 for p in cand2_parts):
                                token = cand2
                                self._save_browser_token(token, cart_store_id=captured_store.get("store_id"))
                                return (
                                    f"✅ Rami Levy: already logged in (token from {result.get('source')} "
                                    f"key={result.get('key')}). Session saved."
                                )
                    except Exception:
                        pass

                    # Not logged in yet — poll until user completes login
                    print(
                        "[israelgrocery] Rami Levy browser opened — waiting for login...",
                        file=sys.stderr,
                    )
                    poll_interval = 2000  # 2 seconds
                    elapsed = 0

                    while elapsed < timeout_ms:
                        await page.wait_for_timeout(poll_interval)
                        elapsed += poll_interval

                        # Check network-intercepted token
                        if captured_token.get("token"):
                            token = captured_token["token"]
                            break

                        # Check storage
                        try:
                            result = await page.evaluate(_LS_TOKEN_JS)
                            if result and result.get("token"):
                                candidate = result["token"]
                                # Only accept well-formed JWTs
                                parts = candidate.split(".")
                                if len(parts) == 3 and all(len(p) > 4 for p in parts):
                                    token = candidate
                                    print(
                                        f"[israelgrocery] Token found in {result.get('source')}"
                                        f" key={result.get('key')}",
                                        file=sys.stderr,
                                    )
                                    break
                                else:
                                    print(
                                        f"[israelgrocery] Skipping malformed storage token "
                                        f"(len={len(candidate)}, dots={candidate.count('.')})",
                                        file=sys.stderr,
                                    )
                        except Exception:
                            # Page navigating or closed — safe to retry
                            pass

                    # If still nothing, dump Rami Levy specific state for debugging
                    if not token:
                        try:
                            rl_state = await page.evaluate(_RL_STATE_DEBUG_JS)
                            debug_msg = (
                                f"Rami Levy Vuex state: {rl_state}\n"
                                "Tip: log in on the browser that just opened, "
                                "then the token will be captured automatically."
                            )
                        except Exception:
                            debug_msg = "(could not read storage — browser may have been closed)"

                finally:
                    try:
                        await ctx.close()
                    except Exception:
                        pass  # browser may already be closed by user/Claude

        except Exception as exc:
            # If we captured a token via network before the crash, use it
            if captured_token.get("token"):
                self._save_browser_token(captured_token["token"], cart_store_id=captured_store.get("store_id"))
                return (
                    f"✅ Logged in to Rami Levy (token from {captured_token.get('source')}). "
                    "Browser closed. Session saved."
                )
            return f"Browser login failed: {exc}"

        if not token:
            return (
                "Login timed out — no token found after "
                f"{timeout_ms // 1000} seconds.\n\n"
                f"Debug — browser storage contained:\n{debug_msg}\n\n"
                "Please share the above so we can fix token detection."
            )

        self._save_browser_token(token, cart_store_id=captured_store.get("store_id"))
        return "✅ Logged in to Rami Levy! Session token captured automatically."

    def _save_browser_token(self, token: str, cart_store_id: Optional[int] = None) -> None:
        """Persist a token (and optionally the correct cart store ID) into the session store."""
        session = self._ss.load_session(STORE_ID) or {}
        session.update({
            "token": token,
            "items": session.get("items", {}),
            # Prefer the captured store ID (from the browser's actual cart call) over the default
            "cart_store_id": cart_store_id or session.get("cart_store_id") or self._cfg.cart_store_id,
        })
        session.pop("pending_email", None)
        self._ss.save_session(STORE_ID, session)

    @staticmethod
    def _clear_profile_locks(browser_dir: str) -> None:
        """Remove Chromium singleton lock files left by crashed sessions."""
        from pathlib import Path as _P
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
            try:
                (_P(browser_dir) / name).unlink(missing_ok=True)
            except Exception:
                pass

    async def check_login_status(self) -> bool:
        session = self._ss.load_session(STORE_ID)
        if not session or not session.get("token"):
            self._ss.mark_validation(STORE_ID, False, "No token")
            return False
        # Validate token with a lightweight cart call (empty items)
        try:
            async with self._build_client(auth=True) as client:
                resp = await client.post(
                    self._cfg.cart_url,
                    json={
                        "store": self._cfg.cart_store_id,
                        "isClub": 0,
                        "supplyAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "items": {},
                        "meta": None,
                    },
                )
                ok = resp.status_code in (200, 201)
                self._ss.mark_validation(STORE_ID, ok, f"Cart API HTTP {resp.status_code}")
                return ok
        except Exception as exc:
            self._ss.mark_validation(STORE_ID, False, f"Validation failed: {exc}")
            return False

    def is_logged_in_cached(self) -> bool:
        session = self._ss.load_session(STORE_ID)
        return bool(session and session.get("token"))

    def login_hint(self) -> str:
        return (
            "Run login_ramilevi_browser() — no email needed. Opens a browser; "
            "log in and the token is captured automatically."
        )

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def search(self, query: str, max_results: int = 8) -> list[StoreProduct]:
        # Search is a public endpoint — no auth needed
        async with self._build_client(auth=False) as client:
            try:
                resp = await client.post(
                    self._cfg.catalog_url,
                    json={"q": query, "aggs": 1, "store": self._cfg.store_id},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
                items = data.get("data") or []
                products = []
                for item in items[:max_results]:
                    try:
                        products.append(self._parse_product(item))
                    except Exception:
                        pass
                return products
            except Exception:
                return []

    async def raw_search(self, query: str) -> dict:
        async with self._build_client(auth=False) as client:
            try:
                resp = await client.post(
                    self._cfg.catalog_url,
                    json={"q": query, "aggs": 1, "store": self._cfg.store_id},
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
                    "sample": {k: (v[:2] if isinstance(v, list) else v)
                               for k, v in list(body.items())[:6]} if isinstance(body, dict) else body,
                }
            except Exception as exc:
                return {"store": STORE_ID, "error": str(exc)}

    def _parse_product(self, item: dict) -> StoreProduct:
        prop = item.get("prop") or {}
        price_data = item.get("price") or {}
        price_raw = price_data.get("price")
        price = float(price_raw) if price_raw is not None else None

        # Sale price sometimes in price.sale_price
        sale_raw = price_data.get("sale_price")
        sale_price = float(sale_raw) if sale_raw else None

        # Weighable: by_kilo=1 means sold per kg
        is_weighable = bool(prop.get("by_kilo") == 1)
        # multiplication = minimum cart increment (0.5 = 500g minimum)
        unit_resolution = float(item.get("multiplication") or 0)

        # Stock: status=2 is available; any other value is out of stock
        in_stock = prop.get("status") == 2

        # Product name — API returns it directly in the `name` field
        name = item.get("name") or ""
        if not name:
            # Fallback: convert slug or use group name
            slug = item.get("slug") or ""
            group_name = (item.get("group") or {}).get("name") or ""
            name = _slug_to_name(slug) if slug else group_name

        # Category from department
        dept = item.get("department") or {}
        category = dept.get("name") or (item.get("group") or {}).get("name") or ""

        # Brand: brand field is an integer ID in catalog, no name available
        brand = str(item.get("brand") or "")

        # Image URL
        images = item.get("images") or {}
        image_url = images.get("small") or images.get("original") or ""
        if image_url and image_url.startswith("/"):
            image_url = self._cfg.base_url + image_url

        return StoreProduct(
            store_id=STORE_ID,
            product_id=str(item.get("id") or ""),
            name=str(name),
            price=price,
            sale_price=sale_price,
            is_on_sale=bool(sale_price and price and sale_price < price),
            brand=brand,
            category=str(category),
            in_stock=in_stock,
            image_url=image_url,
            is_weighable=is_weighable,
            unit_resolution=unit_resolution,
        )

    # ------------------------------------------------------------------
    # Cart
    # ------------------------------------------------------------------

    def _load_cart_items(self) -> dict[str, float]:
        session = self._ss.load_session(STORE_ID) or {}
        raw = session.get("items") or {}
        return {str(k): float(v) for k, v in raw.items()}

    def _save_cart_items(self, items: dict[str, float]) -> None:
        session = self._ss.load_session(STORE_ID) or {}
        session["items"] = {k: str(round(v, 4)) for k, v in items.items()}
        self._ss.save_session(STORE_ID, session)

    def _cart_store_id(self) -> int:
        session = self._ss.load_session(STORE_ID) or {}
        return int(session.get("cart_store_id") or self._cfg.cart_store_id)

    async def get_cart(self) -> CartView:
        """Return cart contents.  If logged in, fetches live data from the cart API
        so product names and prices are shown.  Falls back to the local cache if
        the API call fails or the user is not logged in."""
        items = self._load_cart_items()
        if not items:
            return CartView(store_id=STORE_ID, lines=[], total=0.0, item_count=0)

        session = self._ss.load_session(STORE_ID)
        if session and session.get("token"):
            # Try a live cart refresh to get real names/prices
            cart_store = self._cart_store_id()
            payload = {
                "store": cart_store,
                "isClub": 0,
                "supplyAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "items": {pid: f"{qty:.2f}" for pid, qty in items.items()},
                "meta": None,
            }
            try:
                async with self._build_client(auth=True) as client:
                    resp = await client.post(self._cfg.cart_url, json=payload)
                if resp.status_code in (200, 201):
                    return self._parse_cart_response(resp.json())
            except Exception:
                pass  # fall through to local cache

        # Fallback: return local cache without prices
        lines = [CartLine(product_id=pid, product_name=f"Product {pid}", quantity=qty)
                 for pid, qty in items.items()]
        return CartView(store_id=STORE_ID, lines=lines, total=0.0, item_count=len(lines))

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
                message="Not logged in to Rami Levy. Use login_ramilevi_browser() to open a login window.",
            )

        # Build the full items dict (all items in cart + new addition).
        # Rami Levy cart is idempotent/full-replace — send ALL items every time.
        items = self._load_cart_items()
        current_qty = items.get(product_id, 0.0)
        items[product_id] = round(current_qty + quantity, 4)

        cart_store = self._cart_store_id()

        # Items format: {"product_id_str": "quantity_str"} per HAR analysis
        payload = {
            "store": cart_store,
            "isClub": 0,
            "supplyAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "items": {pid: f"{qty:.2f}" for pid, qty in items.items()},
            "meta": None,
        }

        async with self._build_client(auth=True) as client:
            try:
                resp = await client.post(self._cfg.cart_url, json=payload)
            except Exception as exc:
                return CartMutationResult(
                    success=False, store_id=STORE_ID, product_id=product_id, quantity=quantity,
                    message=f"Cart request failed: {type(exc).__name__}: {exc!r}",
                )

        # Include response body in error for debugging
        if resp.status_code not in (200, 201):
            try:
                body = resp.text[:300]
            except Exception:
                body = "(unreadable)"
            return CartMutationResult(
                success=False, store_id=STORE_ID, product_id=product_id, quantity=quantity,
                message=(
                    f"Rami Levy cart API returned HTTP {resp.status_code}.\n"
                    f"URL: {self._cfg.cart_url}\n"
                    f"Response: {body}"
                ),
            )

        self._save_cart_items(items)
        cart = self._parse_cart_response(resp.json())
        qty_label = f"{quantity} kg" if sold_by == "weight" or quantity % 1 != 0 else f"×{int(quantity)}"
        return CartMutationResult(
            success=True, store_id=STORE_ID, product_id=product_id, quantity=quantity,
            message=f"Added {qty_label} to Rami Levy cart ({len(items)} items total).",
            cart=cart,
        )

    def _parse_cart_response(self, data: Any) -> CartView:
        if not isinstance(data, dict):
            return CartView(store_id=STORE_ID)
        raw_items = data.get("items") or []
        lines = []
        total = 0.0
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            # Skip the delivery fee line
            if raw.get("is_delivery"):
                total += float(raw.get("FormatedTotalPrice") or 0)
                continue
            pid = str(raw.get("id") or "")
            name = raw.get("name") or f"Product {pid}"
            price = float(raw.get("price") or 0)
            qty = float(raw.get("quantity") or 0)
            line_total = float(raw.get("FormatedTotalPrice") or (price * qty))
            total += line_total
            lines.append(CartLine(
                product_id=pid,
                product_name=name,
                quantity=qty,
                price=price,
                total=line_total,
            ))
        return CartView(
            store_id=STORE_ID,
            lines=lines,
            total=total,
            item_count=len(lines),
        )
