# ml/auto_trainer.py
#
# Scheduled auto-retraining for CB6 Quantum ML models.
# Triggers:
#   1. Every N new completed trades  (default: 20)
#   2. Weekly (Sunday 02:00 UTC)
#   3. Manual via trigger_now(market, account)
#
# Runs training in a background thread so the main trading loop is not blocked.
# SHADOW MODE ONLY — training reads data, never touches orders.

from __future__ import annotations
import os, json, threading, time
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils.logger import logger

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ML_DIR    = os.path.join(_ROOT, 'data', 'ml')
_STATE_DIR = os.path.join(_ROOT, 'data', 'ml', '_trainer_state')

# How many new completed trades before auto-retrain
RETRAIN_EVERY_N = 20

# Retrain all models every N days regardless of trade count
RETRAIN_DAYS = 7


# ── State persistence ──────────────────────────────────────────────────────────

def _state_path(market: str, account: str) -> str:
    os.makedirs(_STATE_DIR, exist_ok=True)
    tag = f"{account}_" if account else ""
    return os.path.join(_STATE_DIR, f"{market}_{tag}state.json")


def _load_state(market: str, account: str) -> dict:
    path = _state_path(market, account)
    if not os.path.exists(path):
        return {
            'last_trained_at'  : None,
            'trades_at_last_train': 0,
            'total_trains'     : 0,
        }
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {'last_trained_at': None, 'trades_at_last_train': 0, 'total_trains': 0}


def _save_state(market: str, account: str, state: dict) -> None:
    try:
        with open(_state_path(market, account), 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"AutoTrainer state save error: {e}")


# ── Count completed trades ────────────────────────────────────────────────────

def _count_completed(market: str, account: str) -> int:
    """Count OUTCOME records in JSONL — proxy for completed trades."""
    try:
        from ml.data_pipeline import join_trades
        df = join_trades(market, account)
        return len(df)
    except Exception:
        return 0


# ── Single retrain job ─────────────────────────────────────────────────────────

def _run_training(market: str, account: str) -> None:
    """Train all three models for a given market/account. Runs in background thread."""
    logger.info(f"AutoTrainer [{market}/{account or 'all'}]: starting training run")

    results = {}
    try:
        from ml.trainer import dnn_trainer
        meta = dnn_trainer.train(market, account)
        results['dnn'] = meta
    except Exception as e:
        logger.error(f"AutoTrainer DNN [{market}] error: {e}")

    try:
        from ml.trainer import cnn_trainer
        meta = cnn_trainer.train(market, account)
        results['cnn'] = meta
    except Exception as e:
        logger.error(f"AutoTrainer CNN [{market}] error: {e}")

    try:
        from ml.trainer import rnn_trainer
        meta = rnn_trainer.train(market, account)
        results['rnn'] = meta
    except Exception as e:
        logger.error(f"AutoTrainer RNN [{market}] error: {e}")

    # Update state
    state = _load_state(market, account)
    state['last_trained_at']       = datetime.now(timezone.utc).isoformat(timespec='seconds')
    state['trades_at_last_train']  = _count_completed(market, account)
    state['total_trains']          = state.get('total_trains', 0) + 1
    state['last_results']          = {
        k: {'acc': v.get('accuracy'), 'val_loss': v.get('val_loss'), 'n': v.get('n_samples')}
        for k, v in results.items() if v
    }
    _save_state(market, account, state)

    # Telegram summary
    trained = [k.upper() for k, v in results.items() if v]
    skipped = [k.upper() for k, v in results.items() if not v]
    msg_parts = [
        f"🧠 *ML Auto-Train complete* — {market.upper()}/{account or 'all'}",
        f"Trained: {', '.join(trained) if trained else 'none'}",
    ]
    if skipped:
        msg_parts.append(f"Skipped (not enough data): {', '.join(skipped)}")
    for kind, meta in results.items():
        if meta:
            msg_parts.append(
                f"  {kind.upper()}: N={meta.get('n_samples',0)} "
                f"acc={meta.get('accuracy',0):.1%} "
                f"val_loss={meta.get('val_loss',0):.4f}"
            )
    _send_telegram('\n'.join(msg_parts))
    logger.info(f"AutoTrainer [{market}/{account or 'all'}]: done. trained={trained}")


