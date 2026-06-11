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
from forex_engine.gft_10k.config import ACCOUNT_NAMESPACE, GFT_10K_PROFILE, live_execution_enabled
from forex_engine.gft_10k.risk import validate_entry
from forex_engine.gft_10k.state import (
    DEDUP_FILE, HEARTBEAT_FILE, load_lock_state, load_state,
    reset_daily_if_needed, save_state,
)
from forex_engine.scanner.signal_scanner import scan_setup, in_rollover_window
from forex_engine.scanner.structure_scanner import get_h1_bias, get_h4_bias
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk, cap_lots_for_account

_P = GFT_10K_PROFILE


class GFT10KWorker:
    def __init__(self, account_namespace: str = ACCOUNT_NAMESPACE):
        self.account_namespace = account_namespace
        self._paper = not live_execution_enabled()
        from forex_engine.gft_10k.adapter import build_gft_10k_connector
        self._connector = build_gft_10k_connector(paper=self._paper)
        self._dedup    = DuplicateGuard(persist_path=DEDUP_FILE)
        self._slip     = SlippageTracker()
        self._candles  = {}
        self._locks    = {sym: threading.Lock() for sym in _P["enabled_symbols"]}
        self._running  = False

        logger.info(
            f"GFT 10K worker initialized - namespace={account_namespace} "
            f"paper={self._paper} state_dir={_P['state_dir']} magic={_P['magic']}"
        )

    def on_closed_candle(self, symbol: str, df):
        if symbol not in _P["enabled_symbols"]:
            return
        self._candles[symbol] = df
        threading.Thread(target=self._scan, args=(symbol,), daemon=True,
                         name=f"GFT10K_{symbol}").start()

    def on_intracandle_poll(self, symbol: str, df):
        utc_hour = datetime.now(timezone.utc).hour
        if not any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)]):
            return
        if symbol not in _P["enabled_symbols"]:
            return
        self._candles[symbol] = df
        threading.Thread(target=self._scan, args=(symbol,), daemon=True,
                         name=f"GFT10KIntra_{symbol}").start()

    def _scan(self, symbol: str):
        if not self._locks[symbol].acquire(blocking=False):
            return
        try:
            self._run_symbol(symbol)
        except Exception as exc:
            logger.error(f"GFT 10K scan({symbol}): {exc}")
        finally:
            self._locks[symbol].release()

    def _run_symbol(self, symbol: str):
        if is_emergency_stop_active():
            logger.warning(f"GFT 10K scan skipped ({symbol}) - emergency stop")
            return

        utc_hour = datetime.now(timezone.utc).hour
        if in_rollover_window(utc_hour):
            return
        if not any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)]):
            return

        df = self._candles.get(symbol)
        if df is None or len(df) < 40:
            return

        _h4_pre = get_h4_bias(self._connector, symbol)
        if not any(s <= datetime.now(timezone.utc).hour < e for s, e in [(7, 12), (16, 20)]):
            return
        setup = scan_setup(df, symbol, min_rr=_P["min_rr"], h4_bias=_h4_pre)
        if not setup:
            return

        direction = setup["direction"]
        h1_bias   = get_h1_bias(self._connector, symbol)
        h4_bias   = get_h4_bias(self._connector, symbol)

        # H4 direction gate — XAUUSD only (same rule as GFT 2-Step).
        # XAGUSD/USOIL: H4 is informational, not a direction block.
        if h4_bias not in ("RANGING", direction):
            _xauusd_gate = 'XAUUSD' in symbol.upper()
            if _xauusd_gate:
                _wc = int(setup.get('wave_count', 0) or 0)
                _sw = bool(setup.get('sweep_confirmed', False))
                if _wc >= 3 and _sw:
                    logger.info(f"GFT 10K {symbol}: counter-H4 ALLOWED — 3-wave wave={_wc} HALF SIZE")
                    setup['size_multiplier'] = setup.get('size_multiplier', 1.0) * 0.5
                    setup['t1_only']         = True
                else:
                    logger.info(f"GFT 10K {symbol}: counter-H4 SKIP wave={_wc} sweep={_sw} H4={h4_bias}")
                    return
            else:
                logger.info(
                    f"GFT 10K {symbol}: H4={h4_bias} vs {direction} "
                    f"(informational — H4 gate is XAUUSD-only, {symbol} proceeds)"
                )

        if h1_bias not in ("RANGING", direction):
            _wc = int(setup.get('wave_count', 0) or 0)
            _sw = bool(setup.get('sweep_confirmed', False))
            if _wc >= 3 and _sw:
                logger.info(f"GFT 10K {symbol}: 3-WAVE counter-H1 allowed wave={_wc} HALF SIZE T1-only")
                setup['size_multiplier'] = setup.get('size_multiplier', 1.0) * 0.5
                setup['t1_only']         = True
                setup['reversal_3wave']  = True
            else:
                logger.info(f"GFT 10K {symbol}: H1 block H1={h1_bias} wave={_wc}")
                return

        signal  = setup["entry_signal"]
        fvg_low = signal.get("fvg_low")
        if self._dedup.is_duplicate(symbol, direction, fvg_low):
            return

        state   = reset_daily_if_needed(load_state())
        capital = state.get("capital", _P["account_size"])
        if not self._paper:
            equity = self._connector.get_equity()
            if equity and equity > 0:
                capital = equity

        lots     = calc_lot_size(symbol, capital, signal["entry"], signal["stop_loss"],
                                 _P["risk_per_trade_pct"])
        lots     = cap_lots_for_account(symbol, round(lots, 2), _P)
        risk_usd = min(dollar_risk(symbol, lots, signal["entry"], signal["stop_loss"]),
                       _P["max_risk_usd"])

        ok, reason = validate_entry(setup, lots, risk_usd, state)
        if not ok:
            logger.info(f"GFT 10K {symbol}: BLOCKED — {reason}")
            return

        lock_state = load_lock_state()
        if lock_state.get("locked"):
            logger.info(f"GFT 10K {symbol}: BLOCKED — {lock_state.get('reason', 'telegram lock')}")
            return

        logger.info(f"GFT 10K {symbol}: APPROVED {direction} {lots:.2f}L risk=${risk_usd:.2f}")
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
                logger.error(f"GFT 10K {symbol}: MT5 order failed")
                return
            self._update_ticket(trade["id"], result.get("ticket", 0))
            ticket = result.get("ticket", 0)
            logger.info(f"GFT 10K {symbol}: executed ticket={ticket} lots={lots:.2f}")
            self._alert(
                f"<b>GFT 10K — TRADE EXECUTED</b>\n"
                f"{symbol} {direction}  {lots:.2f}L  risk=${risk_usd:.2f}\n"
                f"Entry={signal['entry']}  SL={signal['stop_loss']}  ticket={ticket}"
            )

    def _open_trade_state(self, setup: dict, lots: float, risk_usd: float) -> dict:
        state  = reset_daily_if_needed(load_state())
        signal = setup["entry_signal"]
        trade  = {
            "id"               : str(uuid.uuid4())[:8],
            "ticket"           : 0,
            "account_namespace": self.account_namespace,
            "symbol"           : setup["symbol"],
            "direction"        : setup["direction"],
            "lots"             : lots,
            "entry_price"      : signal["entry"],
            "stop_loss"        : signal["stop_loss"],
            "target"           : signal.get("target2") or signal.get("target1") or signal.get("target3"),
            "rr_ratio"         : signal.get("rr_ratio"),
            "risk_usd"         : risk_usd,
            "magic"            : _P["magic"],
            "entry_time"       : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status"           : "OPEN",
        }
        state["open_trades"].append(trade)
        state["daily_trades"]      = state.get("daily_trades", 0) + 1
        state["available_capital"] = round(
            state.get("available_capital", _P["account_size"]) - risk_usd, 2
        )
        save_state(state)
        return trade

    def _rollback_trade(self, trade_id: str, risk_usd: float) -> None:
        state = load_state()
        state["open_trades"]       = [t for t in state.get("open_trades", []) if t.get("id") != trade_id]
        state["daily_trades"]      = max(0, state.get("daily_trades", 0) - 1)
        state["available_capital"] = round(state.get("available_capital", _P["account_size"]) + risk_usd, 2)
        save_state(state)

    def _update_ticket(self, trade_id: str, ticket: int) -> None:
        state = load_state()
        for trade in state.get("open_trades", []):
            if trade.get("id") == trade_id:
                trade["ticket"] = ticket
                save_state(state)
                return

    def _alert(self, text: str) -> None:
        try:
            from communications.forex_bot import send_alert as _sa
            _sa(text)
        except Exception as exc:
            logger.debug(f"GFT 10K Telegram alert skipped: {exc}")

    def _position_monitor_loop(self):
        """Polls MT5 every 15s — detects SL/TP hits on open trades and sends Telegram alert."""
        while self._running:
            try:
                state = load_state()
                open_trades = state.get("open_trades", [])
                if open_trades and not self._paper:
                    live_tickets = set()
                    try:
                        positions    = self._connector.get_open_positions()
                        live_tickets = {str(p.get("ticket", 0)) for p in (positions or [])}
                    except Exception:
                        pass
                    closed_now = [t for t in open_trades
                                  if str(t.get("ticket", 0)) not in ("0", "") and
                                  str(t.get("ticket", 0)) not in live_tickets]
                    if closed_now:
                        for trade in closed_now:
                            sym   = trade.get("symbol", "?")
                            dire  = trade.get("direction", "?")
                            entry = trade.get("entry_price", 0)
                            sl    = trade.get("stop_loss", 0)
                            tp    = trade.get("target", 0)
                            lots  = trade.get("lots", 0)
                            try:
                                tick     = self._connector.get_tick(sym)
                                close_px = tick.bid if dire == "BULLISH" else tick.ask if tick else 0
                            except Exception:
                                close_px = 0
                            if close_px and sl:
                                hit = "SL" if (
                                    (dire == "BULLISH" and close_px <= float(sl) * 1.001) or
                                    (dire == "BEARISH" and close_px >= float(sl) * 0.999)
                                ) else "TP"
                            else:
                                hit = "CLOSED"
                            pnl_est = ""
                            if close_px and entry:
                                pnl_est = f"  ~${round((close_px - float(entry)) * float(lots) * (100 if 'XAU' in sym else 1000) * (1 if dire == 'BULLISH' else -1), 2):+.2f}"
                            self._alert(
                                f"<b>GFT 10K — {hit}</b>\n"
                                f"{sym} {dire}  {lots}L  @ {close_px:.2f}{pnl_est}\n"
                                f"entry={entry}  SL={sl}  ticket={trade.get('ticket')}"
                            )
                            logger.info(f"GFT 10K {sym}: {hit} detected ticket={trade.get('ticket')}")
                        closed_ids           = {t["id"] for t in closed_now}
                        state["open_trades"] = [t for t in open_trades if t["id"] not in closed_ids]
                        save_state(state)
            except Exception as exc:
                logger.debug(f"GFT 10K position monitor: {exc}")
            time.sleep(15)

    def _heartbeat_loop(self):
        while self._running:
            try:
                with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                pass
            time.sleep(60)

    def run(self):
        self._running = True
        logger.info("=" * 55)
        logger.info("GFT $10K INSTANT ENGINE")
        logger.info(f"Namespace : {self.account_namespace}")
        logger.info(f"State dir : {_P['state_dir']}")
        logger.info(f"Max lot   : {_P['max_lot']:.2f}")
        logger.info(f"Risk cap  : ${_P['max_risk_usd']:.2f}")
        logger.info(f"Daily DD  : ${_P['daily_dd_limit']:.0f} (hard stop)")
        logger.info(f"Mode      : {'Paper' if self._paper else 'LIVE — GFT $10K'}")
        logger.info("=" * 55)

        threading.Thread(target=self._heartbeat_loop,       daemon=True, name="GFT10KHeartbeat").start()
        threading.Thread(target=self._position_monitor_loop, daemon=True, name="GFT10KPositionMonitor").start()

        try:
            from communications.forex_bot import set_adapter as _set_bot_adapter
            _set_bot_adapter(self._connector)
        except Exception:
            pass

        self._connector.start_polling(
            symbols          = _P["enabled_symbols"],
            interval         = "15m",
            on_closed_candle = self.on_closed_candle,
            poll_secs        = 60 if self._paper else 15,
            on_intracandle   = None if self._paper else self.on_intracandle_poll,
        )

        while self._running:
            time.sleep(10)

    def stop(self):
        self._running = False
        self._connector.stop_polling()
        self._connector.disconnect()
        logger.info("GFT 10K engine stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description="CB6 GFT $10K Instant monitor")
    parser.add_argument("--account-namespace", default=ACCOUNT_NAMESPACE,
                        choices=[ACCOUNT_NAMESPACE])
    args = parser.parse_args(argv)
    worker = GFT10KWorker(account_namespace=args.account_namespace)
    worker.run()


if __name__ == "__main__":
    main()
