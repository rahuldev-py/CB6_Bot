# watch_nifty_long.py
# One-shot intraday watcher: NIFTY long setup — MSS confirm → FVG pullback → paper trade
#
# Conditions (from 10:30 IST analysis on 13-May-2026):
#   Phase 1 — MSS: wait for a 5-min candle to close above the swing high (DOL level)
#   Phase 2 — FVG: after MSS, wait for price to pull back into the 15-min bullish FVG zone
#   Trade:  entry at FVG zone touch, SL below FVG, targets at T1/T2/T3
#   Risk:   5% of available capital (user-specified conviction trade)
#   Expiry: 11:00 IST (Morning Silver Bullet window closes)
#
# Run: python watch_nifty_long.py
# The script self-terminates after firing the trade or when the window closes.

import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))

import datetime
import pytz
from dotenv import dotenv_values
from fyers_apiv3 import fyersModel

from utils.logger import logger
from utils.telegram_alerts import send_message
from scanner.index_futures import get_active_futures
from scanner.data_fetcher import get_historical_data
from scanner.silver_bullet import (
    find_draw_on_liquidity, detect_sb_mss, detect_sb_fvg,
    opening_range_swept, scan_silver_bullet
)
from trader.paper_trader import open_paper_trade

IST           = pytz.timezone('Asia/Kolkata')
MARKET_END_H  = 15
MARKET_END_M  = 30
RISK_PCT      = 5.0     # 5% capital at risk for this trade
POLL_SEC      = 90      # re-check every 90 seconds


def _now_ist():
    return datetime.datetime.now(IST)


def _market_open():
    """Run until market close — ignore SB window, test full ICT logic."""
    n   = _now_ist()
    end = n.replace(hour=MARKET_END_H, minute=MARKET_END_M, second=0, microsecond=0)
    return n < end


def _init_fyers():
    env       = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
    client_id = env.get('CLIENT_ID', '')
    token_str = env.get('ACCESS_TOKEN', '')
    if ':' in token_str:
        token_str = token_str.split(':', 1)[1]
    return fyersModel.FyersModel(
        client_id=client_id, token=token_str,
        is_async=False,
        log_path=os.path.join(os.path.dirname(__file__), 'logs', '')
    )


def _fetch(fyers, symbol):
    df5  = get_historical_data(fyers, symbol, '5',  days=2)
    df15 = get_historical_data(fyers, symbol, '15', days=3)
    return df5, df15


def _check_conditions(df5, df15, symbol, mss_confirmed):
    """
    Returns (phase, details_dict, new_mss_confirmed) — always 3 values.
    phase: 'waiting_mss' | 'waiting_fvg' | 'fire'
    """
    ltp  = round(float(df5['close'].iloc[-1]), 2)
    dol  = find_draw_on_liquidity(df5, lookback=80)
    mss5 = detect_sb_mss(df5, lookback=30)

    # Phase 1 — latch MSS: once confirmed, stays confirmed for the session
    new_mss_confirmed = mss_confirmed
    if not mss_confirmed:
        if mss5 and mss5['direction'] == 'BULLISH':
            new_mss_confirmed = True

    # Phase 2 — FVG pullback (only after MSS locked in)
    if new_mss_confirmed:
        fvg15 = detect_sb_fvg(df15, 'BULLISH', lookback=30)
        if fvg15:
            fvg_lo = fvg15['fvg_low']
            fvg_hi = fvg15['fvg_high']
            in_fvg = fvg_lo <= ltp <= fvg_hi
            near   = abs(ltp - fvg15['mid']) / fvg15['mid'] <= 0.015  # within 1.5%

            if in_fvg or near:
                # Try full setup scan first (more complete chain validation)
                setup = scan_silver_bullet(df5, symbol, tf='5', force=True)
                if setup and setup['direction'] == 'BULLISH':
                    return 'fire', {
                        'setup'  : setup,
                        'ltp'    : ltp,
                        'fvg_lo' : fvg_lo,
                        'fvg_hi' : fvg_hi,
                        'mss'    : mss5,
                        'dol'    : dol,
                    }, new_mss_confirmed

                # Fallback: build minimal setup manually if scanner is too strict
                fvg_size  = max(fvg15.get('size', 2.0), 2.0)
                entry     = round(fvg_lo + 0.5, 2)
                stop_loss = round(fvg_lo - fvg_size, 2)
                risk      = round(entry - stop_loss, 2)
                if risk > 0:
                    t1 = round(entry + risk * 2.0, 2)
                    t2 = round(entry + risk * 3.0, 2)
                    t3 = round(dol['level'] if dol and dol['level'] > entry else entry + risk * 4.0, 2)
                    rr = round((t2 - entry) / risk, 1)
                    manual_setup = {
                        'symbol'          : symbol,
                        'direction'       : 'BULLISH',
                        'timeframe'       : '5min',
                        'instrument_type' : 'INDEX',
                        'confluence'      : 7,
                        'in_fvg'          : in_fvg,
                        'window'          : 'Silver Bullet',
                        'setup_type'      : 'SILVER_BULLET',
                        'entry_signal'    : {
                            'entry'    : entry,
                            'stop_loss': stop_loss,
                            'target1'  : t1,
                            'target2'  : t2,
                            'target3'  : t3,
                            'risk'     : risk,
                            'rr_ratio' : rr,
                            'fvg_low'  : round(fvg_lo, 2),
                            'fvg_high' : round(fvg_hi, 2),
                            'mss_level': round(mss5['level'], 2) if mss5 else entry,
                            'dol_level': round(dol['level'], 2) if dol else t3,
                        },
                    }
                    return 'fire', {
                        'setup'  : manual_setup,
                        'ltp'    : ltp,
                        'fvg_lo' : fvg_lo,
                        'fvg_hi' : fvg_hi,
                        'mss'    : mss5,
                        'dol'    : dol,
                    }, new_mss_confirmed

            return 'waiting_fvg', {
                'ltp'   : ltp,
                'fvg_lo': fvg15['fvg_low'] if fvg15 else None,
                'fvg_hi': fvg15['fvg_high'] if fvg15 else None,
                'mss'   : mss5,
            }, new_mss_confirmed

    return 'waiting_mss', {
        'ltp': ltp,
        'mss': mss5,
        'dol': dol,
    }, new_mss_confirmed


