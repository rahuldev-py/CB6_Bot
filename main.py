# main.py - CB6 QUANTUM ICT Buy + Sell + AI Learning

import os

import sys

import time

import json

import base64

import subprocess

import threading

from datetime import datetime, timezone

import pytz



_EMERGENCY_STOP_FLAG = os.path.join(os.path.dirname(__file__), "data", "NSE_EMERGENCY_STOP.flag")





def _emergency_stop_active() -> bool:

    """Layer 2 emergency stop: file-based. Layer 1=Telegram /stop. Layer 3=Ctrl+C."""

    return os.path.exists(_EMERGENCY_STOP_FLAG)

from fyers_apiv3 import fyersModel

from settings import (

    CLIENT_ID, ACCESS_TOKEN, CAPITAL,

    MIN_BUY_SCORE, MIN_SELL_SCORE, MAX_DAILY_LOSS_PCT,

    EXECUTION_MODE, MAX_ENTRY_DRIFT_PERCENT, MAX_ENTRY_DRIFT_POINTS,

    EXECUTION_MIN_RR, EXECUTION_INVALIDATION_BUFFER_POINTS,

    EXECUTION_ALLOWED_SIGNAL_AGE_SECONDS,

    EXECUTION_REVALIDATE_CYCLE_SECONDS, EXECUTION_MAX_SPREAD_PCT,

    ML_GATE_NSE,

)

from utils.logger import logger

from utils.telegram_alerts import send_message, send_test_alert

from utils.market_hours import (

    is_market_open,

    get_market_status

)

from utils.bot_listener import (

    start_listening,

    set_scan_callback,

    set_nifty_scan_callback,

    set_fyers_ref,

    set_signal_approval_callback,

)

from scanner.nifty50 import NIFTY200_SYMBOLS, INDEX_ETF_SYMBOLS, FUTURES_SYMBOLS

from scanner.index_futures import get_lot_size, is_futures

from trader.paper_trader import (

    open_paper_trade,

    update_paper_trades,

    get_portfolio_summary,

    reset_paper_state_if_new_day,

)

from utils.scheduler import start_scheduler, schedule_daily, schedule_weekly

from dashboard import start_dashboard, archive_trades



fyers_instance = None

_EXECUTION_VALIDATION_CONFIG = {

    'max_entry_drift_percent': MAX_ENTRY_DRIFT_PERCENT,

    'max_entry_drift_points': MAX_ENTRY_DRIFT_POINTS,

    'minimum_required_rr': EXECUTION_MIN_RR,

    'invalidation_buffer_points': EXECUTION_INVALIDATION_BUFFER_POINTS,

    'allowed_signal_age_seconds': EXECUTION_ALLOWED_SIGNAL_AGE_SECONDS,

    'revalidate_cycle_seconds': EXECUTION_REVALIDATE_CYCLE_SECONDS,

    'max_spread_pct': EXECUTION_MAX_SPREAD_PCT,

}





def _rearm_existing_trade_triggers():

    """

    On WebSocket startup: re-register SL/TP triggers for all currently open paper trades

    and subscribe to their symbols. Without this, restart loses real-time SL coverage.

    """

    try:

        from trader.paper_trader import load_state

        from core.trade_triggers import register_trade_triggers

        from scanner.websocket_feed import subscribe

        state = load_state()

        symbols = []

        for trade in state.get('open_trades', []):

            register_trade_triggers(trade)

            symbols.append(trade['symbol'])

        if symbols:

            subscribe(symbols)

            logger.info(f"Re-armed WS triggers for {len(symbols)} open trades")

    except Exception as e:

        logger.error(f"Trigger re-arm error: {e}")





def _trace(event: str, symbol: str, **kwargs) -> None:

    """Structured execution-trace log. Grep for [TRACE] to follow a signal end-to-end."""

    try:

        import pytz as _ptz

        ts = datetime.now(_ptz.timezone("Asia/Kolkata")).strftime("%H:%M:%S IST")

        parts = [f"[TRACE] {event}", f"sym={symbol}", f"ts={ts}"]

        for k, v in kwargs.items():

            parts.append(f"{k}={v}")

        logger.info(" | ".join(parts))

    except Exception:

        pass





def _read_token_from_env():

    """Always read the freshest ACCESS_TOKEN directly from .env file.

    

    Returns empty string if .env is not readable or token not found.

    Does NOT fall back to stale token from settings.py.

    Strips surrounding quotes that may be added by dotenv library.

    """

    try:

        from dotenv import dotenv_values

        env_path = os.path.join(os.path.dirname(__file__), '.env')

        token = dotenv_values(env_path).get('ACCESS_TOKEN', '')

        if token:

            token = token.strip("'\"")

            return token

        logger.debug("ACCESS_TOKEN not found in .env")

        return ''

    except Exception as e:

        logger.debug(f"Error reading token from .env: {e}")

        return ''





def is_token_fresh():

    """Return True if the token in .env was issued today (IST).

    

    Explicitly converts UTC timestamp from JWT 'iat' claim to IST timezone

    before comparing to today's IST date. Returns False for invalid/missing tokens.

    """

    try:

        token_str = _read_token_from_env()

        if not token_str:

            logger.debug("No token found in .env")

            return False

        

        # Strip CLIENT_ID prefix if present (format: CLIENT_ID:JWT)

        if ":" in token_str:

            token_str = token_str.split(":", 1)[1]

        

        # Validate JWT structure (header.payload.signature)

        parts = token_str.split(".")

        if len(parts) < 2:

            logger.debug(f"Invalid JWT format: {len(parts)} parts")

            return False

        

        # Decode JWT payload

        payload_b64 = parts[1]

        payload_b64 += "=" * (-len(payload_b64) % 4)  # Add base64 padding

        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        

        # Extract 'iat' (issued-at) timestamp from JWT claims

        iat_timestamp = payload.get('iat')

        if not iat_timestamp:

            logger.debug("JWT missing 'iat' claim")

            return False

        

        # Convert UTC timestamp to IST timezone

        iat_utc = datetime.fromtimestamp(iat_timestamp, tz=timezone.utc)

        ist_tz = pytz.timezone('Asia/Kolkata')

        iat_ist = iat_utc.astimezone(ist_tz)

        iat_date_ist = iat_ist.strftime('%Y-%m-%d')

        

        # Compare token's IST date to today's IST date

        today_ist = datetime.now(ist_tz).strftime('%Y-%m-%d')

        

        is_fresh = iat_date_ist == today_ist

        logger.debug(f"Token freshness check: iat_date_ist={iat_date_ist}, today_ist={today_ist}, fresh={is_fresh}")

        return is_fresh

        

    except Exception as e:

        logger.error(f"Token freshness check error: {e}  -- assuming token is invalid")

        return False





def run_token_refresh():

    """Launch web_token.py to capture today's Fyers token.

    

    Implements retry logic with exponential backoff (3 attempts, 30s base delay).

    Validates token was actually saved to .env and is fresh before returning True.

    Returns True only if token refresh succeeds and is verified.

    """

    max_retries = 3

    base_delay = 30  # seconds

    

    logger.warning("Token is stale  -- launching web_token.py...")

    send_message(

        "CB6 QUANTUM - TOKEN REFRESH\n\n"

        "Today's token not found.\n"

        "Opening Fyers login in browser...\n"

        "Complete the login within 2 minutes."

    )

    

    web_token_path = os.path.join(os.path.dirname(__file__), "broker", "web_token.py")

    

    for attempt in range(1, max_retries + 1):

        try:

            logger.info(f"Token refresh attempt {attempt}/{max_retries}")

            result = subprocess.run([sys.executable, web_token_path], timeout=180)

            

            if result.returncode != 0:

                logger.error(f"web_token.py exited with code {result.returncode} (attempt {attempt})")

                if attempt < max_retries:

                    delay = base_delay * (2 ** (attempt - 1))  # exponential backoff

                    logger.info(f"Retrying in {delay}s...")

                    time.sleep(delay)

                continue

            

            # Subprocess succeeded (code 0)  -- now validate the token was actually saved

            time.sleep(2)  # Give .env file time to be written/flushed

            

            # Read token from .env and verify it's fresh

            saved_token = _read_token_from_env()

            if not saved_token:

                logger.error(f"Token not found in .env after refresh (attempt {attempt})")

                if attempt < max_retries:

                    delay = base_delay * (2 ** (attempt - 1))

                    logger.info(f"Retrying in {delay}s...")

                    time.sleep(delay)

                continue

            

            if not is_token_fresh():

                logger.error(f"Refreshed token is not fresh (attempt {attempt})")

                if attempt < max_retries:

                    delay = base_delay * (2 ** (attempt - 1))

                    logger.info(f"Retrying in {delay}s...")

                    time.sleep(delay)

                continue

            

            # Token is valid and fresh!

            logger.info(f"Token refreshed successfully on attempt {attempt}")

            send_message("CB6 QUANTUM - Token refreshed! Connecting now...")

            return True

            

        except subprocess.TimeoutExpired:

            logger.error(f"Token refresh timed out (180s) on attempt {attempt}")

            if attempt < max_retries:

                delay = base_delay * (2 ** (attempt - 1))

                logger.info(f"Retrying in {delay}s...")

                time.sleep(delay)

        except Exception as e:

            logger.error(f"Token refresh error on attempt {attempt}: {e}")

            if attempt < max_retries:

                delay = base_delay * (2 ** (attempt - 1))

                logger.info(f"Retrying in {delay}s...")

                time.sleep(delay)

    

    # All retries exhausted

    logger.error(f"Token refresh failed after {max_retries} attempts")

    return False





def initialize_fyers():

    """Always reads the freshest token from .env (handles post-refresh case)."""

    token_str = _read_token_from_env()

    if ":" in token_str:

        token_str = token_str.split(":", 1)[1]

    fyers = fyersModel.FyersModel(

        client_id=CLIENT_ID,

        token=token_str,

        is_async=False,

        log_path=os.path.join(os.getcwd(), "logs", "")

    )

    return fyers





def test_connection(fyers):

    profile = fyers.get_profile()

    if profile.get('code') == 200:

        name = profile['data']['name']

        logger.info(f"Connected | User: {name}")

        return True

    else:

        logger.error(f"Connection failed: {profile}")

        return False





def _send_setup_chart(setup, df=None):

    """Render and send the ICT chart screenshot for a setup."""

    try:

        from utils.chart_renderer import render_setup_chart

        from utils.telegram_alerts import send_photo

        if df is None:

            from scanner.data_fetcher import get_historical_data

            tf_str = str(setup.get('timeframe', '15min')).replace('min', '')

            df = get_historical_data(fyers_instance, setup['symbol'], tf_str, days=10)

        if df is None:

            return

        png = render_setup_chart(df, setup)

        if png:

            sym = setup['symbol'].replace('NSE:', '').replace('-EQ', '')

            send_photo(png, f"{sym} {setup.get('direction','BUY')} setup chart")

    except Exception as e:

        logger.debug(f"Setup chart error: {e}")







