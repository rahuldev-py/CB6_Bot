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
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk


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

        setup = scan_setup(df, symbol, min_rr=_P["min_rr"])
        if not setup:
            return
        self._alert("trade_signal_received", f"{symbol} setup received")

        signal = setup["entry_signal"]
        direction = setup["direction"]
        h1_bias = get_h1_bias(self._connector, symbol)
        h4_bias = get_h4_bias(self._connector, symbol)
        if h4_bias not in ("RANGING", direction):
            logger.info(f"GFT 1K Instant {symbol}: H4 block - {h4_bias}")
            return
        if h1_bias not in ("RANGING", direction):
            logger.info(f"GFT 1K Instant {symbol}: H1 block - {h1_bias}")
            return

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
        lots = min(round(lots, 2), _P["max_lot"])
        risk_usd = dollar_risk(symbol, lots, signal["entry"], signal["stop_loss"])
        risk_usd = min(risk_usd, _P["max_risk_usd"])

        ok, reason = validate_entry(setup, lots, risk_usd, state)
        if not ok:
            logger.info(f"GFT 1K Instant {symbol}: BLOCKED - {reason}")
            self._alert("trade_blocked", f"{symbol} blocked: {reason}")
            return

        lock_state = load_lock_state()
        if lock_state.get("locked"):
            reason = lock_state.get("reason") or "telegram lock active"
            logger.info(f"GFT 1K Instant {symbol}: BLOCKED - {reason}")
            self._alert("trade_blocked", f"{symbol} blocked: {reason}")
            return

        self._alert(
            "trade_approved",
            f"{symbol} {direction} approved at {lots:.2f}L risk=${risk_usd:.2f}",
        )
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

        self._connector.start_polling(
            symbols=_P["enabled_symbols"],
            interval="15m",
            on_closed_candle=self.on_closed_candle,
            poll_secs=60 if self._paper else 15,
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
