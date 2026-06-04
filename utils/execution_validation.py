import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, List, Any

from utils.logger import logger
from utils.state_io import state_lock


SIGNAL_NEW = 'NEW'
SIGNAL_VALIDATING = 'VALIDATING'
SIGNAL_ARMED = 'ARMED'
SIGNAL_WAITING_CONFIRM = 'WAITING_FOR_MANUAL_CONFIRMATION'
SIGNAL_APPROVED = 'APPROVED'
SIGNAL_EXECUTED = 'EXECUTED'
SIGNAL_REJECTED = 'REJECTED'
SIGNAL_EXPIRED = 'EXPIRED'
SIGNAL_INVALIDATED = 'INVALIDATED'
SIGNAL_MISSED = 'MISSED'


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
STATE_FILE = os.path.join(DATA_DIR, 'execution_signals.json')
AUDIT_LOG_FILE = os.path.join(DATA_DIR, 'execution_validation_audit.jsonl')


def _utc_now() -> datetime:
    return datetime.utcnow()


def _iso_now() -> str:
    return _utc_now().isoformat()


def _default_state() -> Dict:
    return {'signals': {}}


def _append_audit(record: Dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    line = (
        f"{record.get('timestamp','')}\t{record.get('signal_id','')}\t{record.get('symbol','')}\t"
        f"{record.get('direction','')}\t{record.get('planned_entry','')}\t{record.get('current_ltp','')}\t"
        f"{record.get('stop_loss','')}\t{record.get('target','')}\t{record.get('calculated_rr','')}\t"
        f"{record.get('signal_age','')}\t{record.get('status','')}\t{record.get('reason','')}\n"
    )
    try:
        with open(AUDIT_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as e:
        logger.exception(f"execution validation audit write failed: {e}")


def _build_audit(signal: Dict, status: str, reason: str) -> Dict:
    return {
        'timestamp': _iso_now(),
        'signal_id': signal.get('signal_id'),
        'symbol': signal.get('symbol'),
        'direction': signal.get('direction'),
        'planned_entry': signal.get('planned_entry'),
        'current_ltp': signal.get('current_ltp'),
        'stop_loss': signal.get('stop_loss'),
        'target': signal.get('target'),
        'calculated_rr': signal.get('calculated_rr'),
        'signal_age': signal.get('signal_age_seconds'),
        'status': status,
        'reason': reason,
    }


def _entry_band(planned_entry: float, max_drift_points: float, max_drift_pct: float) -> Tuple[float, float]:
    pct_band = abs(planned_entry) * max_drift_pct / 100.0
    band = max(max_drift_points, pct_band)
    return planned_entry - band, planned_entry + band


def _is_long(direction: str) -> bool:
    d = str(direction or '').upper()
    return d in ('BUY', 'BULLISH', 'LONG')


def _compute_signal_age_seconds(created_at_iso: str, ref_time: Optional[datetime] = None) -> int:
    try:
        created = datetime.fromisoformat(created_at_iso)
        now = ref_time if ref_time is not None else _utc_now()
        return max(0, int((now - created).total_seconds()))
    except Exception:
        return 10**9


def _validate(signal: Dict, cfg: Dict, ref_time: Optional[datetime] = None) -> Tuple[str, str]:
    required = ('planned_entry', 'current_ltp', 'stop_loss', 'target', 'direction')
    for k in required:
        if signal.get(k) is None:
            return SIGNAL_REJECTED, f"MISSING_REQUIRED_VALUE:{k}"

    planned_entry = float(signal['planned_entry'])
    ltp = float(signal['current_ltp'])
    stop_loss = float(signal['stop_loss'])
    target = float(signal['target'])
    direction = str(signal['direction'])
    is_long = _is_long(direction)

    signal_age = _compute_signal_age_seconds(signal['created_at'], ref_time=ref_time)
    signal['signal_age_seconds'] = signal_age
    if signal_age > int(cfg['allowed_signal_age_seconds']):
        return SIGNAL_EXPIRED, 'SIGNAL_EXPIRED'

    band_low, band_high = _entry_band(
        planned_entry=planned_entry,
        max_drift_points=float(cfg['max_entry_drift_points']),
        max_drift_pct=float(cfg['max_entry_drift_percent']),
    )
    signal['entry_band_low'] = round(band_low, 6)
    signal['entry_band_high'] = round(band_high, 6)
    if ltp < band_low or ltp > band_high:
        return SIGNAL_MISSED, 'LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM'

    invalidation_buffer = float(cfg['invalidation_buffer_points'])
    risk_to_stop = abs(planned_entry - stop_loss)
    effective_buffer = min(invalidation_buffer, risk_to_stop * 0.5)
    # Structure invalidation should only trigger after adverse pre-entry drift.
    # Long: price moved down toward SL before entry.
    # Short: price moved up toward SL before entry.
    if is_long and ltp < planned_entry and ltp <= stop_loss + effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP'
    if (not is_long) and ltp > planned_entry and ltp >= stop_loss - effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP'

    # RR and sanity computed at planned_entry (the limit order fill price), not ltp.
    # The entry-band check above already confirms ltp is close enough for a fill.
    # Using ltp would inflate risk and crush reward whenever price closes above fvg_low
    # (normal inside-FVG bar close), causing false RR_DAMAGED rejections on limit strategies.
    rr_entry = planned_entry
    risk = abs(rr_entry - stop_loss)
    reward = abs(target - rr_entry)
    if risk <= 0:
        return SIGNAL_REJECTED, 'INVALID_RISK_ZERO'
    rr = reward / risk
    signal['calculated_rr'] = round(rr, 4)

    min_rr = float(cfg['minimum_required_rr'])
    if rr < min_rr:
        return SIGNAL_REJECTED, 'RR_DAMAGED_BELOW_MINIMUM'

    if is_long:
        if not (stop_loss < rr_entry and target > rr_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_LONG'
    else:
        if not (stop_loss > rr_entry and target < rr_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_SHORT'

    return SIGNAL_WAITING_CONFIRM, 'READY_FOR_MANUAL_CONFIRMATION'


def create_signal(setup: Dict, current_ltp: float, config: Dict) -> Dict:
    sig = setup.get('entry_signal', {}) or {}
    signal = {
        'signal_id': uuid.uuid4().hex[:10].upper(),
        'state': SIGNAL_NEW,
        'created_at': _iso_now(),
        'updated_at': _iso_now(),
        'symbol': setup.get('symbol'),
        'direction': setup.get('direction'),
        'planned_entry': sig.get('entry'),
        'current_ltp': current_ltp,
        'stop_loss': sig.get('stop_loss'),
        'target': sig.get('target2') or sig.get('target1') or sig.get('target3'),
        'target1': sig.get('target1'),
        'target2': sig.get('target2'),
        'target3': sig.get('target3'),
        'status_reason': '',
        'signal_age_seconds': 0,
        'calculated_rr': None,
        'setup': setup,
    }
    signal['state'] = SIGNAL_VALIDATING
    status, reason = _validate(signal, config)
    signal['state'] = SIGNAL_ARMED if status == SIGNAL_WAITING_CONFIRM else status
    if signal['state'] == SIGNAL_ARMED:
        signal['state'] = SIGNAL_WAITING_CONFIRM
    signal['status_reason'] = reason
    signal['updated_at'] = _iso_now()

    with state_lock(STATE_FILE, default=_default_state()) as state:
        state.setdefault('signals', {})
        state['signals'][signal['signal_id']] = signal

    _append_audit(_build_audit(signal, signal['state'], reason))
    return signal


def get_signal(signal_id: str) -> Optional[Dict]:
    with state_lock(STATE_FILE, default=_default_state()) as state:
        return (state.get('signals') or {}).get(signal_id)


def update_signal(signal_id: str, state_value: str, reason: str, current_ltp: Optional[float] = None) -> Optional[Dict]:
    with state_lock(STATE_FILE, default=_default_state()) as state:
        signals = state.setdefault('signals', {})
        sig = signals.get(signal_id)
        if not sig:
            return None
        sig['state'] = state_value
        sig['status_reason'] = reason
        if current_ltp is not None:
            sig['current_ltp'] = current_ltp
        sig['updated_at'] = _iso_now()
        signals[signal_id] = sig
        _append_audit(_build_audit(sig, state_value, reason))
        return sig


def revalidate_existing(signal: Dict, current_ltp: float, config: Dict, ref_time: Optional[datetime] = None) -> Tuple[str, str, Dict]:
    signal = dict(signal)
    signal['current_ltp'] = current_ltp
    status, reason = _validate(signal, config, ref_time=ref_time)
    return status, reason, signal


def list_signals_by_state(state_value: str) -> list:
    with state_lock(STATE_FILE, default=_default_state()) as state:
        signals = (state.get('signals') or {}).values()
        return [s for s in signals if s.get('state') == state_value]


def cancel_signal(signal_id: str, reason: str = "USER_CANCELLED") -> Optional[Dict]:
    return update_signal(signal_id=signal_id, state_value=SIGNAL_REJECTED, reason=reason)


def get_execution_stats_for_date(date_str: Optional[str] = None) -> Dict:
    """
    Build execution-validation summary from audit log for a given UTC date.
    date_str format: YYYY-MM-DD. Default: today (UTC).
    """
    if date_str is None:
        date_str = _utc_now().strftime('%Y-%m-%d')

    stats = {
        'date': date_str,
        'total_signals': 0,
        'blocked_count': 0,
        'approved_count': 0,
        'executed_count': 0,
        'block_rate_pct': 0.0,
        'blocked_reason_breakdown': {},
    }
    blocked_states = {SIGNAL_MISSED, SIGNAL_REJECTED, SIGNAL_INVALIDATED, SIGNAL_EXPIRED}

    # Track latest state/reason per signal for the selected date.
    latest_by_signal: Dict[str, Dict] = {}
    if not os.path.exists(AUDIT_LOG_FILE):
        return stats

    try:
        with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) < 12:
                    continue
                ts, signal_id, symbol, direction, planned_entry, current_ltp, stop_loss, target, rr, age, status, reason = parts[:12]
                day = ts[:10]
                if day != date_str:
                    continue
                latest_by_signal[signal_id] = {
                    'status': status,
                    'reason': reason,
                }
    except Exception as e:
        logger.exception(f"execution stats read failed: {e}")
        return stats

    stats['total_signals'] = len(latest_by_signal)

    for rec in latest_by_signal.values():
        st = rec.get('status')
        rs = rec.get('reason', '')
        if st in blocked_states:
            stats['blocked_count'] += 1
            stats['blocked_reason_breakdown'][rs] = stats['blocked_reason_breakdown'].get(rs, 0) + 1
        if st == SIGNAL_APPROVED:
            stats['approved_count'] += 1
        if st == SIGNAL_EXECUTED:
            stats['executed_count'] += 1

    if stats['total_signals'] > 0:
        stats['block_rate_pct'] = round((stats['blocked_count'] / stats['total_signals']) * 100.0, 2)

    return stats


def patch_signal(signal_id: str, fields: Dict, state_value: Optional[str] = None, reason: Optional[str] = None) -> Optional[Dict]:
    """Patch arbitrary signal fields atomically, optionally updating state/reason."""
    with state_lock(STATE_FILE, default=_default_state()) as state:
        signals = state.setdefault('signals', {})
        sig = signals.get(signal_id)
        if not sig:
            return None
        sig.update(fields or {})
        if state_value is not None:
            sig['state'] = state_value
        if reason is not None:
            sig['status_reason'] = reason
        sig['updated_at'] = _iso_now()
        signals[signal_id] = sig
        if state_value is not None or reason is not None:
            _append_audit(_build_audit(sig, sig.get('state', ''), sig.get('status_reason', '')))
        return sig


def revalidate_for_auto(signal: Dict, current_ltp: float, config: Dict) -> Tuple[str, str, Dict]:
    """
    Auto-execution revalidation variant:
    - Age reason -> RNN_SEQUENCE_EXPIRED
    - RR computed from planned_entry (not LTP) by design for limit-order planning
    """
    signal = dict(signal)
    signal['current_ltp'] = current_ltp

    required = ('planned_entry', 'current_ltp', 'stop_loss', 'target', 'direction')
    for k in required:
        if signal.get(k) is None:
            return SIGNAL_REJECTED, f"MISSING_REQUIRED_VALUE:{k}", signal

    planned_entry = float(signal['planned_entry'])
    ltp = float(signal['current_ltp'])
    stop_loss = float(signal['stop_loss'])
    target = float(signal['target'])
    direction = str(signal['direction'])
    is_long = _is_long(direction)

    signal_age = _compute_signal_age_seconds(signal.get('created_at', ''))
    signal['signal_age_seconds'] = signal_age
    if signal_age > int(config['allowed_signal_age_seconds']):
        return SIGNAL_EXPIRED, 'RNN_SEQUENCE_EXPIRED', signal

    band_low, band_high = _entry_band(
        planned_entry=planned_entry,
        max_drift_points=float(config['max_entry_drift_points']),
        max_drift_pct=float(config['max_entry_drift_percent']),
    )
    signal['entry_band_low'] = round(band_low, 6)
    signal['entry_band_high'] = round(band_high, 6)
    if ltp < band_low or ltp > band_high:
        return SIGNAL_MISSED, 'LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM', signal

    invalidation_buffer = float(config['invalidation_buffer_points'])
    risk_to_stop = abs(planned_entry - stop_loss)
    effective_buffer = min(invalidation_buffer, risk_to_stop * 0.5)
    if is_long and ltp < planned_entry and ltp <= stop_loss + effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP', signal
    if (not is_long) and ltp > planned_entry and ltp >= stop_loss - effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP', signal

    # Planned-entry RR matrix per SAFE_VALIDATION_REVALIDATE_AUTO requirement.
    risk = abs(planned_entry - stop_loss)
    reward = abs(target - planned_entry)
    if risk <= 0:
        return SIGNAL_REJECTED, 'INVALID_RISK_ZERO', signal
    rr = reward / risk
    signal['calculated_rr'] = round(rr, 4)
    if rr < float(config['minimum_required_rr']):
        return SIGNAL_REJECTED, 'RR_DAMAGED_BELOW_MINIMUM', signal

    if is_long:
        if not (stop_loss < planned_entry and target > planned_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_LONG', signal
    else:
        if not (stop_loss > planned_entry and target < planned_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_SHORT', signal

    return SIGNAL_WAITING_CONFIRM, 'READY_FOR_AUTO_REVALIDATION_EXECUTION', signal


def _is_indian_symbol(symbol: str) -> bool:
    s = str(symbol or '').upper()
    return (
        s.startswith('NSE:') or
        s.startswith('BSE:') or
        s.startswith('MCX:') or
        'NIFTY' in s or
        'SENSEX' in s
    )


def _reason_bucket(reason: str) -> str:
    r = str(reason or '')
    if 'LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM' in r:
        return 'REVALIDATION:LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM'
    if 'STRUCTURE_INVALIDATED_NEAR_STOP' in r:
        return 'REVALIDATION:STRUCTURE_INVALIDATED_NEAR_STOP'
    if 'RNN_SEQUENCE_EXPIRED' in r or 'SIGNAL_EXPIRED' in r:
        return 'REVALIDATION:RNN_SEQUENCE_EXPIRED'
    if 'RR_DAMAGED_BELOW_MINIMUM' in r:
        return 'REVALIDATION:RR_DAMAGED_BELOW_MINIMUM'
    if 'SANITY_CHECK_SPREAD_TOO_WIDE' in r:
        return 'REVALIDATION:MAX_SPREAD_EXCEEDED'
    if 'STOP_TARGET_SANITY_FAILED' in r:
        return 'REVALIDATION:STOP_TARGET_SANITY_FAILED'
    return f"OTHER:{r}" if r else "OTHER:UNKNOWN"


def get_execution_report_for_date(date_str: Optional[str] = None) -> Dict:
    """
    Compact telemetry report for Indian-engine execution pipeline.
    Reads:
    - STATE_FILE for currently ARMED count
    - AUDIT_LOG_FILE for today's signal lifecycle outcomes
    """
    if date_str is None:
        date_str = _utc_now().strftime('%Y-%m-%d')

    blocked_states = {SIGNAL_MISSED, SIGNAL_REJECTED, SIGNAL_INVALIDATED, SIGNAL_EXPIRED}
    latest_by_signal: Dict[str, Dict] = {}
    total_signals_received = set()

    if os.path.exists(AUDIT_LOG_FILE):
        try:
            with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) < 12:
                        continue
                    ts, signal_id, symbol, direction, planned_entry, current_ltp, stop_loss, target, rr, age, status, reason = parts[:12]
                    if ts[:10] != date_str:
                        continue
                    if not _is_indian_symbol(symbol):
                        continue
                    total_signals_received.add(signal_id)
                    latest_by_signal[signal_id] = {
                        'symbol': symbol,
                        'status': status,
                        'reason': reason,
                    }
        except Exception as e:
            logger.exception(f"execution report read failed: {e}")

    currently_armed = 0
    try:
        with state_lock(STATE_FILE, default=_default_state()) as state:
            signals = (state.get('signals') or {}).values()
            currently_armed = sum(
                1 for s in signals
                if s.get('state') == SIGNAL_ARMED and _is_indian_symbol(s.get('symbol', ''))
            )
    except Exception as e:
        logger.exception(f"execution report state read failed: {e}")

    executed_count = 0
    blocked_count = 0
    blocked_reason_breakdown: Dict[str, int] = {}
    for rec in latest_by_signal.values():
        st = rec.get('status')
        rs = rec.get('reason', '')
        if st == SIGNAL_EXECUTED:
            executed_count += 1
        if st in blocked_states:
            blocked_count += 1
            bk = _reason_bucket(rs)
            blocked_reason_breakdown[bk] = blocked_reason_breakdown.get(bk, 0) + 1

    return {
        'date': date_str,
        'total_signals_received': len(total_signals_received),
        'currently_armed': currently_armed,
        'executed_count': executed_count,
        'blocked_count': blocked_count,
        'blocked_reason_breakdown': blocked_reason_breakdown,
    }


def get_pipeline_telemetry(date_str: Optional[str] = None) -> Dict:
    """
    Compact telemetry payload for command handlers.
    Thread-safe by construction via state_lock/read-only audit scan.
    """
    r = get_execution_report_for_date(date_str=date_str)
    return {
        "date": r.get("date"),
        "total_signals_received": r.get("total_signals_received", 0),
        "currently_armed": r.get("currently_armed", 0),
        "executed_count": r.get("executed_count", 0),
        "blocked_count": r.get("blocked_count", 0),
        "breakdown": r.get("blocked_reason_breakdown", {}),
    }


# Forex-specific execution validation state/audit files.
FOREX_STATE_FILE = os.path.join(DATA_DIR, 'forex_execution_signals.json')
FOREX_AUDIT_LOG_FILE = os.path.join(DATA_DIR, 'forex_execution_validation_audit.jsonl')
FOREX_CME_PROXY_MAP = {
    'XAUUSD': 'GC=F',
    'GOLD': 'GC=F',
    'XAGUSD': 'SI=F',
    'SILVER': 'SI=F',
    'USOIL': 'CL=F',
    'CRUDE': 'CL=F',
}
SIGNAL_BLOCKED = 'BLOCKED'


def _forex_default_state() -> Dict[str, Any]:
    return {'signals': {}}


def _utc_now_aware() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now_aware() -> str:
    return _utc_now_aware().isoformat()


def _parse_iso_dt(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _jsonl_append(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str) + '\n')
    except Exception as e:
        logger.exception(f"forex execution audit write failed: {e}")


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or '').strip().upper().replace('/', '').replace('-', '')


def _to_hhmm_minutes(hhmm: str) -> Optional[int]:
    try:
        h_s, m_s = str(hhmm).split(':', 1)
        h = int(h_s)
        m = int(m_s)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h * 60 + m
    except Exception:
        return None


def _in_utc_windows(ref_time: datetime, windows: List[List[str]]) -> bool:
    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)
    now_m = ref_time.hour * 60 + ref_time.minute
    for window in windows or []:
        if not isinstance(window, list) or len(window) != 2:
            continue
        start_m = _to_hhmm_minutes(window[0])
        end_m = _to_hhmm_minutes(window[1])
        if start_m is None or end_m is None:
            continue
        if start_m <= now_m <= end_m:
            return True
    return False


def _build_forex_audit(signal: Dict[str, Any], status: str, reason: str) -> Dict[str, Any]:
    return {
        'timestamp': _iso_now_aware(),
        'signal_id': signal.get('signal_id'),
        'symbol': signal.get('symbol'),
        'direction': signal.get('direction'),
        'planned_entry': signal.get('planned_entry'),
        'current_ltp': signal.get('current_ltp'),
        'stop_loss': signal.get('stop_loss'),
        'target': signal.get('target'),
        'calculated_rr': signal.get('calculated_rr'),
        'signal_age': signal.get('signal_age_seconds'),
        'state': status,
        'reason': reason,
        'spread_pct': signal.get('spread_pct'),
        'proxy_symbol': signal.get('proxy_symbol'),
        'proxy_note': signal.get('proxy_note', ''),
        'entry_band_low': signal.get('entry_band_low'),
        'entry_band_high': signal.get('entry_band_high'),
    }


def _forex_signal_age_seconds(created_at_iso: str, ref_time: Optional[datetime] = None) -> int:
    created = _parse_iso_dt(created_at_iso)
    if created is None:
        return 10**9
    now = ref_time.astimezone(timezone.utc) if ref_time and ref_time.tzinfo else (ref_time or _utc_now_aware())
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((now - created).total_seconds()))


def _forex_validate(
    signal: Dict[str, Any],
    cfg: Dict[str, Any],
    ref_time: Optional[datetime] = None,
    proxy_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    required = ('symbol', 'planned_entry', 'current_ltp', 'stop_loss', 'target', 'direction', 'created_at')
    for k in required:
        if signal.get(k) is None:
            return SIGNAL_REJECTED, f"MISSING_REQUIRED_VALUE:{k}"

    symbol_raw = str(signal.get('symbol', ''))
    symbol = _normalize_symbol(symbol_raw)
    signal['symbol'] = symbol_raw
    signal['symbol_normalized'] = symbol

    disabled = {_normalize_symbol(s) for s in (cfg.get('disabled_symbols') or [])}
    if symbol in disabled:
        reason_sym = 'XAGUSD' if symbol in ('XAGUSD', 'SILVER') else symbol
        return SIGNAL_BLOCKED, (
            f"[RISK_FILTER][BLOCKED] {reason_sym} dropped due to negative strategy expectancy (PF < 1.0)"
        )

    now_utc = ref_time if ref_time is not None else _utc_now_aware()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    if not _in_utc_windows(now_utc.astimezone(timezone.utc), cfg.get('allowed_utc_windows') or []):
        return SIGNAL_BLOCKED, 'REVALIDATION:LOW_LIQUIDITY_SESSION_WINDOW'

    spread_pct = signal.get('spread_pct')
    if spread_pct is not None:
        try:
            if float(spread_pct) > float(cfg.get('max_spread_pct', 0.0005)):
                return SIGNAL_BLOCKED, 'REVALIDATION:MAX_SPREAD_EXCEEDED'
        except Exception:
            return SIGNAL_REJECTED, 'MISSING_REQUIRED_VALUE:spread_pct'

    signal_age = _forex_signal_age_seconds(str(signal.get('created_at', '')), ref_time=now_utc)
    signal['signal_age_seconds'] = signal_age
    if signal_age > int(cfg.get('allowed_signal_age_seconds', 600)):
        return SIGNAL_EXPIRED, 'SIGNAL_EXPIRED'

    planned_entry = float(signal['planned_entry'])
    ltp = float(signal['current_ltp'])
    stop_loss = float(signal['stop_loss'])
    target = float(signal['target'])
    direction = str(signal.get('direction', ''))
    is_long = _is_long(direction)

    band_low, band_high = _entry_band(
        planned_entry=planned_entry,
        max_drift_points=float(cfg.get('max_entry_drift_points', 0.5)),
        max_drift_pct=float(cfg.get('max_entry_drift_percent', 2.0)),
    )
    signal['entry_band_low'] = round(band_low, 6)
    signal['entry_band_high'] = round(band_high, 6)
    if ltp < band_low or ltp > band_high:
        return SIGNAL_MISSED, 'LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM'

    invalidation_buffer = float(cfg.get('invalidation_buffer_points', 0.5))
    risk_to_stop = abs(planned_entry - stop_loss)
    effective_buffer = min(invalidation_buffer, risk_to_stop * 0.5)
    if is_long and ltp < planned_entry and ltp <= stop_loss + effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP'
    if (not is_long) and ltp > planned_entry and ltp >= stop_loss - effective_buffer:
        return SIGNAL_INVALIDATED, 'STRUCTURE_INVALIDATED_NEAR_STOP'

    risk = abs(planned_entry - stop_loss)
    reward = abs(target - planned_entry)
    if risk <= 0:
        return SIGNAL_REJECTED, 'INVALID_RISK_ZERO'
    rr = reward / risk
    signal['calculated_rr'] = round(rr, 4)
    if rr < float(cfg.get('minimum_required_rr', 1.5)):
        return SIGNAL_REJECTED, 'RR_DAMAGED_BELOW_MINIMUM'

    if is_long:
        if not (stop_loss < planned_entry and target > planned_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_LONG'
    else:
        if not (stop_loss > planned_entry and target < planned_entry):
            return SIGNAL_REJECTED, 'STOP_TARGET_SANITY_FAILED_SHORT'

    proxy_sym = FOREX_CME_PROXY_MAP.get(symbol)
    signal['proxy_symbol'] = proxy_sym
    signal['proxy_note'] = ''
    if proxy_sym:
        if not proxy_snapshot or not proxy_snapshot.get('available', False):
            signal['proxy_note'] = 'PROXY_DATA_UNAVAILABLE'
        else:
            trend = str(proxy_snapshot.get('trend', '')).upper()
            if trend in ('BULLISH', 'BEARISH') and trend != str(direction).upper():
                return SIGNAL_BLOCKED, 'REVALIDATION:PROXY_STRUCTURE_MISMATCH'

    return SIGNAL_WAITING_CONFIRM, 'READY_FOR_AUTO_REVALIDATION_EXECUTION'


def create_forex_signal(
    setup: Dict[str, Any],
    current_ltp: float,
    config: Dict[str, Any],
    spread_pct: Optional[float] = None,
    created_at: Optional[str] = None,
    proxy_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sig = setup.get('entry_signal', {}) or {}
    signal = {
        'signal_id': uuid.uuid4().hex[:12].upper(),
        'state': SIGNAL_NEW,
        'created_at': created_at or _iso_now_aware(),
        'updated_at': _iso_now_aware(),
        'symbol': setup.get('symbol'),
        'direction': setup.get('direction'),
        'planned_entry': sig.get('entry'),
        'current_ltp': current_ltp,
        'stop_loss': sig.get('stop_loss'),
        'target': sig.get('target2') or sig.get('target1') or sig.get('target3'),
        'spread_pct': spread_pct,
        'status_reason': '',
        'signal_age_seconds': 0,
        'calculated_rr': None,
        'setup': setup,
        'proxy_symbol': FOREX_CME_PROXY_MAP.get(_normalize_symbol(setup.get('symbol', ''))),
        'proxy_note': '',
    }
    signal['state'] = SIGNAL_VALIDATING
    status, reason = _forex_validate(signal, config, proxy_snapshot=proxy_snapshot)
    signal['state'] = SIGNAL_ARMED if status == SIGNAL_WAITING_CONFIRM else status
    signal['status_reason'] = reason
    signal['updated_at'] = _iso_now_aware()

    with state_lock(FOREX_STATE_FILE, default=_forex_default_state()) as state:
        state.setdefault('signals', {})
        state['signals'][signal['signal_id']] = signal

    _jsonl_append(FOREX_AUDIT_LOG_FILE, _build_forex_audit(signal, signal['state'], reason))
    return signal


def get_forex_signal(signal_id: str) -> Optional[Dict[str, Any]]:
    with state_lock(FOREX_STATE_FILE, default=_forex_default_state()) as state:
        return (state.get('signals') or {}).get(signal_id)


def update_forex_signal(
    signal_id: str,
    state_value: str,
    reason: str,
    fields: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    with state_lock(FOREX_STATE_FILE, default=_forex_default_state()) as state:
        signals = state.setdefault('signals', {})
        sig = signals.get(signal_id)
        if not sig:
            return None
        if fields:
            sig.update(fields)
        sig['state'] = state_value
        sig['status_reason'] = reason
        sig['updated_at'] = _iso_now_aware()
        signals[signal_id] = sig
        _jsonl_append(FOREX_AUDIT_LOG_FILE, _build_forex_audit(sig, state_value, reason))
        return sig


def revalidate_forex_signal(
    signal: Dict[str, Any],
    current_ltp: float,
    config: Dict[str, Any],
    spread_pct: Optional[float] = None,
    proxy_snapshot: Optional[Dict[str, Any]] = None,
    ref_time: Optional[datetime] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    sig = dict(signal or {})
    sig['current_ltp'] = current_ltp
    if spread_pct is not None:
        sig['spread_pct'] = spread_pct
    status, reason = _forex_validate(sig, config, ref_time=ref_time, proxy_snapshot=proxy_snapshot)
    return status, reason, sig


def list_forex_signals_by_state(state_value: str) -> List[Dict[str, Any]]:
    with state_lock(FOREX_STATE_FILE, default=_forex_default_state()) as state:
        signals = (state.get('signals') or {}).values()
        return [s for s in signals if s.get('state') == state_value]


def get_forex_execution_stats_for_date(date_str: Optional[str] = None) -> Dict[str, Any]:
    if date_str is None:
        date_str = _utc_now_aware().strftime('%Y-%m-%d')

    stats = {
        'date': date_str,
        'total_signals': 0,
        'blocked_count': 0,
        'executed_count': 0,
        'armed_count': 0,
        'block_rate_pct': 0.0,
        'blocked_reason_breakdown': {},
    }
    blocked_states = {SIGNAL_BLOCKED, SIGNAL_MISSED, SIGNAL_REJECTED, SIGNAL_INVALIDATED, SIGNAL_EXPIRED}
    latest_by_signal: Dict[str, Dict[str, str]] = {}

    if not os.path.exists(FOREX_AUDIT_LOG_FILE):
        return stats

    try:
        with open(FOREX_AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ts = str(rec.get('timestamp', ''))
                if ts[:10] != date_str:
                    continue
                sid = str(rec.get('signal_id', ''))
                if not sid:
                    continue
                latest_by_signal[sid] = {
                    'state': str(rec.get('state', '')),
                    'reason': str(rec.get('reason', '')),
                }
    except Exception as e:
        logger.exception(f"forex execution stats read failed: {e}")
        return stats

    stats['total_signals'] = len(latest_by_signal)
    for rec in latest_by_signal.values():
        state_val = rec.get('state')
        reason = rec.get('reason', '')
        if state_val in blocked_states:
            stats['blocked_count'] += 1
            stats['blocked_reason_breakdown'][reason] = (
                stats['blocked_reason_breakdown'].get(reason, 0) + 1
            )
        if state_val == SIGNAL_EXECUTED:
            stats['executed_count'] += 1
        if state_val == SIGNAL_ARMED:
            stats['armed_count'] += 1

    if stats['total_signals'] > 0:
        stats['block_rate_pct'] = round((stats['blocked_count'] / stats['total_signals']) * 100.0, 2)
    return stats