def _ml_memory_block(setup: dict) -> str:

    """Return ML memory analysis block for Telegram alerts."""

    try:

        from ml.trade_memory import score_setup_similarity, format_entry_analysis

        import datetime as _dt_mlm

        sim = score_setup_similarity(

            mss_type      = setup.get('mss_type', 'BOS'),

            direction     = setup.get('direction', ''),

            fvg_size      = (setup.get('fvg') or {}).get('size', 0),

            score         = setup.get('confluence', 0),

            sweep_quality = setup.get('sweep_quality', 0),

            index_name    = setup.get('symbol','').replace('NSE:','').split('-')[0].split('2')[0],

            entry_hour    = _dt_mlm.datetime.now().hour,

        )

        setup['ml_memory'] = sim

        return format_entry_analysis(sim)

    except Exception:

        return ''



def send_buy_alert(setup, timeframe):

    try:

        sig    = setup['entry_signal']

        symbol = setup['symbol'].replace("NSE:", "").replace("-EQ", "")

        score  = setup.get('confluence', 0)

        fii    = setup.get('fii_bias', 'N/A')

        frvp   = setup.get('frvp') or {}

        frvp_block = ""

        if frvp:

            frvp_block = (

                "\n--- FRVP VOLUME PROFILE ---\n"

                f"POC       : {frvp.get('poc', 'N/A')} (fair value)\n"

                f"VAH       : {frvp.get('vah', 'N/A')} (value area high)\n"

                f"VAL       : {frvp.get('val', 'N/A')} (value area low)\n"

            )

        psy = setup.get('psychology') or {}

        psy_block = ""

        if psy:

            traps = psy.get('traps', [])

            trap_str = (

                "; ".join(t['message'] for t in traps[:2]) if traps else "none"

            )

            psy_block = (

                "\n--- SMART MONEY READ ---\n"

                f"Wyckoff   : {psy.get('phase', 'NEUTRAL')}\n"

                f"Crowd     : {psy.get('crowd', 'NEUTRAL')}\n"

                f"Retail Traps: {trap_str}\n"

                f"Verdict   : {psy.get('verdict', '?')} "

                f"(adj {psy.get('total_adjustment', 0):+.1f})\n"

            )

        send_message(

            "CB6 QUANTUM - BUY SETUP FOUND\n\n"

            f"Symbol    : {symbol}\n"

            f"Direction : LONG BUY\n"

            f"Timeframe : {timeframe}\n"

            f"Bias      : {setup.get('bias', 'N/A')}\n"

            f"FII/DII   : {fii}\n"

            f"Score     : {score}/10\n\n"

            "--- ICT BUY ANALYSIS ---\n"

            f"Sweep Low : {sig.get('sweep_low', 'N/A')}\n"

            f"MSS Level : {sig.get('neck_price', 'N/A')}\n"

            f"FVG Zone  : {sig.get('fvg_low', 'N/A')} - {sig.get('fvg_high', 'N/A')}\n"

            f"OTE Zone  : {sig.get('ote_low', 'N/A')} - {sig.get('ote_high', 'N/A')}\n"

            f"In OTE    : {setup.get('in_ote', False)}\n"

            f"In FVG    : {setup.get('in_fvg', False)}\n"

            + frvp_block

            + psy_block + "\n"

            "--- TRADE PLAN ---\n"

            f"Entry     : {sig['entry']}\n"

            f"Stop Loss : {sig['stop_loss']}\n"

            f"Target 1  : {sig['target1']}\n"

            f"Target 2  : {sig['target2']}\n"

            f"Target 3  : {sig['target3']}\n"

            f"Risk      : {sig['risk']}\n"

            f"RR Ratio  : 1:{sig['rr_ratio']}\n\n"

            "Mode: <b>LIVE TRADING</b> (Fyers ILRAADDBFV-200)"

            + _ml_memory_block(setup)

        )

        # #26 Attach annotated setup chart

        _send_setup_chart(setup)

    except Exception as e:

        logger.error(f"Buy alert error: {e}")





def send_sell_alert(setup, timeframe):

    try:

        sig    = setup['entry_signal']

        symbol = setup['symbol'].replace("NSE:", "").replace("-EQ", "")

        score  = setup.get('confluence', 0)

        fii    = setup.get('fii_bias', 'N/A')

        fvg_low  = sig.get('fvg_low', 'N/A')

        fvg_high = sig.get('fvg_high', 'N/A')

        frvp     = setup.get('frvp') or {}

        frvp_block = ""

        if frvp:

            frvp_block = (

                "\n--- FRVP VOLUME PROFILE ---\n"

                f"POC       : {frvp.get('poc', 'N/A')} (fair value)\n"

                f"VAH       : {frvp.get('vah', 'N/A')} (value area high)\n"

                f"VAL       : {frvp.get('val', 'N/A')} (value area low)\n"

            )

        psy = setup.get('psychology') or {}

        psy_block = ""

        if psy:

            traps = psy.get('traps', [])

            trap_str = (

                "; ".join(t['message'] for t in traps[:2]) if traps else "none"

            )

            psy_block = (

                "\n--- SMART MONEY READ ---\n"

                f"Wyckoff   : {psy.get('phase', 'NEUTRAL')}\n"

                f"Crowd     : {psy.get('crowd', 'NEUTRAL')}\n"

                f"Retail Traps: {trap_str}\n"

                f"Verdict   : {psy.get('verdict', '?')} "

                f"(adj {psy.get('total_adjustment', 0):+.1f})\n"

            )

        send_message(

            "CB6 QUANTUM - SELL SETUP FOUND\n\n"

            f"Symbol    : {symbol}\n"

            f"Direction : SHORT SELL\n"

            f"Timeframe : {timeframe}\n"

            f"Bias      : {setup.get('bias', 'N/A')}\n"

            f"FII/DII   : {fii}\n"

            f"Score     : {score}/10\n\n"

            "--- ICT SELL ANALYSIS ---\n"

            f"BSL Pool  : {sig.get('pool_level', 'N/A')}\n"

            f"Manip High: {sig.get('manip_high', 'N/A')}\n"

            f"MSS Level : {sig.get('mss_level', 'N/A')}\n"

            f"Sell OB   : {sig.get('ob_level', 'N/A')}\n"

            f"FVG Zone  : {fvg_low} - {fvg_high}\n"

            f"OTE Zone  : {sig.get('ote_low', 'N/A')} - {sig.get('ote_high', 'N/A')}\n"

            f"In OTE    : {setup.get('in_ote', False)}\n"

            + frvp_block

            + psy_block + "\n"

            "--- TRADE PLAN ---\n"

            f"Entry     : {sig['entry']} (SHORT)\n"

            f"Stop Loss : {sig['stop_loss']}\n"

            f"Target 1  : {sig['target1']}\n"

            f"Target 2  : {sig['target2']}\n"

            f"Target 3  : {sig['target3']}\n"

            f"Risk      : {sig['risk']}\n"

            f"RR Ratio  : 1:{sig['rr_ratio']}\n\n"

            "Mode: <b>LIVE TRADING</b> (Fyers ILRAADDBFV-200)"

            + _ml_memory_block(setup)

        )

        # #26 Attach annotated setup chart

        _send_setup_chart(setup)

    except Exception as e:

        logger.error(f"Sell alert error: {e}")





def run_scan():

    """Index-only mode: redirect /scan to Silver Bullet index futures scan."""

    logger.info("run_scan: equity stock scans disabled  -- running index SB scan")

    run_silver_bullet_scan()





_sb_daily_taken: set = set()   # dedup: (date, direction)  -- one trade per side per day

_sb_window_announced: set = set()  # (date, window_name)  -- suppress repeated open banners





def _apply_live_entry(setup: dict, df, fyers=None, symbol: str = '') -> dict | None:

    """

    Replace the FVG-boundary theoretical entry with the actual live market price.



    Price source priority:

      1. Fyers quotes API (get_live_price)  -- real-time LTP, zero cache lag.

      2. Last closed candle close (df)  -- fallback if quotes API fails.



    Historical candles are still used for structure (FVG/MSS/DOL detection).

    This function only determines the ENTRY PRICE for the paper trade.



    Flow:

      * Fetch live LTP for the symbol.

      * Check LTP is inside the FVG zone (not just a wick on a closed candle).

      * If outside -> signal is stale -> return None (skip trade).

      * If inside -> set entry = LTP, keep structural SL, recalc targets.

    """

    fvg      = setup.get('fvg', {})

    fvg_low  = fvg.get('fvg_low')

    fvg_high = fvg.get('fvg_high')

    if not fvg_low or not fvg_high:

        return setup  # no FVG data  -- use scanner entry as-is



    #  Â¢" âÂ Â¢" âÂ¬ Fetch live price  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

    live_price = None

    if fyers and symbol:

        try:

            from scanner.live_price import get_live_price

            live_price = get_live_price(fyers, symbol)

        except Exception:

            pass



    if live_price is None:

        # Fallback 1: Yahoo Finance spot price (60s stale max - better than 5-min candle)

        try:

            from data.nse_yahoo_feed import get_yahoo_nse_price

            yahoo_px = get_yahoo_nse_price(symbol)

            if yahoo_px and yahoo_px > 0:

                live_price = round(yahoo_px, 2)

                logger.info(f"Live price {symbol}: {live_price} (Yahoo fallback)")

        except Exception:

            pass



    if live_price is None:

        # Fallback 2: last closed candle (up to 5 min stale  -- log the degradation)

        live_price = round(float(df['close'].iloc[-1]), 2)

        logger.warning(f"Live price unavailable for {symbol}  -- using candle close {live_price}")

    else:

        live_price = round(live_price, 2)

        logger.debug(f"Live price {symbol}: {live_price} (FVG zone {fvg_low} --{fvg_high})")



    direction = setup['direction']



    #  Â¢" âÂ Â¢" âÂ¬ Stale check with approach buffer  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

    # Strict in-zone check; near-FVG approach entries are intentionally blocked.

    in_fvg = fvg_low <= live_price <= fvg_high



    if not in_fvg:

        logger.info(

            f"Stale signal {symbol}: LTP {live_price} outside FVG {fvg_low}-{fvg_high} -- skipping"

        )

        return None



    #  Â¢" âÂ Â¢" âÂ¬ Build trade plan from live entry  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

    sig  = setup['entry_signal']

    sig['entry'] = live_price

    risk = round(abs(live_price - sig['stop_loss']), 2)

    if risk <= 0:

        return None

    sig['risk'] = risk



    dol = sig.get('dol_level', None)

    if direction == 'BULLISH':

        t1     = round(live_price + risk * 2.0, 2)

        t2     = round(live_price + risk * 3.0, 2)

        t3_raw = dol if (dol and dol > t2) else live_price + risk * 4.0

        t3     = round(max(t3_raw, t2), 2)

    else:

        t1     = round(live_price - risk * 2.0, 2)

        t2     = round(live_price - risk * 3.0, 2)

        t3_raw = dol if (dol and dol < t2) else live_price - risk * 4.0

        t3     = round(min(t3_raw, t2), 2)



    sig['target1']  = t1

    sig['target2']  = t2

    sig['target3']  = t3

    sig['rr_ratio'] = round((t2 - live_price) / risk if direction == 'BULLISH'

                            else (live_price - t2) / risk, 2)

    return setup





