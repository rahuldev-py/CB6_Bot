import argparse
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Load .env so subprocess has all credentials when started directly via -m
try:
    from dotenv import dotenv_values as _dv
    for _k, _v in _dv(os.path.join(_ROOT, ".env")).items():
        if _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    pass

from utils.emergency_stop import is_emergency_stop_active
from utils.logger import logger
from forex_engine.data.slippage_tracker import SlippageTracker
from forex_engine.forex_instruments import INSTRUMENTS
from forex_engine.gft_1k_instant.config import (
    ACCOUNT_NAMESPACE,
    GFT_1K_INSTANT_PROFILE,
    live_execution_enabled,
)
from forex_engine.gft_1k_instant.risk import validate_entry
from forex_engine.gft_1k_instant.state import (
    DEDUP_FILE,
    HEARTBEAT_FILE,
    load_lock_state,
    load_state,
    reset_daily_if_needed,
    save_state,
)
from forex_engine.scanner.signal_scanner import scan_setup, in_rollover_window
from forex_engine.scanner.structure_scanner import get_h1_bias, get_h4_bias
from forex_engine.scanner.mtf_scanner import scan_mtf_cascade
from forex_engine.scanner.adaptive_gate import evaluate_adaptive_gate as _eval_ag, log_gate_decision as _log_ag
from settings import CB6_ADAPTIVE_TRADE_GATE_ENABLED as _ADAPTIVE_GATE_ENABLED
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk, cap_lots_for_account


_P = GFT_1K_INSTANT_PROFILE


