import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.execution_validation import (
    STATE_FILE,
    AUDIT_LOG_FILE,
    SIGNAL_WAITING_CONFIRM,
    SIGNAL_REJECTED,
    SIGNAL_EXECUTED,
    SIGNAL_EXPIRED,
    SIGNAL_INVALIDATED,
    SIGNAL_MISSED,
    create_signal,
    get_signal,
    update_signal,
    revalidate_existing,
    cancel_signal,
)
from utils.state_io import state_lock


CFG = {
    'max_entry_drift_percent': 2.0,
    'max_entry_drift_points': 3.0,
    'minimum_required_rr': 1.5,
    'invalidation_buffer_points': 10.0,
    'allowed_signal_age_seconds': 180,
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
        f"rr={signal.get('calculated_rr')} id={signal.get('signal_id')}"
    )


def run():
    # Reset signal store for deterministic dry-run results.
    with state_lock(STATE_FILE, default={'signals': {}}) as st:
        st['signals'] = {}

    print("Execution Validation Dry Run")
    print("=" * 40)

    # 1) Valid entry within band -> WAITING_FOR_MANUAL_CONFIRMATION
    s1 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=149.5,
        config=CFG,
    )
    print_result("1.ValidEntry", s1)

    # 2) LTP far below long entry -> MISSED/WAITING_FOR_RECLAIM
    s2 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=135,
        config=CFG,
    )
    print_result("2.FarBelowEntry", s2)

    # 3) RR damaged -> REJECTED
    s3 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 151, 152, 153),
        current_ltp=150,
        config=CFG,
    )
    print_result("3.RRDamaged", s3)

    # 4) SL/TP direction wrong -> REJECTED
    s4 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 160, 170, 175, 180),
        current_ltp=150,
        config=CFG,
    )
    print_result("4.BadSLTPDirection", s4)

    # 5) Signal too old -> EXPIRED
    s5 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=150,
        config=CFG,
    )
    old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=600)).isoformat()
    with state_lock(STATE_FILE, default={'signals': {}}) as st:
        st['signals'][s5['signal_id']]['created_at'] = old
    s5_ref = get_signal(s5['signal_id'])
    status5, reason5, _ = revalidate_existing(s5_ref, current_ltp=150, config=CFG)
    update_signal(s5['signal_id'], status5, f"DRYRUN_REVALIDATE:{reason5}", current_ltp=150)
    s5 = get_signal(s5['signal_id'])
    print_result("5.SignalTooOld", s5)

    # 6) Price near SL before entry -> INVALIDATED
    cfg_wide_band = dict(CFG)
    cfg_wide_band['max_entry_drift_points'] = 25.0
    s6 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 120, 180, 190, 200),
        current_ltp=129,  # within drift band and <= SL+buffer(130)
        config=cfg_wide_band,
    )
    print_result("6.NearSLInvalidation", s6)

    # 7) Manual approve -> revalidate then EXECUTED (simulated, no order call)
    s7 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=150,
        config=CFG,
    )
    if s7.get('state') == SIGNAL_WAITING_CONFIRM:
        cur7 = get_signal(s7['signal_id'])
        st7, rs7, _ = revalidate_existing(cur7, current_ltp=150, config=CFG)
        if st7 == SIGNAL_WAITING_CONFIRM:
            update_signal(s7['signal_id'], 'APPROVED', "DRYRUN_MANUAL_APPROVED", current_ltp=150)
            update_signal(s7['signal_id'], SIGNAL_EXECUTED, "DRYRUN_EXECUTED_NO_ORDER")
    s7 = get_signal(s7['signal_id'])
    print_result("7.ManualApproveFlow", s7)

    # 8) Manual reject/cancel -> REJECTED
    s8 = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=150,
        config=CFG,
    )
    if s8.get('state') == SIGNAL_WAITING_CONFIRM:
        update_signal(s8['signal_id'], SIGNAL_REJECTED, "DRYRUN_MANUAL_REJECT")
    s8r = create_signal(
        setup=mk_setup("NSE:NIFTY", "BULLISH", 150, 140, 160, 170, 180),
        current_ltp=150,
        config=CFG,
    )
    if s8r.get('state') == SIGNAL_WAITING_CONFIRM:
        cancel_signal(s8r['signal_id'], reason="USER_CANCELLED")
    s8 = get_signal(s8['signal_id'])
    s8r = get_signal(s8r['signal_id'])
    print_result("8.ManualReject", s8)
    print_result("8.ManualCancel", s8r)

    print("\nExpected-state summary:")
    print(f"1 should be {SIGNAL_WAITING_CONFIRM}: {s1.get('state')}")
    print(f"2 should be {SIGNAL_MISSED}: {s2.get('state')}")
    print(f"3 should be {SIGNAL_REJECTED}: {s3.get('state')}")
    print(f"4 should be {SIGNAL_REJECTED}: {s4.get('state')}")
    print(f"5 should be {SIGNAL_EXPIRED}: {s5.get('state')}")
    print(f"6 should be {SIGNAL_INVALIDATED}: {s6.get('state')}")
    print(f"7 should be {SIGNAL_EXECUTED}: {s7.get('state')}")
    print(f"8 reject should be {SIGNAL_REJECTED}: {s8.get('state')}")
    print(f"8 cancel should be {SIGNAL_REJECTED}: {s8r.get('state')}")

    print("\nAudit tail:")
    if os.path.exists(AUDIT_LOG_FILE):
        with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
            lines = [ln.rstrip('\n') for ln in f.readlines() if ln.strip()]
        for ln in lines[-15:]:
            print(ln)
    else:
        print("No audit file found.")


if __name__ == '__main__':
    run()