def _safe_mode_enabled() -> bool:

    return EXECUTION_MODE in ('SAFE_VALIDATION', 'HYBRID_TEST', 'SAFE_VALIDATION_REVALIDATE_AUTO')





def _safe_revalidate_auto_enabled() -> bool:

    return EXECUTION_MODE == 'SAFE_VALIDATION_REVALIDATE_AUTO'





def _parse_iso(ts: str):

    try:

        return datetime.fromisoformat(ts)

    except Exception:

        return None





def _current_option_spread_pct(option_symbol: str):

    """Return spread percentage as (ok, spread_pct, reason)."""

    try:

        if not fyers_instance:

            return False, None, "SANITY_CHECK_NO_FYERS_SESSION"

        resp = fyers_instance.quotes({"symbols": option_symbol})

        if not isinstance(resp, dict) or resp.get('code') != 200:

            return False, None, "SANITY_CHECK_SPREAD_FETCH_FAILED"

        d = (resp.get('d') or [])

        if not d:

            return False, None, "SANITY_CHECK_SPREAD_DATA_MISSING"

        v = d[0].get('v', {})

        bid = float(v.get('bid_price') or v.get('bid') or v.get('bp') or 0)

        ask = float(v.get('ask_price') or v.get('ask') or v.get('ap') or 0)

        if bid <= 0 or ask <= 0:

            return False, None, "SANITY_CHECK_SPREAD_DATA_MISSING"

        spread_pct = (ask - bid) / bid

        return True, spread_pct, ""

    except Exception:

        return False, None, "SANITY_CHECK_SPREAD_FETCH_FAILED"





def _sanity_check_revalidate_auto(signal: dict):

    """Spread and bracket sanity checks before auto execution."""

    setup = signal.get('setup') or {}

    option_info = setup.get('option_info') or {}

    option_symbol = option_info.get('symbol')

    if not option_symbol:

        return False, "SANITY_CHECK_OPTION_SYMBOL_MISSING"



    planned = float(signal.get('planned_entry', 0) or 0)

    stop = float(signal.get('stop_loss', 0) or 0)

    target = float(signal.get('target', 0) or 0)

    direction = str(signal.get('direction', '')).upper()

    is_long = direction in ('BULLISH', 'BUY', 'LONG')



    if is_long and not (stop < planned < target):

        return False, "SANITY_CHECK_BRACKET_INVERTED"

    if (not is_long) and not (target < planned < stop):

        return False, "SANITY_CHECK_BRACKET_INVERTED"



    ok, spread_pct, reason = _current_option_spread_pct(option_symbol)

    if not ok:

        return False, reason

    if spread_pct is not None and spread_pct > EXECUTION_MAX_SPREAD_PCT:

        return False, "SANITY_CHECK_SPREAD_TOO_WIDE"

    return True, "OK"





def _process_armed_revalidate_auto_signals():

    """Process ARMED signals after one cycle and auto-execute on pass."""

    if not _safe_revalidate_auto_enabled():

        return

    

      

    try:

        from scanner.live_price import get_live_price

        from trader.order_manager import place_silver_bullet_trade

        from utils.execution_validation import (

            list_signals_by_state,

            patch_signal,

            revalidate_for_auto,

            SIGNAL_ARMED,

            SIGNAL_WAITING_CONFIRM,

            SIGNAL_EXECUTED,

            SIGNAL_REJECTED,

        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        armed = list_signals_by_state(SIGNAL_ARMED)

        for signal in armed:

            signal_id = signal.get('signal_id')

            armed_at = _parse_iso(signal.get('armed_at', '')) or _parse_iso(signal.get('updated_at', ''))

            if not armed_at:

                # Fail-safe: no timing metadata means reject uncertain payload.

                patch_signal(signal_id, {}, state_value=SIGNAL_REJECTED, reason="REVALIDATION:MISSING_ARMED_TIMESTAMP")

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    "Block_Reason: REVALIDATION:MISSING_ARMED_TIMESTAMP"

                )

                continue

            if (now - armed_at).total_seconds() < EXECUTION_REVALIDATE_CYCLE_SECONDS:

                continue



            try:

                cur_ltp = get_live_price(fyers_instance, signal.get('symbol')) or signal.get('current_ltp')

            except Exception:

                cur_ltp = signal.get('current_ltp')

            if cur_ltp is None:

                patch_signal(signal_id, {}, state_value=SIGNAL_REJECTED, reason="REVALIDATION:MISSING_LTP")

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    "Block_Reason: REVALIDATION:MISSING_LTP"

                )

                continue



            st, reason, revised = revalidate_for_auto(

                signal=signal,

                current_ltp=float(cur_ltp),

                config=_EXECUTION_VALIDATION_CONFIG,

            )

            if st != SIGNAL_WAITING_CONFIRM:

                patch_signal(

                    signal_id,

                    {'current_ltp': float(cur_ltp), 'calculated_rr': revised.get('calculated_rr')},

                    state_value=st,

                    reason=f"REVALIDATION:{reason}",

                )

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    f"Block_Reason: REVALIDATION:{reason}"

                )

                continue



            ok, sanity_reason = _sanity_check_revalidate_auto(signal)

            if not ok:

                patch_signal(signal_id, {'current_ltp': float(cur_ltp)}, state_value=SIGNAL_REJECTED,

                             reason=f"REVALIDATION:{sanity_reason}")

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    f"Block_Reason: REVALIDATION:{sanity_reason}"

                )

                continue



            setup = signal.get('setup') or {}

            option_info = setup.get('option_info')

            if not setup or not option_info:

                patch_signal(signal_id, {}, state_value=SIGNAL_REJECTED, reason="REVALIDATION:MISSING_SETUP_OR_OPTION")

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    "Block_Reason: REVALIDATION:MISSING_SETUP_OR_OPTION"

                )

                continue



            trade = place_silver_bullet_trade(fyers_instance, setup, option_info, paper_mode=False)

            if not trade:

                patch_signal(signal_id, {}, state_value=SIGNAL_REJECTED, reason="REVALIDATION:EXECUTION_FAILED")

                logger.warning(

                    f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {signal_id} | "

                    "Block_Reason: REVALIDATION:EXECUTION_FAILED"

                )

                continue



            patch_signal(signal_id, {'current_ltp': float(cur_ltp)}, state_value=SIGNAL_EXECUTED,

                         reason="AUTO_REVALIDATE_EXECUTED")

            logger.info(

                f"[SAFE_REVALIDATE_AUTO][EXECUTE] Signal_ID: {signal_id} | "

                "Structural & Temporal Validation Passed."

            )

    except Exception as e:

        logger.error(f"SAFE_REVALIDATE_AUTO processing error: {e}")





def _build_signal_approval_message(signal: dict) -> str:

    return (

        "Trade Ready for Approval\n\n"

        f"Signal ID: {signal.get('signal_id')}\n"

        f"Symbol: {signal.get('symbol')}\n"

        f"Direction: {signal.get('direction')}\n"

        f"Planned Entry: {signal.get('planned_entry')}\n"

        f"Current LTP: {signal.get('current_ltp')}\n"

        f"Stop Loss: {signal.get('stop_loss')}\n"

        f"Target: {signal.get('target')}\n"

        f"RR: {signal.get('calculated_rr')}\n"

        f"Signal Age: {signal.get('signal_age_seconds')}s\n"

        f"Status: {signal.get('state')}\n"

        f"Reason: {signal.get('status_reason')}\n\n"

        f"Approve: /approve {signal.get('signal_id')}\n"

        f"Reject: /reject {signal.get('signal_id')}"

    )





def _handle_signal_approval(signal_id: str, approved: bool, chat_id: str = ''):

    try:

        from utils.execution_validation import (

            get_signal,

            update_signal,

            revalidate_existing,

            SIGNAL_WAITING_CONFIRM,

            SIGNAL_APPROVED,

            SIGNAL_EXECUTED,

            SIGNAL_REJECTED,

        )

        signal = get_signal(signal_id)

        if not signal:

            return False, f"Signal not found: {signal_id}"



        state = signal.get('state')

        if state != SIGNAL_WAITING_CONFIRM:

            return False, f"Signal {signal_id} is in state {state}, not awaiting approval."



        if not approved:

            update_signal(signal_id, SIGNAL_REJECTED, "MANUAL_REJECTED")

            return True, f"Signal {signal_id} rejected."



        setup = signal.get('setup') or {}

        if not setup:

            update_signal(signal_id, SIGNAL_REJECTED, "MISSING_SETUP_PAYLOAD")

            return False, f"Signal {signal_id} rejected: missing setup payload."



        try:

            from scanner.live_price import get_live_price

            current_ltp = get_live_price(fyers_instance, signal.get('symbol')) or signal.get('current_ltp')

        except Exception:

            current_ltp = signal.get('current_ltp')



        if current_ltp is None:

            return False, f"Signal {signal_id}: cannot get LTP for revalidation."



        status, reason, updated = revalidate_existing(

            signal=signal,

            current_ltp=float(current_ltp),

            config=_EXECUTION_VALIDATION_CONFIG,

        )

        if status != SIGNAL_WAITING_CONFIRM:

            update_signal(signal_id, status, f"REVALIDATION_FAILED:{reason}", current_ltp=float(current_ltp))

            return False, f"Signal {signal_id} not executable: {reason}"



        update_signal(signal_id, SIGNAL_APPROVED, "MANUAL_APPROVED", current_ltp=float(current_ltp))



        option_info = setup.get('option_info')

        if not option_info:

            update_signal(signal_id, SIGNAL_REJECTED, "MISSING_OPTION_INFO_AT_EXECUTION")

            return False, f"Signal {signal_id} rejected: option info missing."



        from trader.order_manager import place_silver_bullet_trade

        trade = place_silver_bullet_trade(

            fyers_instance,

            setup,

            option_info,

            paper_mode=False

        )

        if not trade:

            update_signal(signal_id, SIGNAL_REJECTED, "EXECUTION_FAILED_POST_APPROVAL")

            return False, f"Signal {signal_id} approved but execution failed."



        update_signal(signal_id, SIGNAL_EXECUTED, "EXECUTED_POST_MANUAL_APPROVAL")

        return True, f"Signal {signal_id} executed."

    except Exception as e:

        logger.exception(f"Signal approval handler error: {e}")

        return False, f"Approval error: {e}"