def _run_training_bg(market: str, account: str) -> None:
    t = threading.Thread(target=_run_training, args=(market, account),
                         daemon=True, name=f"MLTrain-{market}-{account or 'all'}")
    t.start()


def _send_telegram(msg: str) -> None:
    try:
        import os
        from communications.telegram_helpers import send_message
        token = os.getenv('TELEGRAM_BOT_TOKEN_FTMO', '').strip()
        chat_id = os.getenv('CB6_ADMIN_USER_ID', '').strip()
        if token and chat_id:
            send_message(token, chat_id, msg, parse_mode='HTML')
    except Exception as e:
        logger.warning(f"AutoTrainer Telegram send failed: {e}")


# ── Check & trigger ────────────────────────────────────────────────────────────

def check_and_train(market: str, account: str = '') -> bool:
    """
    Call after each new trade outcome is recorded.
    Returns True if training was triggered.
    """
    state    = _load_state(market, account)
    n_now    = _count_completed(market, account)
    n_before = state.get('trades_at_last_train', 0)
    last_ts  = state.get('last_trained_at')

    # Check if enough new trades accumulated
    if n_now - n_before >= RETRAIN_EVERY_N:
        logger.info(
            f"AutoTrainer [{market}/{account or 'all'}]: "
            f"{n_now - n_before} new completed trades → trigger"
        )
        _run_training_bg(market, account)
        return True

    # Check if weekly schedule due
    if last_ts:
        last_dt = datetime.fromisoformat(last_ts)
        if datetime.now(timezone.utc) - last_dt >= timedelta(days=RETRAIN_DAYS):
            logger.info(
                f"AutoTrainer [{market}/{account or 'all'}]: "
                f"weekly schedule triggered"
            )
            _run_training_bg(market, account)
            return True
    else:
        # Never trained — train if any data at all
        if n_now > 0:
            logger.info(
                f"AutoTrainer [{market}/{account or 'all'}]: "
                f"first training run ({n_now} trades available)"
            )
            _run_training_bg(market, account)
            return True

    return False


def trigger_now(market: str, account: str = '') -> None:
    """
    Manual trigger — call from Telegram /ml_train command or admin script.
    """
    logger.info(f"AutoTrainer [{market}/{account or 'all'}]: manual trigger")
    _run_training_bg(market, account)


# ── Background scheduler loop ─────────────────────────────────────────────────

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def start_scheduler() -> None:
    """
    Start a background thread that checks weekly retrain schedule every hour.
    Safe to call multiple times — only one thread started.
    """
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="MLAutoScheduler"
    )
    _scheduler_thread.start()
    logger.info("AutoTrainer scheduler started")


def stop_scheduler() -> None:
    _stop_event.set()


def _scheduler_loop() -> None:
    """Check every hour whether weekly retrain is due for any market/account."""
    pairs = [
        ('nse',   ''),
        ('forex', 'ftmo'),
        ('forex', 'gft'),
    ]
    while not _stop_event.is_set():
        for market, account in pairs:
            try:
                state   = _load_state(market, account)
                last_ts = state.get('last_trained_at')
                if last_ts:
                    last_dt = datetime.fromisoformat(last_ts)
                    if datetime.now(timezone.utc) - last_dt >= timedelta(days=RETRAIN_DAYS):
                        logger.info(
                            f"AutoTrainer scheduler: weekly retrain "
                            f"[{market}/{account or 'all'}]"
                        )
                        _run_training_bg(market, account)
            except Exception as e:
                logger.error(f"AutoTrainer scheduler error [{market}]: {e}")

        # Sleep 1 hour between checks
        _stop_event.wait(timeout=3600)


# ── Training state summary (for /ml_status) ───────────────────────────────────

def get_trainer_status() -> list[dict]:
    pairs = [('nse', ''), ('forex', 'ftmo'), ('forex', 'gft')]
    out = []
    for market, account in pairs:
        s = _load_state(market, account)
        n = _count_completed(market, account)
        s['market']          = market
        s['account']         = account
        s['trades_now']      = n
        s['trades_since_train'] = n - s.get('trades_at_last_train', 0)
        s['next_train_in']   = max(0, RETRAIN_EVERY_N - s['trades_since_train'])
        out.append(s)
    return out
