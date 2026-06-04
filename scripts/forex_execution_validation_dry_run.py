import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.execution_validation import (
    FOREX_STATE_FILE,
    FOREX_AUDIT_LOG_FILE,
    SIGNAL_ARMED,
    SIGNAL_WAITING_CONFIRM,
    SIGNAL_EXECUTED,
    SIGNAL_REJECTED,
    SIGNAL_EXPIRED,
    SIGNAL_INVALIDATED,
    SIGNAL_MISSED,
    SIGNAL_BLOCKED,
    create_forex_signal,
    get_forex_signal,
    update_forex_signal,
    revalidate_forex_signal,
)
from utils.state_io import state_lock


CFG = {
    'disabled_symbols': ['XAGUSD', 'SILVER'],
    'allowed_utc_windows': [['08:00', '11:00'], ['13:00', '16:30']],
    'max_spread_pct': 0.0005,
    'max_entry_drift_percent': 2.0,
    'max_entry_drift_points': 3.0,
    'minimum_required_rr': 1.5,
    'invalidation_buffer_points': 10.0,
    'allowed_signal_age_seconds': 600,
}


def mk_setup(symbol, direction, entry, sl, t1, t2, t3):
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


def print_result(name, signal):
    print(
        f"{name}: state={signal.get('state')} reason={signal.get('status_reason')} "
        f"rr={signal.get('calculated_rr')} proxy={signal.get('proxy_note', '')} "
        f"id={signal.get('signal_id')}"
    )


def run():
    with state_lock(FOREX_STATE_FILE, default={'signals': {}}) as st:
        st['signals'] = {}

    print("Forex Execution Validation Dry Run")
    print("=" * 42)

    cfg = dict(CFG)
    cfg['allowed_utc_windows'] = [['00:00', '23:59']]

    # 1) Valid entry in band -> ARMED then EXECUTED (simulated)
    s1 = create_forex_signal(
        setup=mk_setup('XAUUSD', 'BULLISH', 150, 140, 160, 170, 180),
        current_ltp=150.8,
        config=cfg,
        spread_pct=0.0002,
        proxy_snapshot={'available': True, 'trend': 'BULLISH'},
    )
    if s1.get('state') == SIGNAL_ARMED:
        st1, rs1, _ = revalidate_forex_signal(
            s1, 150.6, cfg, spread_pct=0.0002, proxy_snapshot={'available': True, 'trend': 'BULLISH'}
        )
        if st1 == SIGNAL_WAITING_CONFIRM:
            update_forex_signal(s1['signal_id'], SIGNAL_EXECUTED, "DRYRUN_AUTO_EXECUTED")
    s1 = get_forex_signal(s1['signal_id'])
    print_result("1.Valid->Executed", s1)

    # 2) XAGUSD -> BLOCKED
    s2 = create_forex_signal(
        setup=mk_setup('XAGUSD', 'BULLISH', 25, 24, 26, 27, 28),
        current_ltp=25,
        config=cfg,
        spread_pct=0.0002,
    )
    print_result("2.XAGBlocked", s2)

    # 3) Outside session -> BLOCKED
    signal3 = {
        'signal_id': 'DRY3',
        'symbol': 'XAUUSD',
        'direction': 'BULLISH',
        'planned_entry': 150.0,
        'current_ltp': 150.0,
        'stop_loss': 140.0,
        'target': 170.0,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'spread_pct': 0.0002,
    }
    st3, rs3, sig3 = revalidate_forex_signal(
        signal3,
        150.0,
        CFG,
        spread_pct=0.0002,
        ref_time=datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),
    )
    sig3['state'] = st3
    sig3['status_reason'] = rs3
    print_result("3.OutsideSession", sig3)

    # 4) Spread too wide -> BLOCKED
    signal4 = dict(signal3)
    signal4['signal_id'] = 'DRY4'
    st4, rs4, sig4 = revalidate_forex_signal(
        signal4,
        150.0,
        cfg,
        spread_pct=0.01,
    )
    sig4['state'] = st4
    sig4['status_reason'] = rs4
    print_result("4.SpreadWide", sig4)

    # 5) RR damaged -> REJECTED
    signal5 = dict(signal3)
    signal5.update({'signal_id': 'DRY5', 'planned_entry': 100.0, 'stop_loss': 99.0, 'target': 101.0})
    st5, rs5, sig5 = revalidate_forex_signal(signal5, 100.0, cfg, spread_pct=0.0001)
    sig5['state'] = st5
    sig5['status_reason'] = rs5
    print_result("5.RRDamaged", sig5)

    # 6) SL/TP wrong -> REJECTED
    signal6 = dict(signal3)
    signal6.update({'signal_id': 'DRY6', 'stop_loss': 160.0, 'target': 170.0})
    st6, rs6, sig6 = revalidate_forex_signal(signal6, 150.0, cfg, spread_pct=0.0001)
    sig6['state'] = st6
    sig6['status_reason'] = rs6
    print_result("6.BadSLTP", sig6)

    # 7) LEGACY pass-through (simulated check)
    legacy_mode = os.getenv('FOREX_EXECUTION_MODE', 'LEGACY').strip().upper()
    print(f"7.LegacyMode: FOREX_EXECUTION_MODE={legacy_mode} (LEGACY means zero-latency pass-through)")

    # 8) Proxy unavailable -> no crash, fallback, still valid if other checks pass
    signal8 = dict(signal3)
    signal8['signal_id'] = 'DRY8'
    st8, rs8, sig8 = revalidate_forex_signal(
        signal8,
        150.0,
        cfg,
        spread_pct=0.0001,
        proxy_snapshot={'available': False, 'reason': 'PROXY_DATA_UNAVAILABLE'},
    )
    sig8['state'] = st8
    sig8['status_reason'] = rs8
    print_result("8.ProxyUnavailableFallback", sig8)

    # Bonus: signal too old
    signal9 = dict(signal3)
    signal9['signal_id'] = 'DRY9'
    signal9['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat()
    st9, rs9, sig9 = revalidate_forex_signal(signal9, 150.0, cfg, spread_pct=0.0001)
    sig9['state'] = st9
    sig9['status_reason'] = rs9
    print_result("9.Expired", sig9)

    print("\nExpected summary:")
    print(f"1 => {SIGNAL_EXECUTED}")
    print(f"2 => {SIGNAL_BLOCKED}")
    print(f"3 => {SIGNAL_BLOCKED}")
    print(f"4 => {SIGNAL_BLOCKED}")
    print(f"5 => {SIGNAL_REJECTED}")
    print(f"6 => {SIGNAL_REJECTED}")
    print("7 => LEGACY check only (no order simulation)")
    print(f"8 => {SIGNAL_WAITING_CONFIRM} + proxy_note=PROXY_DATA_UNAVAILABLE")
    print(f"9 => {SIGNAL_EXPIRED}")
    print(f"\nAudit file: {FOREX_AUDIT_LOG_FILE}")


if __name__ == '__main__':
    run()