def run_silver_bullet_scan():

    """

    Fires at window open (10:00 / 13:30) AND every 5 min while inside the window.

    Banner shows once per window; subsequent scans run silently.

    """

    if _emergency_stop_active():

        logger.warning("EMERGENCY_STOP.flag detected - silver bullet scan skipped")

        return



    global fyers_instance, _sb_daily_taken, _sb_window_announced



    from scanner.silver_bullet import (

        scan_silver_bullet, format_sb_alert,

        get_window_status, is_silver_bullet_window

    )

    from scanner.index_futures  import get_active_futures

    from scanner.data_fetcher   import get_historical_data



    # Reset dedup set if it's a new day

    today = datetime.now().strftime('%Y-%m-%d')

    _sb_daily_taken = {k for k in _sb_daily_taken if k[0] == today}



    in_window, window_name = is_silver_bullet_window()



    # Mid-window repeating call  -- skip silently when outside any window

    if not in_window:

        return



    futures = get_active_futures()



    instruments = {

        futures['NIFTY']      : 'NIFTY 50',

        futures['BANKNIFTY']  : 'NIFTY BANK',

        futures['FINNIFTY']   : 'NIFTY FIN SERVICE',

        futures['MIDCPNIFTY'] : 'NIFTY MIDCAP 150',

    }



    from datetime import datetime as _dt

    import pytz as _pytz

    _now_ist = _dt.now(_pytz.timezone('Asia/Kolkata')).strftime('%H:%M')



    found        = 0

    skip_reasons = []   # collect per-symbol block reasons for end-of-scan digest



    for symbol, name in instruments.items():

        try:

            df = get_historical_data(fyers_instance, symbol, '3', days=3)

            if df is None or len(df) < 30:

                skip_reasons.append(f"{name}: no data")

                continue

            from scanner.data_fetcher import inject_live_tick as _inj
            df = _inj(df, symbol)

            setup = scan_silver_bullet(df, symbol, tf='3', fyers=fyers_instance)

            if not setup:

                skip_reasons.append(f"{name}: DOL/MSS/FVG chain incomplete")

                logger.info(f"Silver Bullet: no setup on {name}")

                continue



            score     = setup.get('confluence', 0)

            direction = setup.get('direction', '')

            # Zone-based dedup: allow re-entry if a distinct FVG zone forms later in the day

            fvg_data  = setup.get('fvg', {})

            fvg_zone  = round(fvg_data.get('fvg_low', 0) / 50) * 50   # round to nearest 50pts

            dedup_key = (today, symbol, direction, fvg_zone)



            # Atomic check-and-claim: both CHECK and WRITE inside a single lock
            # acquisition.  Splitting into two separate `with _nse_dedup_lock:`
            # blocks created a TOCTOU race — two threads could both pass the
            # check before either completed the write.
            with _nse_dedup_lock:

                if dedup_key in _sb_daily_taken or dedup_key in _live_alerted:

                    logger.info(f"SB dedup: already alerted {symbol} {direction} zone~{fvg_zone} today")

                    continue

                _sb_daily_taken.add(dedup_key)

                _live_alerted.add(dedup_key)  # cross-mark so live scanner also skips this zone

            found += 1



            setup['timeframe']       = '3min'

            setup['instrument_type'] = 'INDEX'



            #  Â¢" âÂ Â¢" âÂ¬ Pattern confidence engine  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

            from data.pattern_library import compute_trade_confidence, SCORE_GATE_HIGH

            conf = compute_trade_confidence(setup)

            setup['pattern_confidence'] = conf



            score_gate   = conf.get('score_gate', SCORE_GATE_HIGH)
            should_trade = conf.get('should_trade', False)
            decision     = conf.get('decision', '')

            # Expiry day: raise score gate by 2 (gamma distortion = more false sweeps)
            if setup.get('is_expiry_day'):
                score_gate += 2
                logger.info(f"SB {name}: expiry day -- score gate raised to {score_gate}")

            # High-impact NSE news filter (RBI MPC, Indian Budget, FOMC-impact day)
            try:
                from data.news_calendar import is_high_impact_event, get_session_risk_multiplier
                if is_high_impact_event():
                    score_gate += 2
                    logger.info(f"SB {name}: high-impact event -- score gate raised to {score_gate}")
                _srm = get_session_risk_multiplier()
                if _srm < 1.0:
                    setup['risk_multiplier_override'] = _srm
            except Exception:
                pass

            if score < score_gate:
                skip_reasons.append(f"{name}: score {score} < gate {score_gate}")
                logger.info(f"SB skip {name}: score {score} < gate {score_gate} | {decision}")
                continue

            if not should_trade:

                skip_reasons.append(f"{name}: pattern library - {decision[:60]}")

                logger.info(f"SB alert-only {name}: {decision}")

                continue



            # Ã¢"â¬Ã¢"â¬ ML Gate Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

            if ML_GATE_NSE:

                try:

                    import uuid, numpy as np

                    from ml.predictor import predict_nse

                    ml_trade_id = str(uuid.uuid4())[:8]

                    candles_np = None

                    try:

                        candles_np = df[['open','high','low','close','volume']].tail(50).values.astype(float)

                    except Exception:

                        pass

                    idx_name = 'NIFTY'

                    for _idx in ('BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY'):

                        if _idx in symbol.upper():

                            idx_name = _idx

                            break

                    ml_pred = predict_nse(ml_trade_id, setup, candles=candles_np, index_name=idx_name, gate_only=True)

                    if ml_pred:

                        wp   = ml_pred.get('win_prob', 0.5)

                        conf = ml_pred.get('confidence', 'MEDIUM')

                        r_hat = ml_pred.get('r_hat', 0)

                        setup['ml_prediction'] = ml_pred

                        setup['ml_trade_id']   = ml_trade_id

                        if conf == 'AVOID':

                            skip_reasons.append(f"{name}: ML AVOID ({wp:.0%} win_prob)")

                            logger.info(f"ML gate BLOCKED {name}: {conf} win_prob={wp:.0%}")

                            continue

                        if conf == 'HIGH':

                            setup['ml_lot_boost'] = 1.25

                        logger.info(f"ML gate PASS {name}: {conf} win_prob={wp:.0%} R_hat={r_hat:+.2f}")

                except Exception as _ml_err:

                    logger.debug(f"ML gate error (fail open): {_ml_err}")



            # Gate: close must be inside FVG zone; also sets live price as entry

            setup = _apply_live_entry(setup, df, fyers=fyers_instance, symbol=symbol)

            if setup is None:

                skip_reasons.append(f"{name}: LTP outside FVG zone at entry time")

                logger.info(f"SB stale {name}: LTP outside FVG zone - skipping")

                continue



            option_info = setup.get('option_info')

            try:

                from trader.order_manager import place_silver_bullet_trade, place_futures_trade

                # Attach raw candle DataFrame for CNN/RNN training

                setup['_candles_df'] = df

                # Ã¢"â¬Ã¢"â¬ Futures trade (primary - matches backtest data) Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

                place_futures_trade(fyers_instance, setup, paper_mode=False)

                # Ã¢"â¬Ã¢"â¬ Options trade (secondary - if strike available) Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

                if option_info:

                    place_silver_bullet_trade(

                        fyers_instance, setup, option_info, paper_mode=False

                    )

                else:

                    logger.info(f"SB {name}: no option strike - futures trade only")

            except Exception as oe:

                logger.error(f"Order manager error: {oe}")



        except Exception as e:

            logger.error(f"Silver Bullet scan error {name}: {e}")



    if skip_reasons:

        logger.info(f"SB scan done | window={window_name} | setups={found} | skips={len(skip_reasons)}: {'; '.join(skip_reasons[:4])}")





_sb_scan_running = False   # re-entrancy guard — skip if previous scan still active

def _sb_repeating_scan():

    """Called every 15s by the repeating scheduler. Skips if previous run still active."""

    global _sb_scan_running
    if _sb_scan_running:
        return
    _sb_scan_running = True
    try:
        run_silver_bullet_scan()
    finally:
        _sb_scan_running = False





#  Â¢" âÂ Â¢" âÂ¬ Continuous NIFTY live tracker  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

# _live_alerted  : zones where a trade has already been ENTERED today

# _watch_alerted : zones where a WATCH alert has been sent (price not at FVG yet)

#                  kept separate so the scanner re-checks every cycle until price arrives

import threading as _threading

_nse_dedup_lock: _threading.Lock = _threading.Lock()  # guards _live_alerted + _sb_daily_taken across concurrent scanner threads

_live_alerted:  set = set()

_watch_alerted: set = set()



_LIVE_INSTRUMENTS = None   # populated on first call via get_active_futures()



_live_scan_running = False   # re-entrancy guard — skip if previous scan still active

def _nifty_live_scanner():

    """

    Runs every 60s ALL market hours  -- not gated by SB window.

    Scans NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY for the full ICT chain:

    DOL -> MSS -> displaced FVG -> price at FVG.

    Fires alert + paper trade when chain is complete.

    Dedup: same FVG zone on same symbol+direction won't alert twice in a session.

    """

    global _live_scan_running
    if _live_scan_running:
        return
    _live_scan_running = True

    try:
      _nifty_live_scanner_inner()
    finally:
      _live_scan_running = False


