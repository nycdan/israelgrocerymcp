"""Multi-store session persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any, Optional

import httpx

from models import UserPreferences


class MultiStoreSessionStore:
    """Stores sessions for all grocery stores in per-store JSON files."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic JSON session (Tiv Taam style: token + metadata)
    # ------------------------------------------------------------------

    def save_session(self, store_id: str, data: dict[str, Any]) -> None:
        path = self._session_path(store_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def load_session(self, store_id: str) -> Optional[dict[str, Any]]:
        path = self._session_path(store_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def clear_session(self, store_id: str) -> None:
        path = self._session_path(store_id)
        if path.exists():
            path.unlink()

    def _session_path(self, store_id: str) -> Path:
        return self._dir / f"{store_id}_session.json"

    # ------------------------------------------------------------------
    # Playwright storage-state (Shufersal cookies)
    # ------------------------------------------------------------------

    def save_storage_state(self, store_id: str, state: dict[str, Any]) -> None:
        path = self._storage_state_path(store_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))
        # Also save lightweight metadata
        domains = sorted({
            c.get("domain", "") for c in state.get("cookies", []) if c.get("domain")
        })
        self.save_session(store_id, {
            "authenticated": bool(state.get("cookies")),
            "last_login_at": datetime.now(UTC).isoformat(),
            "cookie_count": len(state.get("cookies", [])),
            "saved_domains": domains,
        })

    def load_storage_state(self, store_id: str) -> dict[str, Any]:
        path = self._storage_state_path(store_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def build_cookie_jar(self, store_id: str) -> httpx.Cookies:
        cookies = httpx.Cookies()
        for cookie in self.load_storage_state(store_id).get("cookies", []):
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain")
            path = cookie.get("path", "/")
            if name and value and domain:
                cookies.set(name=name, value=value, domain=domain, path=path)
        return cookies

    def has_storage_state(self, store_id: str) -> bool:
        return self._storage_state_path(store_id).exists()

    def _storage_state_path(self, store_id: str) -> Path:
        return self._dir / f"{store_id}_storage_state.json"

    # ------------------------------------------------------------------
    # User preferences (shared across all stores)
    # ------------------------------------------------------------------

    def save_preferences(self, prefs: UserPreferences) -> None:
        path = self._dir / "preferences.json"
        path.write_text(prefs.model_dump_json(indent=2))

    def load_preferences(self) -> UserPreferences:
        path = self._dir / "preferences.json"
        if not path.exists():
            return UserPreferences()
        try:
            return UserPreferences.model_validate_json(path.read_text())
        except Exception:
            return UserPreferences()

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------

    def save_debug(self, store_id: str, filename: str, content: str, debug_dir: Path) -> None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{store_id}_{filename}").write_text(content)

    def mark_validation(self, store_id: str, authenticated: bool, note: str) -> None:
        session = self.load_session(store_id) or {}
        session.update({
            "authenticated": authenticated,
            "last_validated_at": datetime.now(UTC).isoformat(),
            "validation_note": note,
        })
        self.save_session(store_id, session)
