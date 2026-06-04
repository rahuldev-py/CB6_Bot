from __future__ import annotations

import json
import time
from datetime import date
from typing import Any, Optional

import httpx

from utils.logger import logger
from nse_options.option_cache import OptionTTLCache, load_option_config


class SensibullClient:
    """Hardened Sensibull compute API wrapper. Analytics only, never execution."""

    BASE_URL = "https://oxide.sensibull.com/v1/compute"
    META_URL = "https://api.sensibull.com/v1/instrument_metadata/"

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or load_option_config()
        self.timeout_seconds = float(self.config.get("timeout_seconds", 8))
        self.max_retries = int(self.config.get("max_retries", 2))
        self.cache = OptionTTLCache(int(self.config.get("cache_seconds", 30)))
        self.headers = {
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.8",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        }

    def is_available(self) -> bool:
        if not self.config.get("enabled", False):
            return False
        try:
            return bool(self._get_json(f"{self.BASE_URL}/cache/underlying_instruments"))
        except Exception as exc:
            logger.warning(f"Sensibull unavailable: {exc}")
            return False

    def get_underlying_token(self, symbol: str) -> Optional[dict[str, Any]]:
        symbol = _normalize_underlying(symbol)
        data = self._get_json(f"{self.BASE_URL}/cache/underlying_instruments")
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return None
        for row in rows:
            if str(row.get("tradingsymbol", "")).upper() == symbol:
                return row
        return None

    def get_option_chain_with_greeks(
        self,
        symbol: str,
        expiry: date | str,
        strikes_around_atm: int = 5,
    ) -> tuple[list[dict[str, Any]], Optional[int], Optional[float]]:
        symbol = _normalize_underlying(symbol)
        expiry_s = expiry.isoformat() if isinstance(expiry, date) else str(expiry)
        token_data = self.get_underlying_token(symbol)
        if not token_data:
            return [], None, None

        token = token_data.get("instrument_token")
        if token is None:
            return [], None, None

        live = self._get_json(f"{self.BASE_URL}/cache/live_derivative_prices/{token}")
        expiry_data = _dig(live, ["data", "per_expiry_data", expiry_s], {})
        atm = _to_int(expiry_data.get("atm_strike"))
        future_price = _to_float(expiry_data.get("future_price"))
        options = expiry_data.get("options") or []
        if atm is None or not isinstance(options, list):
            return [], atm, future_price

        metadata = self._post_json(self.META_URL, {"underlyer_list": [symbol]})
        raw_mapping = _dig(metadata, ["derivatives", symbol], "{}")
        try:
            mapping = json.loads(raw_mapping) if isinstance(raw_mapping, str) else raw_mapping
        except Exception:
            mapping = {}
        option_map = _dig(mapping, ["derivatives", expiry_s, "options"], {})
        if not isinstance(option_map, dict):
            option_map = {}

        strikes = self._nearby_strikes(option_map, atm, strikes_around_atm)
        rows: list[dict[str, Any]] = []
        by_token = {str(row.get("token")): row for row in options if isinstance(row, dict)}
        for strike in strikes:
            maps = option_map.get(str(float(strike))) or option_map.get(str(strike)) or {}
            ce_map = maps.get("CE") or {}
            pe_map = maps.get("PE") or {}
            ce = by_token.get(str(ce_map.get("instrument_token")), {})
            pe = by_token.get(str(pe_map.get("instrument_token")), {})
            rows.append({
                "symbol": symbol,
                "expiry": expiry_s,
                "future_price": future_price,
                "strike": int(strike),
                "CE": ce,
                "PE": pe,
                "CE.tradingsymbol": ce_map.get("tradingsymbol"),
                "PE.tradingsymbol": pe_map.get("tradingsymbol"),
            })
        return rows, atm, future_price

    def get_atm_strike(self, symbol: str, expiry: date | str) -> Optional[int]:
        _, atm, _ = self.get_option_chain_with_greeks(symbol, expiry, 0)
        return atm

    def validate_response_schema(self, rows: list[dict[str, Any]]) -> bool:
        return all("strike" in row and "CE" in row and "PE" in row for row in rows)

    def _nearby_strikes(self, option_map: dict[str, Any], atm: int, n: int) -> list[int]:
        strikes = sorted(_to_int(k) for k in option_map.keys() if _to_int(k) is not None)
        if not strikes:
            return [atm]
        nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - atm))
        lo = max(0, nearest - n)
        hi = min(len(strikes), nearest + n + 1)
        return strikes[lo:hi]

    def _get_json(self, url: str) -> dict[str, Any]:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, headers=self.headers, follow_redirects=True) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    self.cache.set(url, data)
                    return data
            except Exception as exc:
                last_exc = exc
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"Sensibull GET failed: {last_exc}")

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = f"POST:{url}:{json.dumps(payload, sort_keys=True)}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, headers=self.headers, follow_redirects=True) as client:
                    resp = client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    self.cache.set(key, data)
                    return data
            except Exception as exc:
                last_exc = exc
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"Sensibull POST failed: {last_exc}")


def _normalize_underlying(symbol: str) -> str:
    return str(symbol).upper().replace("NSE:", "").replace("-INDEX", "").replace("-EQ", "")


def _dig(obj: Any, path: list[str], default: Any = None) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except Exception:
        return None