def _nifty_live_scanner_inner():
    if _emergency_stop_active():

        logger.warning("EMERGENCY_STOP.flag detected - live scanner skipped")

        return



    global fyers_instance, _live_alerted, _watch_alerted, _LIVE_INSTRUMENTS



    if not fyers_instance:

        return



    try:

        from utils.market_hours import is_market_open

        if not is_market_open():

            return



        # SAFE_VALIDATION_REVALIDATE_AUTO: process ARMED signals from previous cycle.

        _process_armed_revalidate_auto_signals()



        from scanner.silver_bullet import scan_silver_bullet, format_sb_alert

        from scanner.index_futures  import get_active_futures

        from scanner.data_fetcher   import get_historical_data

        from data.pattern_library   import compute_trade_confidence

        import datetime as _dt, pytz as _pytz



        # Build instruments map once

        if _LIVE_INSTRUMENTS is None:

            f = get_active_futures()

            _LIVE_INSTRUMENTS = {

                f['NIFTY']      : 'NIFTY 50',

                f['BANKNIFTY']  : 'BANK NIFTY',

                f['FINNIFTY']   : 'FIN NIFTY',

                f['MIDCPNIFTY'] : 'MIDCAP NIFTY',

            }



        today = _dt.datetime.now().strftime('%Y-%m-%d')

        # Flush yesterday's dedup entries

        _live_alerted  = {k for k in _live_alerted  if k[0] == today}

        _watch_alerted = {k for k in _watch_alerted if k[0] == today}



        # MIDCPNIFTY gap recovery: replay last known LTP for any symbol silent > 45s.

        # Runs every scan cycle before bar fetches so the tick cache is never stale.

        if os.getenv('TRUEDATA_LIVE_ENABLED', 'false').lower() == 'true':

            try:

                from data.truedata_feed import get_manager as _td_mgr

                _td_mgr().forward_fill_midcpnifty()

            except Exception:

                pass



        for symbol, name in _LIVE_INSTRUMENTS.items():

            try:

                # Use 3-min for entry precision, force=True bypasses window gate

                df = get_historical_data(fyers_instance, symbol, '3', days=3)

                if df is None or len(df) < 30:

                    continue

                # Inject live tick into last bar — 15s scan cadence means bars
                # may be up to 3 min old; tick makes last-bar close/high/low current.

                from scanner.data_fetcher import inject_live_tick as _inj
                df = _inj(df, symbol)



                setup = scan_silver_bullet(df, symbol, tf='3',

                                           fyers=fyers_instance, force=True)

                if not setup:

                    continue



                _trace("SIGNAL_CREATED", symbol,

                       dir=setup.get('direction', '?'),

                       score=setup.get('confluence', '?'),

                       tf='3min', mode=EXECUTION_MODE)



                direction = setup.get('direction', '')

                fvg       = setup.get('fvg', {})

                # Round FVG low to nearest 50 pts - matches periodic scanner dedup granularity

                fvg_key   = round(fvg.get('fvg_low', 0) / 50) * 50

                dedup_key = (today, symbol, direction, fvg_key)



                # Atomic check-and-claim: claim the zone inside a single lock
                # acquisition so no concurrent thread can pass the same check.
                # If the trade is rejected downstream (score, ML, price outside
                # FVG), we roll back to watch state so the next scan cycle can
                # retry when conditions change.
                with _nse_dedup_lock:

                    if dedup_key in _live_alerted or dedup_key in _sb_daily_taken:

                        continue   # trade already entered for this zone today (either scanner)

                    # Claim the zone immediately.  Roll back below if LTP is outside FVG.
                    _live_alerted.add(dedup_key)

                    _sb_daily_taken.add(dedup_key)



                score = setup.get('confluence', 0)

                setup['timeframe']       = '3min'

                setup['instrument_type'] = 'INDEX'



                # Ã¢"â¬Ã¢"â¬ CB6 Quality Gate Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

                # Unified score gate: 12 inside SB windows, 14 outside.

                # No-OB setups previously required scoreÃ¢â°Â¥15 - lowered to 12:

                #   ML backtest: no-OB WR=64.3% PF=4.45 (above 56% target).

                #   Old gate of 15 was blocking ~70% of valid setups Ã¢â ' 0 trades.

                # Note: CHOPPY hard-block is already inside scan_silver_bullet()

                _ist_now  = _dt.datetime.now(_pytz.timezone('Asia/Kolkata'))

                _cur_min  = _ist_now.hour * 60 + _ist_now.minute

                _in_pm_sb    = 13*60 <= _cur_min < 14*60

                _in_am_sb    = 10*60 <= _cur_min < 11*60

                _in_close_sb = 15*60 <= _cur_min < 15*60+30

                _in_any_sb = _in_pm_sb or _in_am_sb or _in_close_sb



                # Inside SB windows: scoreÃ¢â°Â¥12 required.

                # Outside SB windows: scoreÃ¢â°Â¥14 required (rarer, need stronger setup).

                _strict_gate = 12 if _in_any_sb else 14

                # ── Regime gate (Phase 3.5) ────────────────────────────────
                try:
                    from utils.market_intelligence import MarketIntelligence as _MINSE
                    from utils.regime_gate import evaluate as _rg_eval
                    _mi_nse  = _MINSE()
                    _idx_sym = name.split(':')[-1].split('-')[0]  # NIFTY50, NIFTYBANK, etc.
                    _fyers_sym = f"NSE:{'NIFTY50' if _idx_sym == 'NIFTY' else _idx_sym}-INDEX"
                    _r1h = _mi_nse.get_regime("NSE", _fyers_sym, "1h")
                    _rg  = _rg_eval(_r1h.regime, _r1h.volatility, setup.get("direction", ""))
                    if not _rg.allowed:
                        logger.info(f"NSE regime block {name}: {_rg.block_reason}")
                        continue
                    _strict_gate += _rg.score_boost
                    setup["market_regime"]     = _r1h.regime
                    setup["volatility_regime"] = _r1h.volatility
                    setup["regime_lot_mult"]   = _rg.lot_multiplier
                    if _rg.note and "no adj" not in _rg.note:
                        logger.info(f"NSE regime {name}: {_rg.note} → gate={_strict_gate}")
                except Exception:
                    pass  # never block on regime errors



                if score < _strict_gate:

                    logger.info(

                        f"CB6 gate skip {name}: score {score} < "

                        f"{'SB-window' if _in_any_sb else 'non-window'} gate {_strict_gate}"

                    )

                    continue



                # Tag window mode for alert display

                if _in_pm_sb:

                    setup['window_mode'] = 'PRIMARY'    # 13:30 gold zone

                elif _in_am_sb:

                    setup['window_mode'] = 'NORMAL'     # 10:00 standard

                else:

                    setup['window_mode'] = 'STRICT'     # outside window, rare



                # Pattern confidence

                from data.pattern_library import SCORE_GATE_HIGH

                conf = compute_trade_confidence(setup)

                setup['pattern_confidence'] = conf



                score_gate   = conf.get('score_gate', SCORE_GATE_HIGH)

                should_trade = conf.get('should_trade', False)

                decision     = conf.get('decision', '')



                _now = _dt.datetime.now(_pytz.timezone('Asia/Kolkata')).strftime('%H:%M')

                from scanner.silver_bullet import is_silver_bullet_window

                in_win, win_name = is_silver_bullet_window()



                if score < score_gate:

                    logger.info(f"Live skip {name}: score {score} < gate {score_gate} | {decision}")

                    continue



                if not should_trade:

                    _trace("EXECUTION_BLOCKED", symbol,

                           gate="PATTERN_LIB", reason=decision[:80].replace(" ", "_"),

                           score=score, mode=EXECUTION_MODE)

                    logger.info(f"Live alert-only {name}: {decision}")

                    continue



                # Ã¢"â¬Ã¢"â¬ ML Gate Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

                if ML_GATE_NSE:

                    try:

                        import uuid, numpy as np

                        from ml.predictor import predict_nse

                        ml_trade_id = str(uuid.uuid4())[:8]

                        candles_np = None

                        try:

                            candles_np = df[['open','high','low','close','volume']].tail(50).values.astype(float)

                        except Exception:

                            pass

                        idx_name = 'NIFTY'

                        for _idx in ('BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY'):

                            if _idx in symbol.upper():

                                idx_name = _idx

                                break

                        ml_pred = predict_nse(ml_trade_id, setup, candles=candles_np, index_name=idx_name, gate_only=True)

                        if ml_pred:

                            wp    = ml_pred.get('win_prob', 0.5)

                            conf  = ml_pred.get('confidence', 'MEDIUM')

                            r_hat = ml_pred.get('r_hat', 0)

                            setup['ml_prediction'] = ml_pred

                            setup['ml_trade_id']   = ml_trade_id

                            if conf == 'AVOID':

                                logger.info(f"ML gate BLOCKED {name}: {conf} win_prob={wp:.0%}")

                                continue

                            if conf == 'HIGH':

                                setup['ml_lot_boost'] = 1.25

                            logger.info(f"ML gate PASS {name}: {conf} win_prob={wp:.0%} R_hat={r_hat:+.2f}")

                    except Exception as _ml_err:

                        logger.debug(f"ML gate error (fail open): {_ml_err}")



                _trace("SIGNAL_VALIDATED", symbol,

                       dir=direction, score=score, mode=EXECUTION_MODE)



                #  Â¢" âÂ Â¢" âÂ¬ Live price gate  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

                # Save FVG info before _apply_live_entry may return None

                fvg_zone  = setup.get('fvg', {})

                fvg_low_v = fvg_zone.get('fvg_low', 0)

                fvg_hi_v  = fvg_zone.get('fvg_high', 0)



                setup_live = _apply_live_entry(setup, df, fyers=fyers_instance, symbol=symbol)



                if setup_live is None:

                    # Price is outside FVG — roll back the claim so the next
                    # scan cycle can retry when price enters the zone.
                    with _nse_dedup_lock:

                        _live_alerted.discard(dedup_key)

                        _sb_daily_taken.discard(dedup_key)

                        _watch_alerted.add(dedup_key)

                    _trace("EXECUTION_BLOCKED", symbol,

                           gate="LTP_OUTSIDE_FVG",

                           reason=f"ltp_not_in_{fvg_low_v}_{fvg_hi_v}", mode=EXECUTION_MODE)

                    logger.info(f"Watching {name}: LTP outside FVG {fvg_low_v}-{fvg_hi_v}")

                    continue



                setup = setup_live

                _trace("RISK_APPROVED", symbol,

                       dir=direction, fvg_low=fvg_low_v, fvg_high=fvg_hi_v, mode=EXECUTION_MODE)

                # Dedup sets already updated at check-time above — no second write needed.

                with _nse_dedup_lock:

                    _watch_alerted.discard(dedup_key)



                option_info = setup.get('option_info')

                try:

                    if _safe_revalidate_auto_enabled():

                        from utils.execution_validation import (

                            create_signal,

                            patch_signal,

                            SIGNAL_WAITING_CONFIRM,

                            SIGNAL_ARMED,

                        )

                        live_ltp = setup.get('entry_signal', {}).get('entry')

                        signal = create_signal(

                            setup=setup,

                            current_ltp=float(live_ltp),

                            config=_EXECUTION_VALIDATION_CONFIG,

                        )

                        st = signal.get('state')

                        reason = signal.get('status_reason', '')

                        sid = signal.get('signal_id')

                        if st == SIGNAL_WAITING_CONFIRM and sid:

                            patch_signal(

                                sid,

                                {'armed_at': datetime.utcnow().isoformat(), 'mode': EXECUTION_MODE},

                                state_value=SIGNAL_ARMED,

                                reason="AWAITING_REVALIDATION_CYCLE",

                            )

                            logger.info(

                                f"[SAFE_REVALIDATE_AUTO][ARMED] Signal_ID: {sid} | "

                                "Awaiting one sequence cycle before auto revalidation."

                            )

                        else:

                            logger.warning(

                                f"[SAFE_REVALIDATE_AUTO][BLOCKED] Signal_ID: {sid} | "

                                f"Block_Reason: {reason}"

                            )

                    elif _safe_mode_enabled():

                        from utils.execution_validation import create_signal, SIGNAL_WAITING_CONFIRM

                        live_ltp = setup.get('entry_signal', {}).get('entry')

                        signal = create_signal(

                            setup=setup,

                            current_ltp=float(live_ltp),

                            config=_EXECUTION_VALIDATION_CONFIG,

                        )

                        st = signal.get('state')

                        reason = signal.get('status_reason', '')

                        if st == SIGNAL_WAITING_CONFIRM:

                            send_message(_build_signal_approval_message(signal))

                            logger.info(

                                f"Signal {signal.get('signal_id')} armed and waiting manual approval "

                                f"| {signal.get('symbol')} {signal.get('direction')}"

                            )

                        else:

                            logger.info(

                                f"Signal {signal.get('signal_id')} blocked by validation "

                                f"| state={st} reason={reason}"

                            )

                    else:

                        from trader.order_manager import place_silver_bullet_trade, place_futures_trade

                        from scanner.capital_router import route_trade, format_routing_alert



                        # â?"â?" Dynamic Capital Routing: Futures vs Options â?"â?"â?"â?"â?"â?"â?"â?"â?"â?"â?"â?"â?"â?"

                        # Condition A: available margin >= futures margin -> Futures

                        # Condition B: insufficient margin -> Options Selector


                        # ── ML Capital Allocation Gate ──────────────────────────────────────────
                        # Runs BEFORE routing so blocked signals never touch route_trade().
                        #
                        # Fail-safety contract (per spec):
                        #   live mode (PAPER_MODE unset/false): exception → block, never fail-open
                        #   paper mode (PAPER_MODE=true): exception → 1-lot pass for test continuity
                        #   sl_pts=0: always blocked, regardless of mode
                        #   risk_amount > Rs500: clamped after lot calculation (belt-and-suspenders)
                        #   allocation_pct > 50%: clamped inside allocator + re-enforced here
                        #   free capital < 1-lot margin: allocator blocks via min_margin_per_lot
                        #   every decision (pass + block): logged to TradeVerifier audit trail

                        # Compute SL distance first — zero SL is a hard block in all modes
                        _entry_px_al = float(setup.get('entry_signal', {}).get('entry', 0) or 0)
                        _sl_px_al    = float(setup.get('entry_signal', {}).get('stop_loss', 0) or 0)
                        _sl_pts_al   = abs(_entry_px_al - _sl_px_al)

                        if _sl_pts_al <= 0:
                            logger.warning(
                                f"ML alloc BLOCKED {name}: sl_pts=0 — hard block (SL equals entry or missing)"
                            )
                            _trace("EXECUTION_BLOCKED", symbol,
                                   gate="ML_ALLOC_SL_ZERO",
                                   reason="sl_pts_zero",
                                   mode=EXECUTION_MODE)
                            try:
                                from utils.trade_verifier import get_verifier, VFlag
                                get_verifier().record_alloc_decision(
                                    {"blocked": True, "block_reason": "sl_pts=0"},
                                    symbol=symbol, direction=direction, mode=EXECUTION_MODE,
                                    signal_score=score, sl_pts=0.0, lots_decided=0,
                                )
                            except Exception:
                                pass
                            continue

                        _paper_mode_al = os.getenv("PAPER_MODE", "false").strip().lower() == "true"

                        # Identify index + lot size + min margin for 1 lot
                        try:
                            from scanner.index_futures import get_lot_size as _get_ls
                            from scanner.capital_router import (
                                get_futures_margin_required as _gmr,
                                get_available_margin        as _gam,
                            )
                            _idx_name_al  = next(
                                (k for k in ('BANKNIFTY', 'MIDCPNIFTY', 'FINNIFTY', 'NIFTY')
                                 if k in symbol.upper()), 'NIFTY')
                            _lot_sz_al    = _get_ls(_idx_name_al) or 65
                            _min_margin   = _gmr(_idx_name_al, 1)
                        except Exception:
                            _idx_name_al  = 'NIFTY'
                            _lot_sz_al    = 65
                            _min_margin   = 0.0

                        # Load account state
                        try:
                            from trader.paper_trader import load_state as _load_ps
                            _ps_al = _load_ps()
                        except Exception:
                            _ps_al = {}

                        # Call the safe wrapper (handles exceptions per paper_mode contract)
                        from utils.ml_capital_allocator import safe_calculate_alloc
                        _ml_alloc = safe_calculate_alloc(
                            signal             = setup,
                            memory             = setup.get('pattern_confidence', {}),
                            account_state      = _ps_al,
                            paper_mode         = _paper_mode_al,
                            min_margin_per_lot = _min_margin,
                        )
                        setup['ml_alloc'] = _ml_alloc

                        if _ml_alloc['blocked']:
                            _blk_reason = _ml_alloc.get('block_reason', 'ALLOC_BLOCKED')
                            logger.info(f"ML alloc BLOCKED {name}: {_blk_reason}")
                            _trace("EXECUTION_BLOCKED", symbol,
                                   gate="ML_ALLOC",
                                   reason=_blk_reason[:60].replace(" ", "_"),
                                   mode=EXECUTION_MODE)
                            try:
                                from utils.trade_verifier import get_verifier
                                get_verifier().record_alloc_decision(
                                    _ml_alloc,
                                    symbol=symbol, direction=direction, mode=EXECUTION_MODE,
                                    signal_score=score, sl_pts=_sl_pts_al, lots_decided=0,
                                    paper_mode=_paper_mode_al,
                                )
                            except Exception:
                                pass
                            continue

                        # ── Lot calculation from risk budget ──────────────────────────────────
                        _ml_lots = 1
                        _risk_per_lot_al = _lot_sz_al * _sl_pts_al
                        if _risk_per_lot_al > 500.0:
                            logger.warning(
                                f"ML alloc BLOCKED {name}: 1 lot risk Rs{_risk_per_lot_al:.0f} "
                                f"> Rs500 cap"
                            )
                            _trace("EXECUTION_BLOCKED", symbol,
                                   gate="ML_ALLOC_RISK_PER_LOT",
                                   reason="one_lot_exceeds_500",
                                   mode=EXECUTION_MODE)
                            try:
                                from utils.trade_verifier import get_verifier
                                get_verifier().record_alloc_decision(
                                    {"blocked": True, "block_reason": "1_lot_risk_exceeds_500"},
                                    symbol=symbol, direction=direction, mode=EXECUTION_MODE,
                                    signal_score=score, sl_pts=_sl_pts_al, lots_decided=0,
                                    paper_mode=_paper_mode_al,
                                )
                            except Exception:
                                pass
                            continue
                        if _ml_alloc['risk_amount'] > 0 and _sl_pts_al > 0:
                            _ml_lots = max(1, int(_ml_alloc['risk_amount'] / (_lot_sz_al * _sl_pts_al)))

                        # Belt-and-suspenders: actual risk after lot rounding must not exceed Rs500
                        _actual_risk_al = _ml_lots * _lot_sz_al * _sl_pts_al
                        if _actual_risk_al > 500.0:
                            _ml_lots = int(500.0 / (_lot_sz_al * _sl_pts_al))
                            if _ml_lots <= 0:
                                logger.warning(
                                    f"ML alloc BLOCKED {name}: risk clamp produced 0 lots "
                                    f"(risk/lot=Rs{_risk_per_lot_al:.0f})"
                                )
                                _trace("EXECUTION_BLOCKED", symbol,
                                       gate="ML_ALLOC_RISK_CLAMP",
                                       reason="zero_lots_after_clamp",
                                       mode=EXECUTION_MODE)
                                try:
                                    from utils.trade_verifier import get_verifier
                                    get_verifier().record_alloc_decision(
                                        {"blocked": True, "block_reason": "zero_lots_after_risk_clamp"},
                                        symbol=symbol, direction=direction, mode=EXECUTION_MODE,
                                        signal_score=score, sl_pts=_sl_pts_al, lots_decided=0,
                                        paper_mode=_paper_mode_al,
                                    )
                                except Exception:
                                    pass
                                continue
                            _actual_risk_al = _ml_lots * _lot_sz_al * _sl_pts_al
                            logger.info(
                                f"ML alloc risk-clamped {name}: "
                                f"lots reduced to {_ml_lots} (actual_risk=Rs{_actual_risk_al:.0f})"
                            )

                        # Margin feasibility: reduce lots if broker margin is insufficient
                        try:
                            _avail_margin_al = _gam(fyers_instance)
                            if _avail_margin_al is not None and _avail_margin_al > 0:
                                _margin_per_lot_al = _gmr(_idx_name_al, 1)
                                _max_lots_margin   = max(1, int(_avail_margin_al / _margin_per_lot_al))
                                if _ml_lots > _max_lots_margin:
                                    logger.info(
                                        f"ML alloc margin-capped {name}: "
                                        f"lots {_ml_lots}→{_max_lots_margin} "
                                        f"(avail=Rs{_avail_margin_al:,.0f} "
                                        f"req/lot=Rs{_margin_per_lot_al:,.0f})"
                                    )
                                    _ml_lots = _max_lots_margin
                        except Exception:
                            pass

                        _actual_risk_al = _ml_lots * _lot_sz_al * _sl_pts_al
                        setup['ml_lots'] = _ml_lots

                        logger.info(
                            f"ML alloc PASS {name}: "
                            f"band={_ml_alloc['reason'].split('|')[0].strip()} "
                            f"risk=Rs{_actual_risk_al:.0f} "
                            f"capital=Rs{_ml_alloc['capital_to_use']:.0f} "
                            f"lots={_ml_lots} conf={_ml_alloc['confidence']:.2f} "
                            f"paper={_paper_mode_al}"
                        )

                        try:
                            from utils.trade_verifier import get_verifier
                            get_verifier().record_alloc_decision(
                                _ml_alloc,
                                symbol=symbol, direction=direction, mode=EXECUTION_MODE,
                                signal_score=score, sl_pts=_sl_pts_al, lots_decided=_ml_lots,
                                paper_mode=_paper_mode_al,
                            )
                        except Exception:
                            pass
                        # ── End ML Capital Allocation Gate ────────────────────────────────────

                        routing = route_trade(setup, fyers_instance, lots=_ml_lots)

                        _trace("ROUTER_APPROVED", symbol,

                               route=routing['route'],

                               reason=routing.get('reason', '?'),

                               dir=direction, mode=EXECUTION_MODE)

                        logger.info(

                            f"Capital routing [{name}]: {routing['route']} "

                            f"({routing['reason']}) margin "

                            f"Rs{routing.get('margin_avail') or 0:,.0f}"

                        )

                        try:

                            from communications.telegram_bot import send_message as _send_tg

                            _send_tg(format_routing_alert(routing))

                        except Exception:

                            pass



                        if routing['route'] == 'FUTURES':

                            _trace("ORDER_BUILD_STARTED", symbol,

                                   dir=direction, order_type='FUTURES', mode=EXECUTION_MODE)

                            place_futures_trade(fyers_instance, setup, paper_mode=False)

                        else:

                            opt = routing.get('option')

                            if opt and opt.get('symbol'):

                                setup['option_info'] = opt

                                _trace("ORDER_BUILD_STARTED", symbol,

                                       dir=direction, order_type='OPTIONS',

                                       option=opt.get('symbol', '?'), mode=EXECUTION_MODE)

                                place_silver_bullet_trade(fyers_instance, setup,

                                                          opt, paper_mode=False)

                            else:

                                logger.warning(

                                    f"capital_router [{name}]: OPTIONS route but "

                                    f"no valid contract -- falling back to futures"

                                )

                                _trace("ORDER_BUILD_STARTED", symbol,

                                       dir=direction, order_type='FUTURES_FALLBACK', mode=EXECUTION_MODE)

                                place_futures_trade(fyers_instance, setup, paper_mode=False)

                except Exception as oe:

                    logger.error(f"Order manager/validation error for {name}: {oe}")



            except Exception as e:

                logger.error(f"Live scanner error {name}: {e}")



    except Exception as e:

        logger.error(f"Nifty live scanner error: {e}")





