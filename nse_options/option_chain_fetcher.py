"""
nse_options/option_chain_fetcher.py

Fetch live NSE option chain data. Tries TrueData first (primary), falls back
to Sensibull. Both paths normalise their output through normalize_greeks() so
the rest of the pipeline (pressure engine, expiry risk) is source-agnostic.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any, Optional

from utils.logger import logger
from nse_options.greeks_engine import normalize_greeks
from nse_options.sensibull_client import SensibullClient

_NUM_STRIKES = 5   # ATM ± N strikes


# ---------------------------------------------------------------------------
# TrueData option chain path
# ---------------------------------------------------------------------------

def _fetch_from_truedata(
    symbol: str,
    expiry: date | str,
    strikes_around_atm: int,
    spot: float = 0.0,
) -> dict[str, Any] | None:
    """
    Fetch option chain from TrueData OptionChain. Returns normalised context
    dict (same shape as Sensibull path) or None on any failure.

    This runs with a hard 8-second timeout so it never blocks the scanner.
    """
    try:
        from dotenv import dotenv_values
        from pathlib import Path
        env = dotenv_values(str(Path(__file__).parents[1] / ".env"))
        user = env.get("TRUEDATA_USER", "")
        pwd  = env.get("TRUEDATA_PASSWORD", "")
        port = int(env.get("TRUEDATA_WS_PORT", "8086"))
        if not user or not pwd:
            return None

        # Normalise expiry to datetime
        if isinstance(expiry, str):
            expiry_dt = datetime.fromisoformat(expiry.split("T")[0])
        elif isinstance(expiry, date) and not isinstance(expiry, datetime):
            expiry_dt = datetime(expiry.year, expiry.month, expiry.day)
        else:
            expiry_dt = expiry

        expiry_s = expiry_dt.strftime("%Y-%m-%d")

        result: dict = {}
        done = threading.Event()

        def _run():
            try:
                from truedata_ws.websocket.TD import TD
                from truedata_ws.websocket.TD_chain import OptionChain

                td = TD(user, pwd, live_port=port, log_level=logging.WARNING)
                time.sleep(2)

                future_price = round(spot / 50) * 50 if spot > 0 else 23750
                chain = OptionChain(
                    TD_OBJ=td,
                    symbol=symbol,
                    expiry=expiry_dt,
                    chain_length=strikes_around_atm,
                    future_price=future_price,
                    bid_ask=False,
                    market_open_post_hours=True,
                )

                df = chain.chain_dataframe
                try:
                    td.disconnect()
                except Exception:
                    pass

                if df is None or (hasattr(df, "__len__") and len(df) == 0):
                    return  # empty after hours — not an error

                # Convert TrueData chain_dataframe → normalize_greeks() row format
                rows = []
                atm_strike = None
                min_dist = float("inf")
                for _, row in df.iterrows():
                    strike = float(row.get("strike", 0))
                    if strike <= 0:
                        continue

                    dist = abs(strike - spot)
                    if dist < min_dist:
                        min_dist = dist
                        atm_strike = int(strike)

                    rows.append({
                        "strike": strike,
                        "CE": {
                            "ltp":               row.get("call_ltp"),
                            "iv":                row.get("call_iv"),
                            "oi":                row.get("call_oi"),
                            "volume":            row.get("call_volume"),
                            "delta":             row.get("call_delta"),
                            "gamma":             row.get("call_gamma"),
                            "theta":             row.get("call_theta"),
                            "vega":              row.get("call_vega"),
                        },
                        "PE": {
                            "ltp":               row.get("put_ltp"),
                            "iv":                row.get("put_iv"),
                            "oi":                row.get("put_oi"),
                            "volume":            row.get("put_volume"),
                            "delta":             row.get("put_delta"),
                            "gamma":             row.get("put_gamma"),
                            "theta":             row.get("put_theta"),
                            "vega":              row.get("put_vega"),
                        },
                    })

                result["rows"]   = rows
                result["atm"]    = atm_strike
                result["future"] = future_price

            except Exception as e:
                logger.debug("TrueData option chain thread error: %s", e)
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        done.wait(timeout=8)

        rows = result.get("rows")
        if not rows:
            return None

        context = normalize_greeks(
            rows,
            symbol=symbol,
            expiry=expiry_s,
            atm=result.get("atm"),
        )
        context["future_price"]   = result.get("future", 0)
        context["data_available"] = True
        context["source"]         = "truedata"
        return context

    except Exception as e:
        logger.debug("_fetch_from_truedata error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public interface — called by option_signal_context.py
# ---------------------------------------------------------------------------

def fetch_option_chain_context(
    symbol: str,
    expiry: date | str,
    strikes_around_atm: int = _NUM_STRIKES,
    spot: float = 0.0,
    client: Optional[SensibullClient] = None,
) -> dict[str, Any]:
    """
    Fetch option chain: TrueData primary, Sensibull fallback.

    Returns normalised context dict always — data_available=False if both fail.
    """
    # 1. Try TrueData
    try:
        ctx = _fetch_from_truedata(symbol, expiry, strikes_around_atm, spot)
        if ctx and ctx.get("data_available"):
            logger.debug("Option chain: TrueData OK for %s %s", symbol, expiry)
            return ctx
    except Exception as e:
        logger.debug("TrueData chain fetch failed: %s", e)

    # 2. Sensibull fallback
    try:
        sc = client or SensibullClient()
        rows, atm, future_price = sc.get_option_chain_with_greeks(
            symbol=symbol,
            expiry=expiry,
            strikes_around_atm=strikes_around_atm,
        )
        expiry_s = expiry.isoformat() if isinstance(expiry, date) else str(expiry)
        ctx = normalize_greeks(rows, symbol=symbol, expiry=expiry_s, atm=atm)
        ctx["future_price"]   = future_price
        ctx["data_available"] = bool(rows)
        ctx["source"]         = "sensibull"
        logger.debug("Option chain: Sensibull OK for %s %s", symbol, expiry)
        return ctx
    except Exception as exc:
        logger.warning("Sensibull option chain failed for %s: %s", symbol, exc)

    # Both failed
    expiry_s = expiry.isoformat() if isinstance(expiry, date) else str(expiry)
    return {
        "symbol":         symbol,
        "expiry":         expiry_s,
        "data_available": False,
        "source":         "none",
    }
