import argparse
import os
import sys
from typing import Any, Dict, Optional

import requests
from dotenv import dotenv_values

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


DEFAULT_MARKETDATA_BASE_URL = "https://ttblaze.iifl.com/apibinarymarketdata"
DEFAULT_INTERACTIVE_BASE_URL = "https://ttblaze.iifl.com/interactive"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-3:]}"


def _env(env: Dict[str, Any], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        value = os.getenv(key, default)
    return str(value or default).strip().strip("'\"")


def _extract_token(resp: Any) -> Optional[str]:
    if isinstance(resp, list) and resp:
        return _extract_token(resp[0])
    if not isinstance(resp, dict):
        return None
    result = resp.get("result")
    if isinstance(result, dict):
        token = result.get("token")
        return str(token) if token else None
    token = resp.get("token")
    return str(token) if token else None


def _classify_response(resp: Any) -> str:
    item = resp[0] if isinstance(resp, list) and resp else resp
    if not isinstance(item, dict):
        return "BAD_RESPONSE"
    item_type = str(item.get("type", "")).lower()
    code = str(item.get("code", "")).lower()
    desc = str(item.get("description", "")).lower()
    if item_type == "success" or code.startswith("s-"):
        return "OK"
    if "invalid" in desc or "credential" in desc:
        return "BAD_CREDENTIALS"
    if "token" in desc and "expired" in desc:
        return "TOKEN_EXPIRED"
    return "API_ERROR"


def _post_login(name: str, base_url: str, path: str, app_key: str, secret_key: str, source: str, timeout: int) -> bool:
    if not app_key or not secret_key:
        print(f"{name}: MISSING_KEYS")
        return False

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    payload = {
        "appKey": app_key,
        "secretKey": secret_key,
        "source": source,
    }
    print(f"{name}: login URL {url}")
    print(f"{name}: appKey {_mask(app_key)}")

    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        print(f"{name}: NETWORK_ERROR -> {exc}")
        return False

    try:
        data = response.json()
    except ValueError:
        print(f"{name}: BAD_RESPONSE HTTP {response.status_code} -> {response.text[:200]}")
        return False

    status = _classify_response(data)
    if response.status_code != 200 or status != "OK":
        print(f"{name}: {status} HTTP {response.status_code} -> {data}")
        return False

    token = _extract_token(data)
    if not token:
        print(f"{name}: BAD_RESPONSE -> success without token")
        return False

    print(f"{name}: OK token={_mask(token)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check IIFL/XTS NSE API credentials.")
    parser.add_argument("--marketdata-only", action="store_true", help="Check only IIFL Market Data API.")
    parser.add_argument("--interactive-only", action="store_true", help="Check only IIFL Interactive Order API.")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    base = os.path.dirname(os.path.dirname(__file__))
    env = dotenv_values(os.path.join(base, ".env"))

    source = _env(env, "IIFL_SOURCE", "WebAPI")
    marketdata_base = _env(env, "IIFL_MARKETDATA_BASE_URL", DEFAULT_MARKETDATA_BASE_URL)
    interactive_base = _env(env, "IIFL_INTERACTIVE_BASE_URL", DEFAULT_INTERACTIVE_BASE_URL)

    checks = []
    if not args.interactive_only:
        checks.append((
            "IIFL_MARKETDATA",
            marketdata_base,
            "/auth/login",
            _env(env, "IIFL_MARKETDATA_APP_KEY"),
            _env(env, "IIFL_MARKETDATA_SECRET_KEY"),
        ))
    if not args.marketdata_only:
        checks.append((
            "IIFL_INTERACTIVE",
            interactive_base,
            "/user/session",
            _env(env, "IIFL_INTERACTIVE_APP_KEY"),
            _env(env, "IIFL_INTERACTIVE_SECRET_KEY"),
        ))

    if not checks:
        print("No checks selected.")
        return 2

    all_ok = True
    for name, base_url, path, app_key, secret_key in checks:
        ok = _post_login(name, base_url, path, app_key, secret_key, source, args.timeout)
        all_ok = all_ok and ok

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
