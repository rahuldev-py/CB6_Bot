"""
CB6 Futures Core — Manual Trade Bridge
Detects and tracks externally opened trades (trades opened manually
outside the bot). Allows coexistence of manual + semi-auto CB6 trades.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("cb6.futures.manual_bridge")


@dataclass
class ManualTrade:
    trade_id: str
    source: str             # "MANUAL" | "CB6_SEMI_AUTO" | "CB6_PAPER"
    symbol: str
    contract: str
    direction: str          # "LONG" | "SHORT"
    entry_price: float
    contracts: int
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None
    point_value: float = 0.0
    notes: str = ""
    open: bool = True

    def close(self, exit_price: float, exit_time: Optional[datetime] = None) -> None:
        self.exit_price = exit_price
        self.exit_time = exit_time or datetime.now(timezone.utc)
        self.open = False
        direction_mult = 1 if self.direction == "LONG" else -1
        self.pnl = round(
            direction_mult * (exit_price - self.entry_price) * self.contracts * self.point_value,
            2,
        )


class ManualTradeBridge:
    """
    Journal for all trades — both manually opened and CB6-generated.
    Persists to CSV + JSON in data/futures/manual_bridge/.
    """

    def __init__(self, storage_dir: str = "data/futures/manual_bridge"):
        self._dir = storage_dir
        os.makedirs(self._dir, exist_ok=True)
        self._json_path = os.path.join(self._dir, "trades.json")
        self._csv_path  = os.path.join(self._dir, "trades.csv")
        self._trades: List[ManualTrade] = self._load()

    def _load(self) -> List[ManualTrade]:
        if not os.path.exists(self._json_path):
            return []
        try:
            with open(self._json_path, encoding="utf-8") as f:
                raw = json.load(f)
            trades = []
            for r in raw:
                r["entry_time"] = datetime.fromisoformat(r["entry_time"])
                if r.get("exit_time"):
                    r["exit_time"] = datetime.fromisoformat(r["exit_time"])
                trades.append(ManualTrade(**r))
            return trades
        except Exception as e:
            logger.warning("ManualBridge load error: %s", e)
            return []

    def _save(self) -> None:
        data = []
        for t in self._trades:
            d = asdict(t)
            d["entry_time"] = t.entry_time.isoformat()
            d["exit_time"] = t.exit_time.isoformat() if t.exit_time else None
            data.append(d)
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        if self._trades:
            with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)

    def log_entry(
        self,
        symbol: str,
        contract: str,
        direction: str,
        entry_price: float,
        contracts: int,
        point_value: float,
        source: str = "MANUAL",
        notes: str = "",
    ) -> ManualTrade:
        trade = ManualTrade(
            trade_id=str(uuid.uuid4())[:8],
            source=source,
            symbol=symbol.upper(),
            contract=contract,
            direction=direction.upper(),
            entry_price=entry_price,
            contracts=contracts,
            entry_time=datetime.now(timezone.utc),
            point_value=point_value,
            notes=notes,
        )
        self._trades.append(trade)
        self._save()
        logger.info("Manual entry logged: %s %s %s @ %.4f x%d",
                    source, symbol, direction, entry_price, contracts)
        return trade

    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        notes: str = "",
    ) -> Optional[ManualTrade]:
        for trade in self._trades:
            if trade.trade_id == trade_id and trade.open:
                trade.close(exit_price)
                if notes:
                    trade.notes += f" | {notes}"
                self._save()
                logger.info("Manual exit logged: %s pnl=$%.2f", trade_id, trade.pnl or 0)
                return trade
        logger.warning("log_exit: trade_id '%s' not found or already closed", trade_id)
        return None

    def open_trades(self) -> List[ManualTrade]:
        return [t for t in self._trades if t.open]

    def closed_trades(self) -> List[ManualTrade]:
        return [t for t in self._trades if not t.open]

    def all_trades(self) -> List[ManualTrade]:
        return list(self._trades)

    def summary(self) -> dict:
        closed = self.closed_trades()
        total_pnl = sum(t.pnl or 0 for t in closed)
        wins = [t for t in closed if (t.pnl or 0) > 0]
        return {
            "open_count": len(self.open_trades()),
            "closed_count": len(closed),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        }
