"""Application configuration — loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Per-store config objects (plain dataclasses — no Pydantic overhead at load)
# ---------------------------------------------------------------------------


class ShufersalConfig:
    base_url: str
    storefront_prefix: str
    locale: str
    currency: str
    request_timeout: float
    login_timeout: float
    max_results: int
    debug_http: bool
    browser_channel: str | None

    def __init__(self) -> None:
        self.base_url = os.getenv("SHUFERSAL_BASE_URL", "https://www.shufersal.co.il")
        self.storefront_prefix = os.getenv("SHUFERSAL_STOREFRONT_PREFIX", "/online/he")
        self.locale = os.getenv("SHUFERSAL_LOCALE", "he")
        self.currency = os.getenv("SHUFERSAL_CURRENCY", "ILS")
        self.request_timeout = float(os.getenv("SHUFERSAL_REQUEST_TIMEOUT_SECONDS", "20"))
        self.login_timeout = float(os.getenv("SHUFERSAL_LOGIN_TIMEOUT_SECONDS", "600"))
        self.max_results = int(os.getenv("SHUFERSAL_MAX_SEARCH_RESULTS", "8"))
        self.debug_http = os.getenv("SHUFERSAL_DEBUG_HTTP", "false").lower() == "true"
        self.browser_channel = os.getenv("SHUFERSAL_BROWSER_CHANNEL", "chrome") or None

    @property
    def login_url(self) -> str:
        return f"{self.base_url}{self.storefront_prefix}/login"

    @property
    def cart_url(self) -> str:
        return f"{self.base_url}{self.storefront_prefix}/cart/cartsummary"


class TivTaamConfig:
    base_url: str
    retailer_id: int
    branch_id: int
    request_timeout: float
    login_timeout: float
    max_results: int
    debug_http: bool

    def __init__(self) -> None:
        self.base_url = os.getenv("TIVTAAM_BASE_URL", "https://www.tivtaam.co.il")
        self.retailer_id = int(os.getenv("TIVTAAM_RETAILER_ID", "1062"))
        self.branch_id = int(os.getenv("TIVTAAM_BRANCH_ID", "943"))
        self.request_timeout = float(os.getenv("TIVTAAM_REQUEST_TIMEOUT_SECONDS", "20"))
        self.login_timeout = float(os.getenv("TIVTAAM_LOGIN_TIMEOUT_SECONDS", "120"))
        self.max_results = int(os.getenv("TIVTAAM_MAX_SEARCH_RESULTS", "8"))
        self.debug_http = os.getenv("TIVTAAM_DEBUG_HTTP", "false").lower() == "true"
        # Auto-login credentials (optional)
        self.email = os.getenv("TIVTAAM_EMAIL", "")
        self.password = os.getenv("TIVTAAM_PASSWORD", "")

    @property
    def products_url(self) -> str:
        return f"{self.base_url}/v2/retailers/{self.retailer_id}/branches/{self.branch_id}/products"

    @property
    def autocomplete_url(self) -> str:
        return f"{self.base_url}/v2/retailers/{self.retailer_id}/branches/{self.branch_id}/products/autocomplete"

    @property
    def sessions_url(self) -> str:
        return f"{self.base_url}/retailers/{self.retailer_id}/sessions"

    @property
    def orders_url(self) -> str:
        return f"{self.base_url}/v2/retailers/{self.retailer_id}/branches/{self.branch_id}/orders"

    @property
    def carts_url(self) -> str:
        return f"{self.base_url}/v2/retailers/{self.retailer_id}/branches/{self.branch_id}/carts"


# ---------------------------------------------------------------------------
# Top-level app settings
# ---------------------------------------------------------------------------


class AppSettings:
    state_dir: Path
    debug_dir: Path
    browser_dir: Path
    max_search_results: int
    shufersal: ShufersalConfig
    tivtaam: TivTaamConfig

    def __init__(self) -> None:
        self.state_dir = Path(os.getenv("ISRAELGROCERY_STATE_DIR", ".local/state"))
        self.debug_dir = Path(os.getenv("ISRAELGROCERY_DEBUG_DIR", ".local/debug"))
        self.browser_dir = Path(os.getenv("ISRAELGROCERY_BROWSER_DIR", ".local/browser"))
        self.max_search_results = int(os.getenv("ISRAELGROCERY_MAX_SEARCH_RESULTS", "8"))
        self.shufersal = ShufersalConfig()
        self.tivtaam = TivTaamConfig()

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.browser_dir.mkdir(parents=True, exist_ok=True)
