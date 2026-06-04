# ml/shadow_monitor.py
#
# Tracks ML prediction accuracy vs actual trade outcomes.
# Sends Telegram alerts when models cross accuracy thresholds.
# Provides /ml_status command data.
#
# SHADOW MODE ONLY — read/write to data files, never touches orders.

from __future__ import annotations
import os, json
from datetime import datetime, timezone
from typing import Optional

from utils.logger import logger

_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ML_DIR = os.path.join(_ROOT, 'data', 'ml')

# Thresholds to trigger Telegram alerts
_ALERT_ACCURACY_THRESHOLD  = 58.0  # % accuracy to send "ML is learning well" alert
_ALERT_MIN_SAMPLES         = 20    # minimum predictions with outcomes before alerting
_HIGH_CONF_GOOD_THRESHOLD  = 65.0  # % accuracy on HIGH/MEDIUM confidence calls


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ── Core accuracy computation ──────────────────────────────────────────────────

def compute_accuracy(market: str, account: str = '') -> dict:
    """
    Scan predictions.jsonl and compute accuracy metrics.
    Returns rich dict for /ml_status display.
    """
    try:
        tag  = f"{account}_" if account else ""
        path = os.path.join(_ML_DIR, market, f"{tag}predictions.jsonl")
        if not os.path.exists(path):
            return _empty_stats(market, account)

        total = correct = 0
        conf_buckets: dict[str, dict] = {
            'HIGH': {'total': 0, 'correct': 0},
            'MEDIUM': {'total': 0, 'correct': 0},
            'LOW': {'total': 0, 'correct': 0},
            'AVOID': {'total': 0, 'correct': 0},
        }
        model_stats: dict[str, dict] = {}
        pending = 0

        r_errors = []   # |r_hat - r_actual| for closed trades

        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('_type') != 'PREDICTION':
                        continue
                    outcome = rec.get('outcome')
                    if not outcome:
                        pending += 1
                        continue

                    predicted_win = rec['win_prob'] >= 0.5
                    actual_win    = outcome['result'] == 'WIN'
                    conf          = rec.get('confidence', 'LOW')
                    total  += 1
                    match   = predicted_win == actual_win
                    if match:
                        correct += 1
                    if conf in conf_buckets:
                        conf_buckets[conf]['total']   += 1
                        if match:
                            conf_buckets[conf]['correct'] += 1

                    # Per-model stats
                    for m, md in rec.get('model_detail', {}).items():
                        if m not in model_stats:
                            model_stats[m] = {'total': 0, 'correct': 0}
                        model_stats[m]['total'] += 1
                        m_pred_win = md['win_prob'] >= 0.5
                        if m_pred_win == actual_win:
                            model_stats[m]['correct'] += 1

                    # R-multiple error
                    r_actual = outcome.get('r_actual', 0)
                    r_hat    = rec.get('r_hat', 0)
                    r_errors.append(abs(r_hat - r_actual))

                except Exception:
                    pass

        acc = round(correct / total * 100, 1) if total else None
        mae_r = round(float(sum(r_errors) / len(r_errors)), 3) if r_errors else None

        model_acc = {}
        for m, s in model_stats.items():
            model_acc[m] = round(s['correct'] / s['total'] * 100, 1) if s['total'] else None

        conf_acc = {}
        for c, s in conf_buckets.items():
            conf_acc[c] = round(s['correct'] / s['total'] * 100, 1) if s['total'] else None

        return {
            'market'       : market,
            'account'      : account,
            'total_closed' : total,
            'pending'      : pending,
            'accuracy'     : acc,
            'correct'      : correct,
            'conf_accuracy': conf_acc,
            'model_accuracy': model_acc,
            'r_mae'        : mae_r,
            'computed_at'  : _now_utc(),
        }

    except Exception as e:
        logger.error(f"Shadow monitor compute error: {e}")
        return _empty_stats(market, account)


def _empty_stats(market: str, account: str) -> dict:
    return {
        'market': market, 'account': account,
        'total_closed': 0, 'pending': 0,
        'accuracy': None, 'correct': 0,
        'conf_accuracy': {}, 'model_accuracy': {}, 'r_mae': None,
        'computed_at': _now_utc(),
    }


# ── Threshold alerts ───────────────────────────────────────────────────────────