class GFT1KInstantWorker:
    def __init__(self, account_namespace: str = ACCOUNT_NAMESPACE):
        if account_namespace != ACCOUNT_NAMESPACE:
            raise ValueError(f"Invalid account namespace: {account_namespace}")

        self.account_namespace = account_namespace
        self._paper = not live_execution_enabled()
        from forex_engine.accounts.gft_1k_instant_adapter import (
            build_gft_1k_instant_connector,
        )
        self._connector = build_gft_1k_instant_connector(paper=self._paper)
        try:
            from forex_engine.gft_1k_instant.telegram_bot import set_connector
            set_connector(self._connector)
        except Exception:
            logger.warning("GFT 1K Instant Telegram connector wiring skipped", exc_info=True)
        self._dedup = DuplicateGuard(persist_path=DEDUP_FILE)
        self._slip = SlippageTracker()
        self._candles = {}
        self._locks = {symbol: threading.Lock() for symbol in _P["enabled_symbols"]}
        self._running = False

        logger.info(
            f"GFT 1K Instant worker initialized - namespace={account_namespace} "
            f"paper={self._paper} state_dir={_P['state_dir']} magic={_P['magic']}"
        )

    def on_closed_candle(self, symbol: str, df):
        if symbol not in _P["enabled_symbols"]:
            return
        self._candles[symbol] = df
        threading.Thread(
            target=self._scan,
            args=(symbol,),
            daemon=True,
            name=f"GFT1K_{symbol}",
        ).start()

    def on_intracandle_poll(self, symbol: str, df):
        """Fires every 15s with live forming bar from MT5 (pos=0 OHLC)."""
        utc_hour = datetime.now(timezone.utc).hour
        if not any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)]):
            return
        if symbol not in _P["enabled_symbols"]:
            return
        self._candles[symbol] = df
        threading.Thread(
            target=self._scan,
            args=(symbol,),
            daemon=True,
            name=f"GFT1KIntra_{symbol}",
        ).start()

    def _scan(self, symbol: str):
        if not self._locks[symbol].acquire(blocking=False):
            return
        try:
            self._run_symbol(symbol)
        except Exception as exc:
            logger.error(f"GFT 1K Instant scan({symbol}): {exc}")
        finally:
            self._locks[symbol].release()

    def _run_symbol(self, symbol: str):
        if is_emergency_stop_active():
            logger.warning(f"GFT 1K Instant scan skipped ({symbol}) - emergency stop")
            return

        utc_hour = datetime.now(timezone.utc).hour
        if in_rollover_window(utc_hour):
            return
        # Kill zone: London 07-12 UTC | NY 16-20 UTC
        if not any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)]):
            return

        df = self._candles.get(symbol)
        if df is None or len(df) < 40:
            return

        # Pre-fetch H4 bias before scanner so gate is enforced at scan time (belt-and-suspenders)
        _h4_pre = get_h4_bias(self._connector, symbol)
        # Re-check KZ: H4 fetch is an MT5 call that can block for minutes; if KZ closed while
        # waiting, the scan would fire with stale KZ context — abort before calling scan_setup.
        if not any(s <= datetime.now(timezone.utc).hour < e for s, e in [(7, 12), (16, 20)]):
            return
        setup = scan_setup(df, symbol, min_rr=_P["min_rr"], h4_bias=_h4_pre)
        if not setup:
            setup = scan_mtf_cascade(self._connector, symbol, h4_bias=_h4_pre, min_rr=_P["min_rr"])
            if not setup:
                return
        logger.info(f"GFT 1K Instant {symbol}: setup found score={setup.get('confluence', '?')}")

        signal = setup["entry_signal"]
        direction = setup["direction"]
        h1_bias = get_h1_bias(self._connector, symbol)
        h4_bias = get_h4_bias(self._connector, symbol)

        # HTF gate — adaptive or legacy path.
        _adaptive_size_mult_1k = float(setup.get('size_multiplier', 1.0) or 1.0)
        if _ADAPTIVE_GATE_ENABLED:
            _adg_1k = _eval_ag(setup, h4_bias, h1_bias, utc_hour)
            _log_ag(symbol, _adg_1k)
            if not _adg_1k.trade_allowed:
                logger.info(
                    f"GFT 1K Instant {symbol}: adaptive gate {_adg_1k.decision} — "
                    f"{_adg_1k.soft_gate_reasons or _adg_1k.hard_block_reasons}"
                )
                return
            _adaptive_size_mult_1k = _adg_1k.size_multiplier
            if _adg_1k.t1_only:
                setup['t1_only'] = True
        else:
            # LEGACY path
            if setup.get('mtf_cascade'):
                logger.info(
                    f"GFT 1K Instant {symbol}: MTF cascade path — "
                    f"HTF gates bypassed (size={setup.get('size_multiplier',1.0):.1f}x "
                    f"t1_only={setup.get('t1_only',False)} tfs={setup.get('cascade_tfs',[])})"
                )
            elif h4_bias not in ("RANGING", direction):
                _xauusd_gate = 'XAUUSD' in symbol.upper()
                if _xauusd_gate:
                    _wc_h4 = int(setup.get('wave_count', 0) or 0)
                    _sw_h4 = bool(setup.get('sweep_confirmed', False))
                    if _wc_h4 >= 3 and _sw_h4:
                        logger.info(
                            f"GFT 1K Instant {symbol}: counter-H4 ALLOWED — 3-wave reversal "
                            f"wave={_wc_h4} H4={h4_bias} HALF SIZE"
                        )
                        setup['size_multiplier'] = setup.get('size_multiplier', 1.0) * 0.5
                        setup['t1_only']         = True
                    else:
                        logger.info(
                            f"GFT 1K Instant {symbol}: counter-H4 SKIP — 3-wave not complete "
                            f"wave={_wc_h4} sweep={_sw_h4} H4={h4_bias}"
                        )
                        return
                else:
                    logger.info(
                        f"GFT 1K Instant {symbol}: H4={h4_bias} vs {direction} "
                        f"(informational — H4 gate is XAUUSD-only, {symbol} proceeds)"
                    )

            # H1 — 3-wave exception
            _wc = int(setup.get('wave_count', 0) or 0)
            _sw = bool(setup.get('sweep_confirmed', False))
            if h1_bias not in ("RANGING", direction):
                if _wc >= 3 and _sw:
                    logger.info(
                        f"GFT 1K Instant {symbol}: 3-WAVE counter-H1 allowed "
                        f"wave={_wc} HALF SIZE T1-only"
                    )
                    setup['size_multiplier'] = setup.get('size_multiplier', 1.0) * 0.5
                    setup['t1_only']         = True
                    setup['reversal_3wave']  = True
                else:
                    logger.info(
                        f"GFT 1K Instant {symbol}: H1 block — H1={h1_bias} "
                        f"wave={_wc} (need ≥3 + sweep)"
                    )
                    return
            _adaptive_size_mult_1k = float(setup.get('size_multiplier', 1.0) or 1.0)

        # Wave exhaustion tag (5-6 waves)
        _wc_now = int(setup.get('wave_count', 0) or 0)
        setup['wave_count_tag'] = _wc_now
        setup['wave_exhaustion'] = _wc_now >= 5
        if _wc_now >= 5:
            logger.info(
                f"GFT 1K Instant {symbol}: ⭐ WAVE EXHAUSTION TAG — {_wc_now} waves "
                f"direction={direction} H4={h4_bias}"
            )
            try:
                import json as _json, os as _os
                _wex_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                    'data', 'wave_exhaustion_log.jsonl'
                )
                _wex_row = {
                    'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'symbol': symbol, 'direction': direction,
                    'wave_count': _wc_now, 'h4_bias': h4_bias,
                    'score': setup.get('confluence', 0),
                    'account': 'GFT_1K_INSTANT',
                }
                with open(_wex_path, 'a', encoding='utf-8') as _wf:
                    _wf.write(_json.dumps(_wex_row) + '\n')
            except Exception:
                pass

        fvg_low = signal.get("fvg_low")
        if self._dedup.is_duplicate(symbol, direction, fvg_low):
            return

        state = reset_daily_if_needed(load_state())
        capital = state.get("capital", _P["account_size"])
        if not self._paper:
            equity = self._connector.get_equity()
            if equity and equity > 0:
                capital = equity

        lots = calc_lot_size(
            symbol,
            capital,
            signal["entry"],
            signal["stop_loss"],
            _P["risk_per_trade_pct"],
        )
        lots = cap_lots_for_account(symbol, round(lots, 2), _P)
        min_lot_1k = INSTRUMENTS.get(symbol, {}).get('min_lot', 0.01)
        if _adaptive_size_mult_1k < 1.0:
            lots = max(min_lot_1k, round(lots * _adaptive_size_mult_1k, 2))
        risk_usd = dollar_risk(symbol, lots, signal["entry"], signal["stop_loss"])
        risk_usd = min(risk_usd, _P["max_risk_usd"])

        ok, reason = validate_entry(setup, lots, risk_usd, state)
        if not ok:
            logger.info(f"GFT 1K Instant {symbol}: BLOCKED - {reason}")
            return

        lock_state = load_lock_state()
        if lock_state.get("locked"):
            reason = lock_state.get("reason") or "telegram lock active"
            logger.info(f"GFT 1K Instant {symbol}: BLOCKED - {reason}")
            return

        # Price proximity check — skip if LTP has drifted >25% of SL distance from FVG entry.
        if not self._paper:
            _cur_px_1k = self._connector.get_price(symbol)
            if _cur_px_1k:
                _max_drift_1k = abs(signal["entry"] - signal["stop_loss"]) * 0.25
                _drift_1k     = abs(_cur_px_1k - signal["entry"])
                if _drift_1k > _max_drift_1k:
                    logger.info(
                        f"GFT 1K Instant {symbol}: PRICE DRIFTED — "
                        f"entry={signal['entry']:.5f} LTP={_cur_px_1k:.5f} "
                        f"drift={_drift_1k:.5f} > max={_max_drift_1k:.5f}. "
                        f"Waiting for retrace."
                    )
                    return

        logger.info(f"GFT 1K Instant {symbol}: {direction} approved {lots:.2f}L risk=${risk_usd:.2f}")
        trade = self._open_trade_state(setup, lots, risk_usd)
        if not trade:
            return
        self._dedup.mark_seen(symbol, direction, fvg_low)

        if not self._paper and not lock_state.get("dry_run", True):
            result = self._connector.place_market_order(
                symbol=symbol,
                direction="BUY" if direction == "BULLISH" else "SELL",
                lots=lots,
                sl=signal["stop_loss"],
                tp=signal.get("target2") or signal.get("target1") or 0.0,
                magic=_P["magic"],
            )
            if not result:
                self._rollback_trade(trade["id"], risk_usd)
                logger.error(f"GFT 1K Instant {symbol}: MT5 order failed")
                self._alert("trade_blocked", f"{symbol} MT5 order failed")
                return
            self._update_ticket(trade["id"], result.get("ticket", 0))
            self._alert(
                "trade_executed",
                f"{symbol} executed ticket={result.get('ticket', 0)} lots={lots:.2f}",
            )

        logger.info(
            f"GFT 1K Instant {symbol}: trade opened {direction} "
            f"{lots}L risk=${risk_usd:.2f} magic={_P['magic']}"
        )

    def _open_trade_state(self, setup: dict, lots: float, risk_usd: float) -> dict:
        state = reset_daily_if_needed(load_state())
        signal = setup["entry_signal"]
        trade = {
            "id": str(uuid.uuid4())[:8],
            "ticket": 0,
            "account_namespace": self.account_namespace,
            "symbol": setup["symbol"],
            "direction": setup["direction"],
            "lots": lots,
            "entry_price": signal["entry"],
            "stop_loss": signal["stop_loss"],
            "target": signal.get("target2") or signal.get("target1") or signal.get("target3"),
            "rr_ratio": signal.get("rr_ratio"),
            "risk_usd": risk_usd,
            "magic": _P["magic"],
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "OPEN",
        }
        state["open_trades"].append(trade)
        state["daily_trades"] = state.get("daily_trades", 0) + 1
        state["available_capital"] = round(
            state.get("available_capital", _P["account_size"]) - risk_usd, 2
        )
        save_state(state)
        return trade

    def _rollback_trade(self, trade_id: str, risk_usd: float) -> None:
        state = load_state()
        state["open_trades"] = [
            trade for trade in state.get("open_trades", [])
            if trade.get("id") != trade_id
        ]
        state["daily_trades"] = max(0, state.get("daily_trades", 0) - 1)
        state["available_capital"] = round(
            state.get("available_capital", _P["account_size"]) + risk_usd, 2
        )
        save_state(state)

    def _update_ticket(self, trade_id: str, ticket: int) -> None:
        state = load_state()
        for trade in state.get("open_trades", []):
            if trade.get("id") == trade_id:
                trade["ticket"] = ticket
                save_state(state)
                return

    def _alert(self, alert_type: str, message: str) -> None:
        try:
            from forex_engine.gft_1k_instant.telegram_bot import send_alert
            send_alert(alert_type, message)
        except Exception:
            logger.debug(f"GFT 1K Instant alert skipped: {alert_type}")

    def _position_monitor_loop(self):
        """Polls MT5 every 15s — detects SL/TP hits on open trades and sends Telegram alert."""
        while self._running:
            try:
                state = load_state()
                open_trades = state.get("open_trades", [])
                if open_trades and not self._paper:
                    live_tickets = set()
                    try:
                        positions = self._connector.get_open_positions()
                        live_tickets = {str(p.get("ticket", 0)) for p in (positions or [])}
                    except Exception:
                        pass
                    closed_now = []
                    for trade in open_trades:
                        ticket = str(trade.get("ticket", 0))
                        if ticket and ticket != "0" and ticket not in live_tickets:
                            closed_now.append(trade)
                    if closed_now:
                        for trade in closed_now:
                            sym   = trade.get("symbol", "?")
                            dire  = trade.get("direction", "?")
                            entry = trade.get("entry_price", 0)
                            sl    = trade.get("stop_loss", 0)
                            tp    = trade.get("target", 0)
                            lots  = trade.get("lots", 0)
                            try:
                                tick = self._connector.get_tick(sym)
                                close_px = tick.bid if dire == "BULLISH" else tick.ask if tick else 0
                            except Exception:
                                close_px = 0
                            if close_px and sl:
                                mid_sl_tp = (float(sl) + float(tp)) / 2 if tp else float(sl)
                                hit = "SL" if (
                                    (dire == "BULLISH" and close_px <= float(sl) * 1.001) or
                                    (dire == "BEARISH" and close_px >= float(sl) * 0.999)
                                ) else "TP"
                            else:
                                hit = "CLOSED"
                            pnl_est = ""
                            if close_px and entry:
                                pip_val = 0.1 if "XAU" in sym else 0.01
                                pnl_est = f"  ~${round((close_px - float(entry)) * float(lots) * (100 if 'XAU' in sym else 1000) * (1 if dire == 'BULLISH' else -1), 2):+.2f}"
                            self._alert(
                                "sl_hit" if hit == "SL" else "tp_hit",
                                f"{sym} {dire} {hit} @ {close_px:.2f}{pnl_est}  entry={entry}  SL={sl}  lots={lots}L"
                            )
                            logger.info(f"GFT 1K Instant {sym}: {hit} detected ticket={trade.get('ticket')}")
                        # Remove closed trades from state
                        closed_ids = {t["id"] for t in closed_now}
                        state["open_trades"] = [t for t in open_trades if t["id"] not in closed_ids]
                        save_state(state)
            except Exception as exc:
                logger.debug(f"GFT 1K position monitor: {exc}")
            time.sleep(15)

    def _heartbeat_loop(self):
        while self._running:
            try:
                with open(HEARTBEAT_FILE, "w", encoding="utf-8") as handle:
                    handle.write(datetime.now().isoformat())
            except Exception:
                logger.warning("GFT 1K Instant heartbeat write failed", exc_info=True)
            time.sleep(60)

    def run(self):
        self._running = True
        logger.info("=" * 55)
        logger.info("GFT 1K Instant Engine")
        logger.info(f"Namespace : {self.account_namespace}")
        logger.info(f"State dir : {_P['state_dir']}")
        logger.info(f"Max lot   : {_P['max_lot']:.2f}")
        logger.info(f"Risk cap  : ${_P['max_risk_usd']:.2f}")
        logger.info(f"Mode      : {'Paper' if self._paper else 'LIVE - GFT 1K'}")
        logger.info("=" * 55)

        try:
            from forex_engine.gft_1k_instant.telegram_bot import (
                start_background_listener,
                startup_alert,
            )
            start_background_listener()
            startup_alert()
        except Exception:
            logger.warning("GFT 1K Instant Telegram startup skipped", exc_info=True)

        threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="GFT1KHeartbeat",
        ).start()
        threading.Thread(
            target=self._position_monitor_loop,
            daemon=True,
            name="GFT1KPositionMonitor",
        ).start()

        self._connector.start_polling(
            symbols=_P["enabled_symbols"],
            interval="15m",
            on_closed_candle=self.on_closed_candle,
            poll_secs=60 if self._paper else 15,
            on_intracandle=None if self._paper else self.on_intracandle_poll,
        )

        while self._running:
            time.sleep(10)

    def stop(self):
        self._running = False
        self._connector.stop_polling()
        self._connector.disconnect()
        logger.info("GFT 1K Instant engine stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description="CB6 GFT 1K Instant monitor")
    parser.add_argument(
        "--account-namespace",
        default=ACCOUNT_NAMESPACE,
        choices=[ACCOUNT_NAMESPACE],
    )
    args = parser.parse_args(argv)

    worker = GFT1KInstantWorker(account_namespace=args.account_namespace)
    worker.run()


if __name__ == "__main__":
    main()