def _trade_monitor():

    """Called every 3 min -- updates SL/TP state. Telegram only on SL/TP events."""

    # REQ-3: Emergency stop - halt monitor cycle immediately if flag is active

    if _emergency_stop_active():

        logger.warning("EMERGENCY_STOP.flag active - _trade_monitor cycle skipped")

        return



    global fyers_instance

    if not fyers_instance:

        return

    try:

        from utils.market_hours import is_market_open

        if not is_market_open():

            return



        from trader.paper_trader import load_state

        if not load_state().get('open_trades'):

            return



        update_paper_trades(fyers_instance)



    except Exception as e:

        logger.error(f"Trade monitor error: {e}")





def run_nifty_scan():

    logger.info("run_nifty_scan: ICT index scanner disabled (SB-only mode)")





def build_morning_watchlist():

    logger.info("build_morning_watchlist: disabled (SB-only mode)")







def send_eod_report():

    """15:30 IST: comprehensive EOD report — all accounts + ML + Hermes — saved as .txt and sent to both Telegram bots."""

    try:

        from utils.eod_report import generate_and_send

        fpath = generate_and_send(trigger='NSE_CLOSE')

        logger.info(f"EOD report sent: {fpath}")

    except Exception as e:

        logger.error(f"EOD report error: {e}")

        # Fallback: send a plain alert so Telegram at least notifies

        try:

            send_message(f"CB6 QUANTUM — EOD report failed to generate: {e}\nCheck logs.")

        except Exception:

            pass





