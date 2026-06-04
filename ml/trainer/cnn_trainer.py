# ml/trainer/cnn_trainer.py
#
# 1-D Convolutional Neural Network — reads price candle sequences.
# Treats each 15-min candle as a 5-channel signal (O/H/L/C/V).
# Learns WHICH price patterns before entry predict winners.
#
# Architecture: Input(5, N) → Conv1d blocks → GAP → FC → dual heads
# Requires price series saved by data_pipeline.save_price_series().
#
# SHADOW MODE ONLY — never touches orders.

from __future__ import annotations
import os, json
from datetime import datetime
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split
import joblib

from utils.logger import logger

_ROOT      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_DIR = os.path.join(_ROOT, 'ml', 'models')
MIN_SERIES = 30    # need at least this many price series saved
SEQ_LEN    = 50    # candles used (padded/truncated)


# ── Model ──────────────────────────────────────────────────────────────────────

class CB6CNN(nn.Module):
    """
    1-D CNN over OHLCV candle sequences.
    Input shape: (batch, 5, SEQ_LEN)
    """
    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(5, 32, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=3, padding=1), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(64, 128, kernel_size=3, padding=1), nn.ReLU(),
            nn.Conv1d(128, 128, kernel_size=3, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),        # Global Average Pooling → (batch, 128, 1)
        )
        self.win_head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128, 64), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(64, 1), nn.Sigmoid()
        )
        self.r_head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128, 32), nn.ReLU(),
            nn.Linear(32, 1)
        )

    def forward(self, x):
        h = self.encoder(x)
        return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)


def _normalise_series(arr: np.ndarray) -> np.ndarray:
    """
    Normalize each OHLC channel by its first value (% change from candle 0).
    Volume normalized by its mean.
    Shape in/out: (SEQ_LEN, 5) → same.
    """
    out = arr.copy().astype(np.float32)
    for i in range(4):   # OHLC
        base = out[0, i] if out[0, i] != 0 else 1.0
        out[:, i] = (out[:, i] - base) / base
    vmean = out[:, 4].mean()
    if vmean > 0:
        out[:, 4] /= vmean
    return out


def _load_dataset(market: str, account: str = '') -> Optional[tuple]:
    """Load all price series + outcomes. Returns (X_tensor, y_cls, y_r) or None."""
    from ml.data_pipeline import join_trades, load_price_series

    df = join_trades(market, account)
    if df.empty:
        return None

    Xs, ycs, yrs = [], [], []
    for _, row in df.iterrows():
        tid  = row.get('trade_id', '')
        arr  = load_price_series(tid, market, account)
        if arr is None or len(arr) < 5:
            continue
        # Pad or truncate to SEQ_LEN
        if len(arr) < SEQ_LEN:
            pad = np.zeros((SEQ_LEN - len(arr), 5), dtype=np.float32)
            arr = np.vstack([pad, arr])
        else:
            arr = arr[-SEQ_LEN:]
        arr = _normalise_series(arr)
        Xs.append(arr.T)   # → (5, SEQ_LEN) = (channels, time)
        ycs.append(1.0 if str(row.get('result','')).upper() == 'WIN' else 0.0)
        r = float(row.get('r_multiple', 0) or 0)
        yrs.append(r)

    if len(Xs) < MIN_SERIES:
        return None

    X   = torch.tensor(np.array(Xs, dtype=np.float32))
    yc  = torch.tensor(np.array(ycs, dtype=np.float32))
    yr  = torch.tensor(np.array(yrs, dtype=np.float32))
    return X, yc, yr


# ── Train ──────────────────────────────────────────────────────────────────────

def train(market: str, account: str = '',
          epochs: int = 100, lr: float = 1e-3) -> Optional[dict]:
    data = _load_dataset(market, account)
    if data is None:
        logger.warning(f"CNN [{market}/{account}]: insufficient price series — skip")
        return None

    X, yc, yr = data
    dataset   = TensorDataset(X, yc, yr)
    val_sz    = max(1, int(len(dataset) * 0.2))
    tr_sz     = len(dataset) - val_sz
    tr_ds, va_ds = random_split(dataset, [tr_sz, val_sz],
                                generator=torch.Generator().manual_seed(42))
    tr_dl = DataLoader(tr_ds, batch_size=16, shuffle=True)
    va_dl = DataLoader(va_ds, batch_size=16)

    model = CB6CNN()
    opt   = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    sch   = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    cls_l = nn.BCELoss()
    reg_l = nn.HuberLoss()

    best_val, best_state = float('inf'), None
    for epoch in range(epochs):
        model.train()
        for xb, yb, rb in tr_dl:
            opt.zero_grad()
            wp, rp = model(xb)
            loss   = cls_l(wp, yb) + 0.5 * reg_l(rp, rb)
            loss.backward(); opt.step()
        sch.step()

        model.eval()
        vl = []
        with torch.no_grad():
            for xb, yb, rb in va_dl:
                wp, rp = model(xb)
                vl.append((cls_l(wp, yb) + 0.5 * reg_l(rp, rb)).item())
        mean_vl = float(np.mean(vl))
        if mean_vl < best_val:
            best_val   = mean_vl
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        wp_all, _ = model(X)
    preds = (wp_all.numpy() >= 0.5).astype(int)
    acc   = float((preds == yc.numpy().astype(int)).mean())

    tag   = f"{account}_" if account else ""
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    mdir  = os.path.join(_MODEL_DIR, market)
    os.makedirs(mdir, exist_ok=True)
    torch.save(best_state, os.path.join(mdir, f"{tag}cnn_latest.pt"))

    meta = {'trained_at': stamp, 'market': market, 'account': account,
            'n_samples': len(X), 'val_loss': round(best_val, 6),
            'accuracy': round(acc, 4)}
    with open(os.path.join(mdir, f"{tag}cnn_meta_latest.json"), 'w') as f:
        json.dump(meta, f, indent=2)

    logger.info(f"CNN [{market}/{account}] trained — N={len(X)} acc={acc:.1%}")
    return meta


# ── Predict ────────────────────────────────────────────────────────────────────

def predict(candles: np.ndarray, market: str, account: str = '') -> Optional[dict]:
    """
    Predict from raw candle array (N, 5).
    SHADOW MODE — never touches orders.
    """
    try:
        tag  = f"{account}_" if account else ""
        mdir = os.path.join(_MODEL_DIR, market)
        pt   = os.path.join(mdir, f"{tag}cnn_latest.pt")
        if not os.path.exists(pt):
            return None

        arr = candles[-SEQ_LEN:] if len(candles) >= SEQ_LEN else candles
        if len(arr) < SEQ_LEN:
            pad = np.zeros((SEQ_LEN - len(arr), 5), dtype=np.float32)
            arr = np.vstack([pad, arr])
        arr  = _normalise_series(arr)
        x    = torch.tensor(arr.T).unsqueeze(0)   # (1, 5, SEQ_LEN)

        state = torch.load(pt, map_location='cpu', weights_only=True)
        model = CB6CNN()
        model.load_state_dict(state)
        model.eval()

        with torch.no_grad():
            wp, rp = model(x)
        win_prob = float(wp.item())
        r_hat    = float(rp.item())
        conf = 'HIGH' if win_prob >= 0.70 else ('MEDIUM' if win_prob >= 0.55
               else ('AVOID' if win_prob <= 0.35 else 'LOW'))
        return {'win_prob': round(win_prob, 4), 'r_hat': round(r_hat, 3),
                'confidence': conf, 'model': 'CNN'}
    except Exception as e:
        logger.error(f"CNN predict error: {e}")
        return None
