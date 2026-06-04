from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "option_config.json")


def load_option_config() -> dict[str, Any]:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class OptionCacheEntry:
    value: Any
    stored_at: float


class OptionTTLCache:
    def __init__(self, ttl_seconds: int = 30) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, OptionCacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._items.get(key)
        if entry is None:
            return None
        if time.time() - entry.stored_at > self.ttl_seconds:
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        self._items[key] = OptionCacheEntry(value=value, stored_at=time.time())

    def is_stale(self, key: str, max_age_seconds: int) -> bool:
        entry = self._items.get(key)
        if entry is None:
            return True
        return time.time() - entry.stored_at > max_age_seconds