def check_and_alert(market: str, account: str = '') -> None:
    """
    Check accuracy thresholds; if crossed, fire a Telegram alert.
    Called after each new outcome is recorded.
    """
    stats = compute_accuracy(market, account)
    total = stats['total_closed']
    acc   = stats['accuracy']

    if total < _ALERT_MIN_SAMPLES or acc is None:
        return

    high_acc  = stats['conf_accuracy'].get('HIGH')
    med_acc   = stats['conf_accuracy'].get('MEDIUM')
    hm_total  = (stats['conf_accuracy'].get('HIGH') is not None or
                 stats['conf_accuracy'].get('MEDIUM') is not None)

    messages = []

    if acc >= _ALERT_ACCURACY_THRESHOLD:
        messages.append(
            f"🧠 *ML Accuracy Alert* — {market.upper()}/{account or 'all'}\n"
            f"Overall accuracy: *{acc}%* ({total} trades)\n"
            f"ML is learning well ✅"
        )

    if high_acc and high_acc >= _HIGH_CONF_GOOD_THRESHOLD:
        messages.append(
            f"⭐ *ML HIGH confidence* calls: *{high_acc}%* accurate\n"
            f"Consider monitoring these setups closely."
        )

    for msg in messages:
        _send_telegram(msg)


def _send_telegram(msg: str) -> None:
    try:
        import os
        from communications.telegram_helpers import send_message
        token = os.getenv('TELEGRAM_BOT_TOKEN_FTMO', '').strip()
        chat_id = os.getenv('CB6_ADMIN_USER_ID', '').strip()
        if token and chat_id:
            send_message(token, chat_id, msg, parse_mode='HTML')
    except Exception as e:
        logger.warning(f"Shadow monitor Telegram send failed: {e}")


# ── On-outcome hook ────────────────────────────────────────────────────────────

def on_trade_closed(trade_id: str, market: str, account: str,
                    actual_result: str, actual_r: float) -> None:
    """
    Called when a trade closes. Patches prediction log, then checks thresholds.
    """
    try:
        from ml.predictor import record_actual_outcome
        record_actual_outcome(trade_id, market, account, actual_result, actual_r)
        check_and_alert(market, account)
    except Exception as e:
        logger.error(f"Shadow monitor on_trade_closed error: {e}")


# ── Status summary (for /ml_status Telegram command) ──────────────────────────

def build_status_message() -> str:
    """
    Build multi-line /ml_status message showing all markets/accounts.
    """
    lines = ["🤖 *CB6 Quantum — ML Shadow Status*", ""]

    pairs = [
        ('nse',   ''),
        ('forex', 'ftmo'),
        ('forex', 'gft'),
    ]

    for market, acc in pairs:
        stats  = compute_accuracy(market, acc)
        label  = f"{market.upper()}/{acc.upper() if acc else 'ALL'}"
        total  = stats['total_closed']
        pend   = stats['pending']
        acc_pct = f"{stats['accuracy']}%" if stats['accuracy'] is not None else "—"
        r_mae  = f"{stats['r_mae']}" if stats['r_mae'] is not None else "—"

        lines.append(f"📊 *{label}*")
        lines.append(f"  Closed: {total} | Pending: {pend} | Accuracy: *{acc_pct}*")
        lines.append(f"  R-MAE: {r_mae}")

        # Confidence breakdown
        ca = stats.get('conf_accuracy', {})
        if any(v is not None for v in ca.values()):
            conf_str = "  Conf: " + "  ".join(
                f"{k}={v}%" for k, v in ca.items() if v is not None
            )
            lines.append(conf_str)

        # Model breakdown
        ma = stats.get('model_accuracy', {})
        if ma:
            mod_str = "  Models: " + "  ".join(
                f"{k}={v}%" for k, v in ma.items() if v is not None
            )
            lines.append(mod_str)

        # Dataset info
        try:
            from ml.data_pipeline import get_dataset_stats
            ds = get_dataset_stats(market, acc)
            if ds['total'] > 0:
                lines.append(
                    f"  Dataset: {ds['total']} trades | WR {ds['win_rate']}% | AvgR {ds['avg_r']}"
                )
        except Exception:
            pass

        lines.append("")

    # Model file status
    lines.append("📁 *Model files*")
    from ml.trainer import dnn_trainer, cnn_trainer, rnn_trainer
    models_dir = os.path.join(_ROOT, 'ml', 'models')
    for mkt in ('nse', 'forex'):
        for acct in ([''] if mkt == 'nse' else ['ftmo', 'gft']):
            tag  = f"{acct}_" if acct else ""
            mdir = os.path.join(models_dir, mkt)
            for kind in ('dnn', 'cnn', 'rnn'):
                pt = os.path.join(mdir, f"{tag}{kind}_latest.pt")
                icon = "✅" if os.path.exists(pt) else "⬜"
                lines.append(f"  {icon} {mkt}/{acct or 'all'} {kind.upper()}")

    lines.append("")
    lines.append(f"_Updated: {_now_utc()}_")
    return '\n'.join(lines)
