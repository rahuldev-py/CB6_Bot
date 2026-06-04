from __future__ import annotations

from typing import Any, Dict, Optional

from settings import (
    CB6_MEMORY_V1_ENABLED,
    CB6_REGIME_V1_ENABLED,
    CB6_SETUP_DNA_V1_ENABLED,
)
from utils.logger import logger

from ml_engine.memory.isolated_store import IsolatedMemoryStoreV1
from ml_engine.memory.regime_contract import normalize_regime
from ml_engine.memory.schema_v1 import SetupDNA


def _enabled() -> bool:
    return bool(CB6_MEMORY_V1_ENABLED)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def build_setup_dna(payload: Dict[str, Any]) -> SetupDNA:
    entry = payload.get("entry_signal") or {}
    liq = payload.get("liq_sweep") or {}
    sq = payload.get("sweep_quality") or liq.get("quality") or {}
    mss = payload.get("mss") or {}
    fvg = payload.get("fvg") or {}
    pd = payload.get("premium_discount") or {}
    regime_raw = payload.get("regime") or payload.get("market_regime") or "UNKNOWN"
    regime = normalize_regime(regime_raw) if CB6_REGIME_V1_ENABLED else str(regime_raw)
    return SetupDNA(
        regime=regime,
        session=str(payload.get("session") or payload.get("window") or "UNKNOWN"),
        sweep_type=str(liq.get("sweep_type") or sq.get("type") or "UNKNOWN"),
        sweep_quality=_safe_float(sq.get("confidence") or payload.get("sweep_confidence") or payload.get("sweep_quality"), 0.0),
        mss_quality=_safe_float(mss.get("strength") or payload.get("mss_quality") or payload.get("confluence"), 0.0),
        bos_quality=_safe_float(payload.get("bos_quality") or payload.get("confluence"), 0.0),
        fvg_bucket=str(fvg.get("bucket") or fvg.get("category") or ("DISPLACED" if fvg.get("displacement") else "PLAIN")),
        htf_bias=str(payload.get("htf_bias") or pd.get("zone") or "UNKNOWN"),
    )


def log_scanner_outcome(
    market: str,
    engine: str,
    symbol: str,
    setup: Optional[Dict[str, Any]],
    *,
    outcome: str,
    reason: str = "",
) -> None:
    if not _enabled():
        return
    try:
        payload = setup or {}
        dna = build_setup_dna(payload) if CB6_SETUP_DNA_V1_ENABLED else SetupDNA(
            regime="UNKNOWN", session="UNKNOWN", sweep_type="UNKNOWN",
            sweep_quality=0.0, mss_quality=0.0, bos_quality=0.0,
            fvg_bucket="UNKNOWN", htf_bias="UNKNOWN"
        )
        result = "OPEN" if str(outcome).upper() == "SCANNER_PASS" else "LOSS"
        IsolatedMemoryStoreV1(market).append_trade_event(
            engine=engine,
            symbol=symbol,
            session=str(payload.get("session") or payload.get("window") or "SCANNER"),
            direction=str(payload.get("direction") or "BUY"),
            regime=payload.get("regime") or payload.get("market_regime") or "UNKNOWN",
            setup_dna=dna,
            ml_score=_safe_float(payload.get("ml_score") or payload.get("confluence"), 0.0),
            result=result,
            rr_achieved=None,
            metadata={
                "event_type": "scanner_outcome",
                "outcome": outcome,
                "reason": reason,
                "entry_signal": payload.get("entry_signal") or {},
            },
        )
    except Exception as e:
        logger.debug(f"Shadow scanner log skipped: {e}")


def log_closed_trade(
    market: str,
    engine: str,
    trade: Dict[str, Any],
    *,
    result: str,
    rr_achieved: Optional[float],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not _enabled():
        return
    try:
        payload = dict(trade or {})
        dna = build_setup_dna(payload) if CB6_SETUP_DNA_V1_ENABLED else SetupDNA(
            regime="UNKNOWN", session="UNKNOWN", sweep_type="UNKNOWN",
            sweep_quality=0.0, mss_quality=0.0, bos_quality=0.0,
            fvg_bucket="UNKNOWN", htf_bias="UNKNOWN"
        )
        IsolatedMemoryStoreV1(market).append_trade_event(
            engine=engine,
            symbol=str(payload.get("symbol") or payload.get("underlying") or "UNKNOWN"),
            session=str(payload.get("session") or payload.get("window") or "UNKNOWN"),
            direction=str(payload.get("direction") or "BUY"),
            regime=payload.get("regime") or payload.get("market_regime") or "UNKNOWN",
            setup_dna=dna,
            ml_score=_safe_float(payload.get("ml_score") or payload.get("confluence"), 0.0),
            result=str(result).upper(),
            rr_achieved=rr_achieved,
            metadata={"event_type": "trade_closed", **(metadata or {})},
        )
    except Exception as e:
        logger.debug(f"Shadow close-trade log skipped: {e}")

