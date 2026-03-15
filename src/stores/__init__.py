"""Store registry — manages all configured grocery store adapters."""
from __future__ import annotations

from typing import Optional

from auth.session_store import MultiStoreSessionStore
from config import AppSettings
from stores.base import BaseStore
from stores.ramilevy import RamiLevyStore
from stores.shufersal import ShufersalStore
from stores.tivtaam import TivTaamStore


class StoreRegistry:
    def __init__(self) -> None:
        self._stores: dict[str, BaseStore] = {}

    def register(self, store: BaseStore) -> None:
        self._stores[store.store_id] = store

    def get(self, store_id: str) -> Optional[BaseStore]:
        return self._stores.get(store_id)

    def all(self) -> list[BaseStore]:
        return list(self._stores.values())

    def ids(self) -> list[str]:
        return list(self._stores.keys())

    def get_or_raise(self, store_id: str) -> BaseStore:
        store = self._stores.get(store_id)
        if store is None:
            available = ", ".join(self._stores.keys()) or "none"
            raise ValueError(f"Unknown store '{store_id}'. Available: {available}")
        return store


def build_registry(settings: AppSettings, session_store: MultiStoreSessionStore) -> StoreRegistry:
    """Create and populate a StoreRegistry with all supported stores."""
    registry = StoreRegistry()
    registry.register(ShufersalStore(settings.shufersal, session_store))
    registry.register(TivTaamStore(settings.tivtaam, session_store))
    registry.register(RamiLevyStore(settings.ramilevi, session_store))
    return registry