def send_morning_briefing():

    """9:15am daily: FII/DII bias + memory stats."""

    try:

        from data.fii_dii import get_market_bias_from_fii_dii

        from data.bot_memory import load_memory

        from scanner.silver_bullet import get_window_status



        fii_bias, _ = get_market_bias_from_fii_dii()

        mem   = load_memory()

        total = mem.get('total_trades', 0)

        wins  = mem.get('winning_trades', 0)

        wr    = round(wins / total * 100, 1) if total > 0 else 0



        send_message(

            "CB6 QUANTUM - MORNING BRIEFING\n\n"

            f"Date    : {datetime.now().strftime('%d %b %Y')}\n"

            f"FII/DII : {fii_bias}\n\n"

            "SILVER BULLET WINDOWS TODAY:\n"

            "10:00  -- 11:00 IST (Morning)\n"

            "13:30  -- 14:30 IST (Afternoon)\n\n"

            f"AI Memory: {total} trades | WR: {wr}%\n"

            f"SB Window : {get_window_status()}"

        )

        logger.info("Morning briefing sent")

    except Exception as e:

        logger.error(f"Morning briefing error: {e}")





def send_weekly_report():

    """Sunday 6pm: weekly performance + per-trade ICT logic recap."""

    try:

        from trader.paper_trader import load_state

        from data.bot_memory     import load_memory

        from datetime            import timedelta



        state  = load_state()

        closed = state.get('closed_trades', [])

        cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        week   = [t for t in closed if t.get('exit_time', '')[:10] >= cutoff]



        total  = len(week)

        wins   = sum(1 for t in week if t.get('pnl', 0) > 0)

        pnl    = sum(t.get('pnl', 0) for t in week)

        wr     = round(wins / total * 100, 1) if total > 0 else 0

        avg_r  = round(

            sum(

                (t.get('pnl', 0) / max(t.get('quantity', 1) * t.get('risk', 1), 1))

                for t in week

            ) / max(total, 1), 2

        )



        # Per-trade ICT logic recap

        trade_logic = []

        for t in week[-10:]:   # last 10 trades of the week

            sym       = t['symbol'].replace('NSE:', '').replace('-EQ', '')

            direction = t.get('direction', 'BUY')

            score     = t.get('confluence', '?')

            reason    = t.get('status', '?')

            pnl_t     = t.get('pnl', 0)

            tgts      = ', '.join(t.get('targets_hit', [])) or 'none'

            in_ote    = 'YES' if t.get('in_ote') else 'NO'

            in_fvg    = 'YES' if t.get('in_fvg') else 'NO'

            res_emoji = '+' if pnl_t > 0 else '-'

            trade_logic.append(

                f"\n[{res_emoji}] {sym} {direction} | Score {score}/10\n"

                f"   Entry: {t.get('entry_price', '?')} -> Exit: {t.get('exit_price', '?')}\n"

                f"   SL: {t.get('stop_loss', '?')} | Targets hit: {tgts}\n"

                f"   OTE: {in_ote} FVG: {in_fvg} | Status: {reason}\n"

                f"   PnL: Rs {pnl_t:.0f}"

            )



        # Best/worst by P&L

        sorted_trades = sorted(week, key=lambda t: t.get('pnl', 0), reverse=True)

        best  = sorted_trades[0] if sorted_trades else None

        worst = sorted_trades[-1] if sorted_trades else None



        mem    = load_memory()

        params = mem.get('learned_params', {})

        all_wr = round(

            mem.get('winning_trades', 0) /

            max(mem.get('total_trades', 1), 1) * 100, 1

        )



        # Send in two parts to avoid Telegram message length limits

        msg1 = (

            "CB6 QUANTUM - WEEKLY REPORT (1/2)\n\n"

            f"Week     : {(datetime.now() - timedelta(days=7)).strftime('%d %b')}"

            f" - {datetime.now().strftime('%d %b %Y')}\n\n"

            f"Trades   : {total}\n"

            f"Wins     : {wins}  Losses: {total - wins}\n"

            f"Win Rate : {wr}%  (need 56%+ for live)\n"

            f"Avg R    : {avg_r}R\n"

            f"Net P&L  : Rs {pnl:.0f}\n\n"

        )

        if best and worst:

            bs = best['symbol'].replace('NSE:', '').replace('-EQ', '')

            ws = worst['symbol'].replace('NSE:', '').replace('-EQ', '')

            msg1 += (

                f"BEST  : {bs} Rs {best.get('pnl', 0):.0f} (Score {best.get('confluence', '?')})\n"

                f"WORST : {ws} Rs {worst.get('pnl', 0):.0f} (Score {worst.get('confluence', '?')})\n\n"

            )

        msg1 += (

            "AI LEARNED:\n"

            f"Best  : {', '.join(params.get('best_stocks', [])[:3]) or 'Learning...'}\n"

            f"Avoid : {', '.join(params.get('avoid_stocks', [])[:3]) or 'None'}\n\n"

            f"All-time: {mem.get('total_trades', 0)} trades | WR: {all_wr}%\n\n"

            "Validation gate: WR >= 56% before going live."

        )

        send_message(msg1)



        if trade_logic:

            msg2 = (

                "CB6 QUANTUM - WEEKLY REPORT (2/2)\n"

                "TRADE-BY-TRADE LOGIC\n"

                + "".join(trade_logic)

                + "\n\nReview which setups won and which failed.\n"

                "Pattern recognition = the only edge."

            )

            send_message(msg2)



        logger.info("Weekly report sent")

    except Exception as e:

        logger.error(f"Weekly report error: {e}")





