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
from forex_engine.scanner.mtf_scanner import scan_mtf_cascade, granular_mtf_confirm
from forex_engine.scanner.adaptive_gate import evaluate_adaptive_gate as _eval_ag, log_gate_decision as _log_ag
from settings import CB6_ADAPTIVE_TRADE_GATE_ENABLED as _ADAPTIVE_GATE_ENABLED
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk, cap_lots_for_account
from forex_engine.prop_firms.gft.gft_anti_hedge_guard import check_no_same_symbol


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

        # Per-symbol position guard — bail early before any MT5 calls
        _pre_state = load_state()
        _sym_ok, _sym_reason = check_no_same_symbol(_pre_state, symbol, max_positions=1)
        if not _sym_ok:
            logger.info(f"GFT 1K Instant {symbol}: BLOCKED — {_sym_reason}")
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

        # Granular 6-TF entry confirmation: 45m→30m→15m→5m→2m→1m
        _gran = granular_mtf_confirm(
            self._connector, symbol, setup['direction'], _h4_pre
        )
        if not _gran['confirmed']:
            logger.info(
                f"GFT 1K Instant {symbol}: granular MTF BLOCKED — "
                f"{_gran['blocking_reason']} (score={_gran['score']}/6)"
            )
            return
        if _gran['size_multiplier'] < float(setup.get('size_multiplier', 1.0) or 1.0):
            setup['size_multiplier'] = _gran['size_multiplier']
        if _gran['t1_only']:
            setup['t1_only'] = True
        setup['granular_mtf_score'] = _gran['score']
        setup['granular_mtf_action'] = _gran['action']

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
                    'score': (lambda _c: _c.get('score', 0) if isinstance(_c, dict) else int(_c or 0))(setup.get('confluence', 0)),
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

        # Determine order type: LIMIT when LTP drifted >25% of SL distance from FVG entry,
        # MARKET when close enough for a clean fill.
        # BUY LIMIT valid only when entry < LTP; SELL LIMIT valid only when entry > LTP.
        _use_limit_1k = False
        _cur_px_1k    = None
        if not self._paper:
            _cur_px_1k = self._connector.get_price(symbol)
            if _cur_px_1k:
                _sl_dist_1k = abs(signal["entry"] - signal["stop_loss"])
                _drift_1k   = abs(_cur_px_1k - signal["entry"])
                if _drift_1k > _sl_dist_1k * 0.25:
                    _is_long_1k  = direction in ("BULLISH", "BUY")
                    _lmt_ok_1k   = (_is_long_1k and signal["entry"] < _cur_px_1k) or \
                                   (not _is_long_1k and signal["entry"] > _cur_px_1k)
                    if not _lmt_ok_1k:
                        logger.info(
                            f"GFT 1K Instant {symbol}: PRICE DRIFTED past entry — "
                            f"entry={signal['entry']:.5f} LTP={_cur_px_1k:.5f} — skip"
                        )
                        return
                    _use_limit_1k = True
                    logger.info(
                        f"GFT 1K Instant {symbol}: PRICE DRIFTED — "
                        f"entry={signal['entry']:.5f} LTP={_cur_px_1k:.5f} "
                        f"drift={_drift_1k:.5f} — using LIMIT order"
                    )

        _order_tag = "LIMIT" if _use_limit_1k else "MARKET"
        logger.info(f"GFT 1K Instant {symbol}: {direction} approved {lots:.2f}L risk=${risk_usd:.2f} [{_order_tag}]")
        _1k_session = (
            "london"    if 7  <= utc_hour < 12 else
            "new_york"  if 16 <= utc_hour < 20 else
            "off_session"
        )
        setup["h4_bias"] = h4_bias
        setup["session"] = _1k_session
        trade = self._open_trade_state(setup, lots, risk_usd)
        if not trade:
            return
        self._dedup.mark_seen(symbol, direction, fvg_low)

        if not self._paper and not lock_state.get("dry_run", True):
            if _use_limit_1k:
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                _now_utc = _dt.now(_tz.utc)
                _h = _now_utc.hour
                if 7 <= _h < 12:
                    _kz_end = _now_utc.replace(hour=12, minute=0, second=0, microsecond=0)
                elif 16 <= _h < 20:
                    _kz_end = _now_utc.replace(hour=20, minute=0, second=0, microsecond=0)
                else:
                    _kz_end = _now_utc + _td(hours=1)
                _expiry_1k = min(_kz_end, _now_utc + _td(hours=2))
                result = self._connector.place_limit_order(
                    symbol=symbol,
                    direction="BUY" if direction == "BULLISH" else "SELL",
                    lots=lots,
                    entry=signal["entry"],
                    sl=signal["stop_loss"],
                    tp=signal.get("target2") or signal.get("target1") or 0.0,
                    magic=_P["magic"],
                    expiry=_expiry_1k,
                )
                if not result:
                    self._rollback_trade(trade["id"], risk_usd)
                    logger.error(f"GFT 1K Instant {symbol}: MT5 LIMIT order failed")
                    self._alert("trade_blocked", f"{symbol} LIMIT order failed")
                    return
                self._update_ticket(trade["id"], result.get("ticket", 0))
                self._alert(
                    "trade_executed",
                    f"{symbol} LIMIT placed ticket={result.get('ticket', 0)} lots={lots:.2f} "
                    f"@ {signal['entry']:.5f} | expires {_expiry_1k.strftime('%H:%M')} UTC",
                )
            else:
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
                    try:
                        import MetaTrader5 as _mt5
                        _last = _mt5.last_error()
                        _err_detail = f"retcode={_last[0]} {_last[1]}" if _last else "unknown"
                    except Exception:
                        _err_detail = "see log"
                    logger.error(f"GFT 1K Instant {symbol}: MT5 order failed — {_err_detail}")
                    self._alert("trade_blocked", f"{symbol} MT5 order failed — {_err_detail}")
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
            "h4_bias": setup.get("h4_bias", ""),
            "session": setup.get("session", ""),
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "OPEN",
        }
        state["open_trades"].append(trade)
        state["daily_trades"] = state.get("daily_trades", 0) + 1
        state["available_capital"] = round(
            state.get("available_capital", _P["account_size"]) - risk_usd, 2
        )
        save_state(state)
        try:
            from utils.audit_log import append as _audit
            _audit("ORDER_PLACED", "gft_1k", "forex",
                   trade_id=trade["id"], symbol=trade["symbol"],
                   direction=trade["direction"], lots=trade["lots"],
                   entry=trade["entry_price"], sl=trade["stop_loss"],
                   risk_usd=risk_usd)
        except Exception:
            pass
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

    def _reconcile_on_startup(self):
        """Diff state open_trades against live broker positions at startup.
        Closes ghost trades (state=open but broker=gone) with actual deal P&L."""
        if self._paper:
            return
        try:
            live_positions = self._connector.get_live_positions(_P["magic"])
        except Exception as _e:
            logger.error(f"GFT 1K reconcile: failed to fetch live positions — {_e}")
            return

        live_tickets = {p['ticket'] for p in live_positions} if live_positions else set()
        state  = load_state()
        ghosts = [
            t for t in state.get('open_trades', [])
            if t.get('ticket', 0) not in live_tickets and t.get('ticket', 0) != 0
        ]
        if not ghosts:
            logger.info(
                f"GFT 1K reconcile: OK — {len(state.get('open_trades', []))} state trade(s) "
                f"match broker ({len(live_tickets)} live position(s))"
            )
            return

        logger.warning(f"GFT 1K reconcile: {len(ghosts)} ghost trade(s) — closing offline")
        for ghost in ghosts:
            ticket    = ghost.get('ticket', 0)
            sym       = ghost.get('symbol', '?')
            entry     = float(ghost.get('entry_price', 0))
            lots      = float(ghost.get('lots', 0))
            dire      = ghost.get('direction', 'BULLISH')
            close_px, pnl = 0.0, 0.0
            try:
                close_px, pnl = self._connector.get_last_deal_for_ticket(ticket)
            except Exception:
                pass
            if not close_px:
                close_px = self._connector.get_price(sym) or entry
                pnl = round((close_px - entry) * lots * (100 if 'XAU' in sym else 1000) *
                             (1 if dire == 'BULLISH' else -1), 2)

            state['capital']    = round(state.get('capital',    _P['account_size']) + pnl, 2)
            state['daily_pnl']  = round(state.get('daily_pnl', 0.0) + pnl, 2)
            state['open_trades'] = [t for t in state['open_trades'] if t.get('id') != ghost.get('id')]
            logger.warning(f"GFT 1K reconcile: ghost closed — {sym} ticket={ticket} pnl=${pnl:.2f}")
            self._alert(
                "reconcile",
                f"{sym} ticket={ticket} OFFLINE CLOSE exit={close_px:.5f} pnl=${pnl:.2f}"
            )
        save_state(state)

    def _position_monitor_loop(self):
        """Polls MT5 every 15s — detects SL/TP hits on open trades and sends Telegram alert."""
        while self._running:
            try:
                state = load_state()
                open_trades = state.get("open_trades", [])

                if not self._paper:
                    # Update floating P&L every cycle so daily_drawdown() sees unrealized losses
                    try:
                        _eq = self._connector.get_equity()
                        _bal = self._connector.get_balance()
                        if _eq and _bal:
                            state["floating_pnl"] = round(_eq - _bal, 2)
                            state["live_equity"]   = round(_eq, 2)
                            state["live_balance"]  = round(_bal, 2)
                            save_state(state)
                    except Exception:
                        pass

                    if open_trades:
                        live_tickets = set()
                        try:
                            positions    = self._connector.get_open_positions()
                            live_tickets = {str(p.get("ticket", 0)) for p in (positions or [])}
                        except Exception:
                            pass
                        closed_now = [
                            t for t in open_trades
                            if str(t.get("ticket", 0)) not in ("0", "") and
                               str(t.get("ticket", 0)) not in live_tickets
                        ]
                        if closed_now:
                            for trade in closed_now:
                                sym   = trade.get("symbol", "?")
                                dire  = trade.get("direction", "?")
                                entry = float(trade.get("entry_price", 0))
                                sl    = float(trade.get("stop_loss", 0))
                                tp    = float(trade.get("target", 0))
                                lots  = float(trade.get("lots", 0))
                                ticket_int = int(trade.get("ticket", 0) or 0)

                                # Use actual deal history for close price + P&L
                                close_px, actual_pnl = 0.0, None
                                try:
                                    close_px, actual_pnl = self._connector.get_last_deal_for_ticket(ticket_int)
                                except Exception:
                                    pass
                                if not close_px:
                                    try:
                                        close_px = self._connector.get_price(sym) or 0.0
                                    except Exception:
                                        close_px = 0.0

                                hit = "CLOSED"
                                if close_px and sl:
                                    hit = "SL" if (
                                        (dire == "BULLISH" and close_px <= sl * 1.001) or
                                        (dire == "BEARISH" and close_px >= sl * 0.999)
                                    ) else "TP"

                                pnl = actual_pnl if actual_pnl is not None else (
                                    round((close_px - entry) * lots * (100 if "XAU" in sym else 1000) *
                                          (1 if dire == "BULLISH" else -1), 2)
                                    if close_px and entry else 0
                                )
                                pnl_str = f"  ${pnl:+.2f}" if pnl else ""

                                self._alert(
                                    "sl_hit" if hit == "SL" else "tp_hit",
                                    f"{sym} {dire} {hit} @ {close_px:.2f}{pnl_str}  entry={entry}  SL={sl}  lots={lots}L"
                                )
                                logger.info(f"GFT 1K Instant {sym}: {hit} detected ticket={ticket_int} pnl={pnl:+.2f}")

                                # Update capital and daily P&L with actual result
                                state["capital"]     = round(state.get("capital", _P["account_size"]) + pnl, 2)
                                state["daily_pnl"]   = round(state.get("daily_pnl", 0.0) + pnl, 2)
                                state["available_capital"] = round(
                                    state.get("available_capital", _P["account_size"]) + trade.get("risk_usd", 0), 2
                                )

                                try:
                                    from utils.audit_log import append as _audit
                                    _audit("POSITION_CLOSED", "gft_1k", "forex",
                                           symbol=sym, ticket=ticket_int, direction=dire,
                                           lots=lots, entry=entry, close_px=close_px,
                                           pnl=pnl, hit=hit)
                                except Exception:
                                    pass

                            closed_ids           = {t["id"] for t in closed_now}
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

        # Reconcile state vs broker before entering main loop
        self._reconcile_on_startup()

        # REST control plane on localhost:7374
        try:
            from utils.control_server import start as _cs_start, set_state_loader as _cs_loader
            _cs_loader(load_state)
            _cs_start(port=7374)
        except Exception as _cse:
            logger.warning(f"GFT 1K control server failed to start: {_cse}")

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
