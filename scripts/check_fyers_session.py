import os
import sys
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import dotenv_values
from fyers_apiv3 import fyersModel


def classify_error(resp: Optional[Dict], default: str) -> str:
    if not isinstance(resp, dict):
        return default
    msg = str(resp.get("message", "")).lower()
    code = resp.get("code")
    if code in (-16, -22) or ("token" in msg and "expired" in msg):
        return "TOKEN_EXPIRED"
    if code == -99:
        return "BAD_REQUEST"
    return default


def main():
    base = os.path.dirname(os.path.dirname(__file__))
    env = dotenv_values(os.path.join(base, ".env"))

    client_id = str(env.get("CLIENT_ID", "")).strip()
    access_token = str(env.get("ACCESS_TOKEN", "")).strip()

    if not client_id or not access_token:
        print("TOKEN_MISSING: CLIENT_ID or ACCESS_TOKEN missing in .env")
        return

    token_client = access_token.split(":", 1)[0] if ":" in access_token else ""
    token_value = access_token.split(":", 1)[1] if ":" in access_token else access_token
    if token_client and token_client != client_id:
        print("BAD_REQUEST: ACCESS_TOKEN prefix does not match CLIENT_ID")
        return

    print("Token exists: YES")
    print(f"CLIENT_ID loaded: {client_id}")

    try:
        fy = fyersModel.FyersModel(client_id=client_id, token=token_value, is_async=False, log_path="")
    except Exception as e:
        print(f"BAD_REQUEST: failed to initialize fyers model: {e}")
        return

    profile = fy.get_profile()
    if not isinstance(profile, dict) or profile.get("code") != 200:
        err = classify_error(profile, default="BAD_REQUEST")
        print(f"{err}: get_profile failed -> {profile}")
        return
    print("get_profile: OK")

    hist = fy.history({
        "symbol": "NSE:NIFTY50-INDEX",
        "resolution": "5",
        "date_format": "1",
        "range_from": "2026-05-01",
        "range_to": "2026-05-05",
        "cont_flag": "1",
    })
    if not isinstance(hist, dict) or hist.get("code") != 200:
        err = classify_error(hist, default="HISTORY_FETCH_FAILED")
        print(f"{err}: history failed -> {hist}")
        return
    candles = hist.get("candles", []) or []
    if not candles:
        print("HISTORY_FETCH_FAILED: history returned success but no candles")
        return
    print(f"history: OK ({len(candles)} candles)")


if __name__ == "__main__":
    main()