def main():

    global fyers_instance



    logger.info("=" * 45)

    logger.info("CB6 QUANTUM - ICT Buy + Sell + AI Mode")

    logger.info(f"Capital  : Rs {CAPITAL}")

    logger.info("Mode     : LIVE TRADING (Fyers ILRAADDBFV-200)")

    logger.info(f"ExecMode : {EXECUTION_MODE}")

    if EXECUTION_MODE == "LEGACY":

        logger.warning(

            "EXECUTION_MODE=LEGACY — signal-age, drift, and RR revalidation are DISABLED. "

            "Set EXECUTION_MODE=SAFE_VALIDATION_REVALIDATE_AUTO in .env to enable full safety validation."

        )



    # Log timezone info for debugging

    ist_tz = pytz.timezone('Asia/Kolkata')

    now_ist = datetime.now(ist_tz)

    logger.info(f"System TZ : {time.tzname}")

    logger.info(f"IST Time  : {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    logger.info("=" * 45)

    # Backfill NSE trade journal into pattern DB (idempotent)
    try:
        from ml_engine.learning.feedback_loop import _backfill_nse_journal
        _backfill_nse_journal()
    except Exception as _e:
        logger.debug(f"Pattern DB NSE backfill skipped: {_e}")



    # Check token availability in .env

    token_from_env = _read_token_from_env()

    if not token_from_env:

        logger.error("No access token found in .env file!")

        logger.error("Run: python broker/web_token.py")

        return



    # Auto-refresh token if it's not from today

    logger.info("Checking token freshness...")

    if not is_token_fresh():

        logger.warning("Token is stale, starting refresh process...")

        if not run_token_refresh():

            logger.error("Token refresh failed after multiple attempts!")

            logger.error("Run manually: python broker/web_token.py")

            return

    else:

        logger.info("Token is fresh (issued today in IST)")



    # Reset daily paper-trade counters if date changed since last run

    try:

        reset_paper_state_if_new_day()

        logger.info("Daily paper state verified")

    except Exception as e:

        logger.error(f"Daily reset error: {e}")



    fyers_instance = initialize_fyers()



    # Share live fyers session with dashboard so backtest doesn't need a fresh token

    try:

        import dashboard as _dash

        _dash.set_fyers_for_backtest(fyers_instance)

    except Exception as _e:

        logger.debug(f"Dashboard fyers ref: {_e}")



    if not test_connection(fyers_instance):

        logger.error("Fyers connection failed!")

        return



    # Start Yahoo Finance background price feed (fallback when Fyers quote API fails)

    try:

        from data.nse_yahoo_feed import start_nse_yahoo_feed

        start_nse_yahoo_feed()

        logger.info("NSE Yahoo price feed started (background)")

    except Exception as _e:

        logger.warning(f"NSE Yahoo feed failed to start: {_e}")



    # ── TrueData WebSocket live feed (primary LTP / tick source) ──────────────────
    # Guarded by ENABLE_TRUEDATA_LIVE=true in .env.
    # If TrueData WS fails at any point, Fyers quotes + Yahoo remain untouched.
    _TD_LIVE_SYMBOLS = ['NIFTY-I', 'BANKNIFTY-I', 'FINNIFTY-I', 'MIDCPNIFTY-I']
    _TD_FYERS_KEYS   = [
        'NSE:NIFTY50-FUT', 'NSE:NIFTYBANK-FUT',
        'NSE:FINNIFTY-FUT', 'NSE:MIDCPNIFTY-FUT',
    ]
    try:
        from dotenv import dotenv_values as _dv
        _td_flag = _dv(os.path.join(os.path.dirname(__file__), '.env')).get(
            'ENABLE_TRUEDATA_LIVE', 'false'
        )
        _td_live_enabled = str(_td_flag).lower().strip() == 'true'
    except Exception:
        _td_live_enabled = False

    if _td_live_enabled:
        logger.info('TRUEDATA_LIVE_ENABLED — wiring TrueData WebSocket live feed')
        try:
            from scanner.websocket_feed import init_truedata as _td_ws_init
            if _td_ws_init(_TD_LIVE_SYMBOLS):
                logger.info('TRUEDATA_WS_STARTED — live ticks active: %s', _TD_LIVE_SYMBOLS)

                def _verify_truedata_ticks():
                    """
                    Once market opens, waits 60 s then checks _tick_cache for each
                    TrueData symbol every 5 min. Missing symbols are flagged but
                    Fyers quotes + Yahoo continue to cover the gap silently.
                    """
                    import time as _t
                    from utils.market_hours import is_market_open
                    from scanner import websocket_feed as _wsf
                    while not is_market_open():
                        _t.sleep(30)
                    _t.sleep(60)
                    while True:
                        try:
                            with _wsf._lock:
                                snap = dict(_wsf._tick_cache)
                            missing = []
                            for td_sym, fyers_sym in zip(_TD_LIVE_SYMBOLS, _TD_FYERS_KEYS):
                                tick = snap.get(fyers_sym) or snap.get(td_sym)
                                if tick:
                                    logger.info(
                                        'TRUEDATA_TICK_OK  %-16s ltp=%.2f',
                                        td_sym, tick.get('ltp', 0),
                                    )
                                else:
                                    missing.append(td_sym)
                                    logger.warning(
                                        'TRUEDATA_TICK_MISSING  %s — '
                                        'no tick yet; Fyers quotes covers gap', td_sym,
                                    )
                            if missing:
                                logger.warning(
                                    'TRUEDATA_WS_PARTIAL — %d symbol(s) no ticks: %s',
                                    len(missing), missing,
                                )
                        except Exception as _ve:
                            logger.debug('TrueData tick verify error: %s', _ve)
                        _t.sleep(300)

                threading.Thread(
                    target=_verify_truedata_ticks, daemon=True, name='TD-TickVerify'
                ).start()
            else:
                logger.warning(
                    'TRUEDATA_WS_FAILED_FALLBACK_ACTIVE — '
                    'Fyers quotes + Yahoo remain as live LTP sources'
                )
        except Exception as _td_e:
            logger.warning(
                'TRUEDATA_WS_FAILED_FALLBACK_ACTIVE — %s — '
                'Fyers quotes + Yahoo remain as live LTP sources', _td_e,
            )
    else:
        logger.info('TRUEDATA_LIVE_ENABLED=false — Fyers quotes used for live LTP')

    if not send_test_alert():

        logger.error("Telegram failed! Check .env")

        return

    # ── TrueData trial expiry warning ────────────────────────────────────────────
    # Fires via Telegram once at startup if trial is within 7 days or has lapsed.
    try:
        from datetime import date as _date
        _td_expiry = _date(2026, 6, 9)
        _days_left = (_td_expiry - _date.today()).days
        if 0 <= _days_left <= 7:
            _expiry_msg = (
                f'WARNING: TrueData trial expires in {_days_left} day(s) '
                '(2026-06-09). Upgrade to paid plan NOW — '
                'if lapsed, CB6 auto-falls back to Fyers historical + Fyers quotes.'
            )
            logger.warning(_expiry_msg)
            send_message(_expiry_msg)
        elif _days_left < 0:
            _lapsed_msg = (
                'WARNING: TrueData trial EXPIRED. '
                'CB6 running on Fyers historical + Fyers quotes (full fallback active). '
                'Set ENABLE_TRUEDATA_LIVE=false in .env until account is renewed.'
            )
            logger.warning(_lapsed_msg)
            send_message(_lapsed_msg)
    except Exception:
        pass

    # Start daily loss monitor -- closes all positions if Rs 1,000 cap is hit
    try:
        from core.daily_loss_monitor import start_daily_loss_monitor
        start_daily_loss_monitor(fyers_instance)
        logger.info('Daily loss monitor started (cap=Rs 1,000)')
    except Exception as _dlm_e:
        logger.error(f'Daily loss monitor failed to start: {_dlm_e}')







    market_status = get_market_status()

    logger.info(f"Market: {market_status}")





    total_trades = 0

    try:

        from data.bot_memory import load_memory

        memory       = load_memory()

        total_trades = memory.get('total_trades', 0)

    except Exception as e:

        logger.error(f"Memory error: {e}")



    try:

        dash_thread = threading.Thread(

            target=start_dashboard,

            args=(8080,),

            daemon=True

        )

        dash_thread.start()

        logger.info("Dashboard: http://localhost:8080")

    except Exception as e:

        logger.error(f"Dashboard error: {e}")



    # Ã¢"â¬Ã¢"â¬ WebSocket realtime feed (off by default; toggle via /ws on) Ã¢"â¬Ã¢"â¬

    try:

        from config.strategy import STRATEGY

        if STRATEGY.enable_websocket:

            from scanner.websocket_feed import init as ws_init

            # REQ-2.1: Always read token at connection time - never use the stale

            # module-level ACCESS_TOKEN that was imported before the .env was refreshed.

            _ws_token = _read_token_from_env()

            if not _ws_token:

                logger.error(

                    "WebSocket init SKIPPED - ACCESS_TOKEN missing from .env. "

                    "Run auto_token.py to refresh, then restart. Falling back to polling."

                )

            elif ws_init(_ws_token, CLIENT_ID):

                logger.info("WebSocket feed initialized  -- realtime mode ON")

                _rearm_existing_trade_triggers()

            else:

                logger.warning("WebSocket init failed  -- falling back to polling only")

        else:

            logger.info("WebSocket feed: OFF (toggle with /ws on)")

    except Exception as e:

        logger.error(f"WebSocket startup error: {e}")



    from data.pattern_library import load_library as _load_lib

    _pat_count = len(_load_lib())



    send_message(

        "CB6 QUANTUM STARTED\n\n"

        "User      : RAHUL PANCHAL\n"

        f"Capital   : Rs {CAPITAL:.0f}\n"

        "Mode      : LIVE TRADING (Fyers ILRAADDBFV-200)\n"

        "Strategy  : ICT SILVER BULLET\n"

        "Universe  : Index Futures & Options Only\n"

        "Indexes   : NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY\n"

        f"Market    : {market_status}\n"

        f"AI Memory : {total_trades} trades\n"

        f"Patterns  : {_pat_count} backtest trades loaded\n\n"

        "LIVE TRACKER:\n"

        "Scan      : Every 3 min  |  9:15  -- 15:30 IST  |  All 4 indexes\n"

        "Chain     : DOL -> MSS -> Displaced FVG -> Entry\n\n"

        "SILVER BULLET WINDOWS (priority scans):\n"

        "Morning   : 10:00 - 11:00 IST\n"

        "Afternoon : 13:30 - 14:30 IST\n\n"

        "SETUP CHAIN:\n"

        "1 Opening Gap Bias (institutional direction)\n"

        "2 Draw on Liquidity (HOD/LOD or unswept swing)\n"

        "3 Market Structure Shift (MSS)\n"

        "4 Fair Value Gap (FVG) after MSS\n"

        "5 PM Reversal: reverse from AM extreme set before 13:00\n"

        "6 Entry: first touch of FVG | SL: FVG edge\n"

        "7 T1: 1:1 | T2: 1:2 | T3: 1:3  (max score 20)\n\n"

        "COMMANDS:\n"

        "/sb             - Silver Bullet scan (index futures)\n"

        "/scan           - Index futures scan\n"

        "/check INDEX    - Check specific index (e.g. /check NIFTY)\n"

        "/trades         - Open index positions\n"

        "/portfolio      - Capital and PnL\n"

        "/levels         - NIFTY ICT levels + probability\n"

        "/options [IDX]  - ITM/SATM/OTM strikes (e.g. /options BANKNIFTY)\n"

        "/pattern        - Backtest pattern stats\n"

        "/reloadpatterns - Reload after backtest\n"

        "/brain          - Market brain report\n"

        "/fiidii         - FII/DII data\n"

        "/expiry         - F&O expiry calendar\n"

        "/ask <q>        - Ask AI about any trade\n"

        "/clearchat      - Reset AI chat history\n"

        "/stop           - Stop trading today\n"

        "/info           - All commands\n\n"

        "Dashboard: http://localhost:8080"

    )



    #  Â¢" âÂ Â¢" âÂ¬ Market Brain: morning brief at startup  Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ Â¢" âÂ¬

    try:

        from core.market_brain import morning_brief

        send_message(morning_brief(fyers_instance))

    except Exception as e:

        logger.error(f"Morning brief error: {e}")



    set_scan_callback(run_silver_bullet_scan)

    set_nifty_scan_callback(run_scan)   # /scan -> redirects to index SB scan

    set_fyers_ref(fyers_instance)

    set_signal_approval_callback(_handle_signal_approval)



    from scanner.expiry_calendar import set_fyers_client as _set_expiry_fyers

    _set_expiry_fyers(fyers_instance)



    # Schedule daily events and weekly report

    from utils.scheduler import schedule_repeating, schedule_repeating_seconds



    schedule_daily(9,  15, send_morning_briefing,    name="morning_briefing")

    schedule_daily(10,  0, run_silver_bullet_scan,   name="silver_bullet_morning")

    schedule_daily(13, 30, run_silver_bullet_scan,   name="silver_bullet_afternoon")

    schedule_daily(15, 30, send_eod_report,          name="eod_report")

    schedule_daily(15, 45, archive_trades,           name="daily_archive")

    schedule_weekly(6, 18, 0, send_weekly_report,    name="weekly_report")

    # Silver Bullet + live scanner — 60s cadence (bars cached 2 min; tick injected per-scan).
    # 15s was spawning overlapping threads faster than scans completed → thread pile-up crashes.

    schedule_repeating_seconds(15, _sb_repeating_scan,   name="sb_mid_window")

    schedule_repeating_seconds(15, _nifty_live_scanner,  name="nifty_live")

    # Live P&L + SL/TP monitor — 30s is fine, no pattern detection needed

    schedule_repeating_seconds(30, _trade_monitor,       name="trade_monitor")

    start_scheduler()



    # Ã¢"â¬Ã¢"â¬ ML auto-trainer background scheduler Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬Ã¢"â¬

    try:

        from ml.auto_trainer import start_scheduler as _ml_sched

        _ml_sched()

        logger.info("ML auto-trainer scheduler started")

    except Exception as _ml_e:

        logger.warning(f"ML scheduler start skipped: {_ml_e}")



    try:

        listener = threading.Thread(

            target=start_listening,

            daemon=True

        )

        listener.start()

        logger.info("Telegram listener started")

    except Exception as e:

        logger.error(f"Listener error: {e}")



    logger.info("=" * 45)

    logger.info("CB6 QUANTUM Fully Running!")

    logger.info("Dashboard : http://localhost:8080")

    logger.info("Universe  : Index Futures & Options Only")

    logger.info(f"Buy Score : {MIN_BUY_SCORE}+ | Sell Score: {MIN_SELL_SCORE}+ | RR 1:3 minimum")

    logger.info("=" * 45)



    # Write NSE heartbeat every 60s so orchestrator can detect hangs

    _nse_hb_file = os.path.join(os.path.dirname(__file__), 'data', 'nse_heartbeat.txt')

    def _nse_heartbeat_loop():

        while True:

            try:

                with open(_nse_hb_file, 'w') as _f:

                    _f.write(datetime.now().isoformat())

            except Exception:

                pass

            time.sleep(60)

    threading.Thread(target=_nse_heartbeat_loop, daemon=True, name="NSEHeartbeat").start()



    while True:

        time.sleep(1)





if __name__ == "__main__":

    main()









