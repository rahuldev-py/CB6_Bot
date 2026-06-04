"""
CB6 Futures Core — ML Training Data Logger
Captures every trade signal and its outcome in a structured schema.
Stored in ml/futures/training_data/ as JSONL + CSV.
Used later for DNN/CNN/RNN model training — not yet wired into execution.

Schema (one row per closed trade):
  symbol, contract, session, direction,
  entry, stop, target_1, target_2, target_3,
  risk_reward_ratio, htf_bias,
  fvg_present, fvg_size_pct, fvg_type,
  liquidity_swept, liquidity_side, liquidity_distance_pct,
  choch_present, bos_present, sweep_detected,
  setup_score, session_type, killzone,
  bar_of_day, day_of_week,
  is_news_day, minutes_to_nearest_news,
  is_rollover_week,
  outcome,          ← WINNER | LOSER | BE
  r_multiple,       ← actual R realised
  pnl_net,
  duration_minutes,
  entry_time_utc, exit_time_utc,
  model_prediction  ← filled when ML is active (null until then)
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

logger = logging.getLogger("cb6.futures.ml.trade_logger")

JSONL_PATH = "ml/futures/training_data/trades.jsonl"
CSV_PATH   = "ml/futures/training_data/trades.csv"

# ── Schema ─────────────────────────────────────────────────────────────────────

@dataclass
class FuturesTradeFeatures:
    """All observable features at signal time (inputs to future ML model)."""
    # Identity
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    symbol: str = ""
    contract: str = ""
    entry_time_utc: str = ""

    # Signal context
    session_type: str = ""        # RTH | ETH | PRE_OPEN
    killzone: str = ""            # NY_OPEN | LONDON_OPEN | etc. | ""
    direction: str = ""           # LONG | SHORT
    htf_bias: str = ""            # BULLISH | BEARISH | NEUTRAL
    day_of_week: int = 0          # 0=Mon … 4=Fri
    bar_of_day: int = 0           # which 1m bar of the session (0-indexed)

    # Price levels
    entry: float = 0.0
    stop: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    target_3: float = 0.0
    risk_reward_ratio: float = 0.0  # target_3 / risk

    # Market structure
    choch_present: bool = False
    bos_present: bool = False
    sweep_detected: bool = False
    sweep_side: str = ""          # BSL | SSL | ""

    # FVG
    fvg_present: bool = False
    fvg_type: str = ""            # bull | bear | ""
    fvg_size_pct: float = 0.0     # as % of bar range

    # Liquidity
    liquidity_swept: bool = False
    liquidity_side: str = ""      # BSL | SSL | ""
    liquidity_distance_pct: float = 0.0

    # News / calendar
    is_news_day: bool = False
    minutes_to_nearest_news: float = 9999.0
    nearest_news_event: str = ""

    # Rollover
    is_rollover_week: bool = False

    # Setup quality
    setup_score: float = 0.0      # 0-100 Silver Bullet score


@dataclass
class FuturesTradeOutcome:
    """Outcome fields filled after trade closes."""
    trade_id: str = ""
    exit_time_utc: str = ""
    outcome: str = ""             # WINNER | LOSER | BE
    r_multiple: float = 0.0       # realised R
    pnl_net: float = 0.0
    duration_minutes: float = 0.0
    exit_reason: str = ""         # SL | T1 | T2 | T3 | EOD_FLAT | MANUAL
    model_prediction: Optional[float] = None  # future: predicted win prob


@dataclass
class FuturesTradeRecord:
    features: FuturesTradeFeatures
    outcome: Optional[FuturesTradeOutcome] = None

    def is_closed(self) -> bool:
        return self.outcome is not None

    def to_flat_dict(self) -> dict:
        d = asdict(self.features)
        if self.outcome:
            d.update(asdict(self.outcome))
        return d


# ── Logger ─────────────────────────────────────────────────────────────────────

class FuturesMLTradeLogger:
    """
    Buffers open trade features and writes completed records to JSONL + CSV.
    Thread-safe via file append (no in-memory state shared between processes).
    """

    CSV_FIELDNAMES = [
        "trade_id", "symbol", "contract", "entry_time_utc", "exit_time_utc",
        "session_type", "killzone", "direction", "htf_bias",
        "day_of_week", "bar_of_day",
        "entry", "stop", "target_1", "target_2", "target_3", "risk_reward_ratio",
        "choch_present", "bos_present", "sweep_detected", "sweep_side",
        "fvg_present", "fvg_type", "fvg_size_pct",
        "liquidity_swept", "liquidity_side", "liquidity_distance_pct",
        "is_news_day", "minutes_to_nearest_news", "nearest_news_event",
        "is_rollover_week", "setup_score",
        "outcome", "r_multiple", "pnl_net", "duration_minutes",
        "exit_reason", "model_prediction",
    ]

    def __init__(
        self,
        jsonl_path: str = JSONL_PATH,
        csv_path: str = CSV_PATH,
    ):
        self._jsonl = jsonl_path
        self._csv   = csv_path
        self._open_trades: dict[str, FuturesTradeRecord] = {}
        os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
        self._ensure_csv_header()

    def _ensure_csv_header(self) -> None:
        if not os.path.exists(self._csv) or os.path.getsize(self._csv) == 0:
            with open(self._csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDNAMES)
                writer.writeheader()

    def log_entry(self, features: FuturesTradeFeatures) -> str:
        """Call when a signal fires. Returns trade_id."""
        features.entry_time_utc = features.entry_time_utc or datetime.now(timezone.utc).isoformat()
        self._open_trades[features.trade_id] = FuturesTradeRecord(features=features)
        logger.debug("ML entry logged: %s %s %s", features.trade_id, features.symbol, features.direction)
        return features.trade_id

    def log_exit(
        self,
        trade_id: str,
        pnl_net: float,
        r_multiple: float,
        exit_reason: str,
        exit_time_utc: Optional[datetime] = None,
        model_prediction: Optional[float] = None,
    ) -> Optional[FuturesTradeRecord]:
        if trade_id not in self._open_trades:
            logger.warning("ML exit: trade_id '%s' not in open trades", trade_id)
            return None

        record = self._open_trades.pop(trade_id)
        exit_ts = (exit_time_utc or datetime.now(timezone.utc)).isoformat()

        entry_ts_str = record.features.entry_time_utc or ""
        duration = 0.0
        if entry_ts_str:
            try:
                entry_dt = datetime.fromisoformat(entry_ts_str)
                exit_dt  = datetime.fromisoformat(exit_ts)
                duration = (exit_dt - entry_dt).total_seconds() / 60
            except Exception:
                pass

        outcome_str = "BE" if abs(r_multiple) < 0.05 else ("WINNER" if r_multiple > 0 else "LOSER")

        record.outcome = FuturesTradeOutcome(
            trade_id=trade_id,
            exit_time_utc=exit_ts,
            outcome=outcome_str,
            r_multiple=round(r_multiple, 3),
            pnl_net=round(pnl_net, 2),
            duration_minutes=round(duration, 1),
            exit_reason=exit_reason,
            model_prediction=model_prediction,
        )

        self._persist(record)
        return record

    def _persist(self, record: FuturesTradeRecord) -> None:
        flat = record.to_flat_dict()

        # JSONL
        with open(self._jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(flat, default=str) + "\n")

        # CSV
        with open(self._csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDNAMES, extrasaction="ignore")
            writer.writerow(flat)

        logger.debug("ML record persisted: %s %s r=%.2f",
                     flat.get("trade_id"), flat.get("outcome"), flat.get("r_multiple", 0))

    def load_all(self) -> List[dict]:
        """Load all completed trade records from JSONL."""
        if not os.path.exists(self._jsonl):
            return []
        records = []
        with open(self._jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def stats(self) -> dict:
        records = self.load_all()
        if not records:
            return {"total": 0}
        winners = [r for r in records if r.get("outcome") == "WINNER"]
        losers  = [r for r in records if r.get("outcome") == "LOSER"]
        avg_r   = sum(r.get("r_multiple", 0) for r in records) / len(records)
        by_session = {}
        for r in records:
            s = r.get("session_type", "UNKNOWN")
            if s not in by_session:
                by_session[s] = {"total": 0, "wins": 0}
            by_session[s]["total"] += 1
            if r.get("outcome") == "WINNER":
                by_session[s]["wins"] += 1
        return {
            "total": len(records),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(len(winners) / len(records), 4),
            "avg_r": round(avg_r, 3),
            "open_trades": len(self._open_trades),
            "by_session": by_session,
        }
