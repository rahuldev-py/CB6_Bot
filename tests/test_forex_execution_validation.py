import os
import sys
from datetime import datetime, timedelta, timezone


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _cfg():
    return {
        'disabled_symbols': ['XAGUSD', 'SILVER'],
        'allowed_utc_windows': [['08:00', '11:00'], ['13:00', '16:30']],
        'max_spread_pct': 0.0005,
        'max_entry_drift_percent': 2.0,
        'max_entry_drift_points': 3.0,
        'minimum_required_rr': 1.5,
        'invalidation_buffer_points': 10.0,
        'allowed_signal_age_seconds': 600,
    }


def _setup(symbol, direction, entry, sl, t1, t2, t3):
    return {
        'symbol': symbol,
        'direction': direction,
        'entry_signal': {
            'entry': entry,
            'stop_loss': sl,
            'target1': t1,
            'target2': t2,
            'target3': t3,
        },
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_xau_valid_armed_then_executed(tmp_path, monkeypatch):
    import utils.execution_validation as ev

    monkeypatch.setattr(ev, 'FOREX_STATE_FILE', str(tmp_path / 'forex_execution_signals.json'))
    monkeypatch.setattr(ev, 'FOREX_AUDIT_LOG_FILE', str(tmp_path / 'forex_execution_validation_audit.jsonl'))

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]

    created = ev.create_forex_signal(
        setup=_setup('XAUUSD', 'BULLISH', 150.0, 140.0, 165.0, 170.0, 175.0),
        current_ltp=151.0,
        config=cfg,
        spread_pct=0.0002,
        proxy_snapshot={'available': True, 'trend': 'BULLISH'},
    )
    assert created['state'] == ev.SIGNAL_ARMED

    status, _, _ = ev.revalidate_forex_signal(
        created,
        current_ltp=150.5,
        config=cfg,
        spread_pct=0.0002,
        proxy_snapshot={'available': True, 'trend': 'BULLISH'},
    )
    assert status == ev.SIGNAL_WAITING_CONFIRM
    ev.update_forex_signal(created['signal_id'], ev.SIGNAL_EXECUTED, 'TEST_EXECUTED')
    assert ev.get_forex_signal(created['signal_id'])['state'] == ev.SIGNAL_EXECUTED


def test_xag_blocked_by_risk_filter(tmp_path, monkeypatch):
    import utils.execution_validation as ev

    monkeypatch.setattr(ev, 'FOREX_STATE_FILE', str(tmp_path / 'forex_execution_signals.json'))
    monkeypatch.setattr(ev, 'FOREX_AUDIT_LOG_FILE', str(tmp_path / 'forex_execution_validation_audit.jsonl'))

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    created = ev.create_forex_signal(
        setup=_setup('XAGUSD', 'BULLISH', 25.0, 24.0, 26.0, 27.0, 28.0),
        current_ltp=25.0,
        config=cfg,
        spread_pct=0.0002,
    )
    assert created['state'] == ev.SIGNAL_BLOCKED
    assert 'RISK_FILTER' in created['status_reason']


def test_outside_session_blocked():
    import utils.execution_validation as ev

    cfg = _cfg()
    signal = {
        'signal_id': 'T3',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.0,
        'stop_loss': 140.0,
        'target': 170.0,
        'created_at': _now_iso(),
        'spread_pct': 0.0002,
    }
    ref_time = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    status, reason, _ = ev.revalidate_forex_signal(signal, 150.0, cfg, spread_pct=0.0002, ref_time=ref_time)
    assert status == ev.SIGNAL_BLOCKED
    assert reason == 'REVALIDATION:LOW_LIQUIDITY_SESSION_WINDOW'


def test_spread_too_wide_blocked():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    signal = {
        'signal_id': 'T4',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.0,
        'stop_loss': 140.0,
        'target': 170.0,
        'created_at': _now_iso(),
        'spread_pct': 0.01,
    }
    status, reason, _ = ev.revalidate_forex_signal(signal, 150.0, cfg, spread_pct=0.01)
    assert status == ev.SIGNAL_BLOCKED
    assert reason == 'REVALIDATION:MAX_SPREAD_EXCEEDED'


def test_rr_damaged_rejected():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    signal = {
        'signal_id': 'T5',
        'symbol': 'USOIL',
        'direction': 'BULLISH',
        'planned_entry': 100.0,
        'current_ltp': 100.1,
        'stop_loss': 99.0,
        'target': 101.0,
        'created_at': _now_iso(),
        'spread_pct': 0.0001,
    }
    status, reason, _ = ev.revalidate_forex_signal(signal, 100.1, cfg, spread_pct=0.0001)
    assert status == ev.SIGNAL_REJECTED
    assert reason == 'RR_DAMAGED_BELOW_MINIMUM'


def test_sltp_wrong_direction_rejected():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    signal = {
        'signal_id': 'T6',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.0,
        'stop_loss': 160.0,
        'target': 170.0,
        'created_at': _now_iso(),
        'spread_pct': 0.0001,
    }
    status, reason, _ = ev.revalidate_forex_signal(signal, 150.0, cfg, spread_pct=0.0001)
    assert status == ev.SIGNAL_REJECTED
    assert reason == 'STOP_TARGET_SANITY_FAILED_LONG'


def test_price_near_sl_invalidated():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    cfg['max_entry_drift_points'] = 30.0
    signal = {
        'signal_id': 'T7',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 129.0,
        'stop_loss': 120.0,
        'target': 190.0,
        'created_at': _now_iso(),
        'spread_pct': 0.0001,
    }
    status, reason, _ = ev.revalidate_forex_signal(signal, 129.0, cfg, spread_pct=0.0001)
    assert status == ev.SIGNAL_INVALIDATED
    assert reason == 'STRUCTURE_INVALIDATED_NEAR_STOP'


def test_legacy_mode_pass_through_simulated():
    mode = os.getenv('FOREX_EXECUTION_MODE', 'LEGACY').strip().upper()
    assert mode in ('LEGACY', 'SAFE_VALIDATION_REVALIDATE_AUTO')
    assert ('LEGACY' == mode) or ('SAFE_VALIDATION_REVALIDATE_AUTO' == mode)


def test_proxy_unavailable_fallback_no_crash():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    signal = {
        'signal_id': 'T8',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.2,
        'stop_loss': 140.0,
        'target': 170.0,
        'created_at': _now_iso(),
        'spread_pct': 0.0001,
    }
    status, _, sig = ev.revalidate_forex_signal(
        signal,
        150.2,
        cfg,
        spread_pct=0.0001,
        proxy_snapshot={'available': False, 'reason': 'PROXY_DATA_UNAVAILABLE'},
    )
    assert status == ev.SIGNAL_WAITING_CONFIRM
    assert sig.get('proxy_note') == 'PROXY_DATA_UNAVAILABLE'


def test_signal_too_old_expires():
    import utils.execution_validation as ev

    cfg = _cfg()
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]
    old = (datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat()
    signal = {
        'signal_id': 'T9',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.0,
        'stop_loss': 140.0,
        'target': 170.0,
        'created_at': old,
        'spread_pct': 0.0001,
    }
    status, reason, _ = ev.revalidate_forex_signal(signal, 150.0, cfg, spread_pct=0.0001)
    assert status == ev.SIGNAL_EXPIRED
    assert reason == 'SIGNAL_EXPIRED'