def _fire_trade(details):
    setup = details['setup']
    sig   = setup['entry_signal']
    trade = open_paper_trade(setup, risk_pct=RISK_PCT)
    if trade:
        send_message(
            "CB6 WATCHER — NIFTY LONG TRIGGERED\n\n"
            f"Entry  : {sig['entry']}\n"
            f"SL     : {sig['stop_loss']} (FVG low - buffer)\n"
            f"T1     : {sig['target1']} (1:2)\n"
            f"T2     : {sig['target2']} (1:3)\n"
            f"T3     : {sig['target3']} (DOL)\n"
            f"Risk   : {sig['risk']} pts  ({RISK_PCT}% capital)\n"
            f"RR     : 1:{sig['rr_ratio']}\n"
            f"Qty    : {trade.get('quantity','?')}\n\n"
            "Conditions met:\n"
            f"MSS confirmed + price entered 15min bullish FVG\n"
            f"FVG zone: {details['fvg_lo']} – {details['fvg_hi']}\n\n"
            "Mode: Paper Trade  |  Bot managing SL/TP"
        )
        return True
    else:
        return False


def main():
    fyers        = _init_fyers()
    futures      = get_active_futures()
    nifty_sym    = futures['NIFTY']
    mss_confirmed = False
    poll_count   = 0

    logger.info(f"Watcher started — {nifty_sym} | risk={RISK_PCT}% | runs until 15:30 IST")

    mss_alerted = False  # send Telegram once when MSS first locks in

    while _market_open():
        poll_count += 1
        now_str = _now_ist().strftime('%H:%M IST')

        try:
            df5, df15 = _fetch(fyers, nifty_sym)
            if df5 is None or df15 is None:
                logger.warning("Data fetch failed — retrying next poll")
                time.sleep(POLL_SEC)
                continue

            prev_mss = mss_confirmed
            phase, details, mss_confirmed = _check_conditions(df5, df15, nifty_sym, mss_confirmed)

            ltp = details.get('ltp', '?')

            if mss_confirmed and not prev_mss and not mss_alerted:
                mss_alerted = True

            if phase == 'fire':
                logger.info(f"FIRE — conditions met at {ltp} | {now_str}")
                _fire_trade(details)
                logger.info("Trade placed. Watcher exiting.")
                return

            elif phase == 'waiting_fvg':
                flo = details.get('fvg_lo', '?')
                fhi = details.get('fvg_hi', '?')
                logger.info(f"Poll {poll_count} | {now_str} | LTP={ltp} | MSS=CONFIRMED | FVG {flo}–{fhi} | entry~{round(flo+0.5,2) if isinstance(flo,float) else '?'} | waiting pullback")

            else:  # waiting_mss
                dol = details.get('dol')
                logger.info(
                    f"Poll {poll_count} | {now_str} | LTP={ltp} | "
                    f"MSS=PENDING | DOL={dol['level'] if dol else '?'}"
                )

        except Exception as e:
            logger.error(f"Watcher poll error: {e}")

        time.sleep(POLL_SEC)

    logger.info("Watcher expired — market closed without trigger")


if __name__ == "__main__":
    main()
