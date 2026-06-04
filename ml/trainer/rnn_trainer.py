# ml/trainer/rnn_trainer.py
#
# LSTM (RNN) — learns sequential candle patterns leading into a trade.
# Processes the same price series as CNN but as a sequence, not an image.
# LSTM can capture temporal dependencies: "price was falling for 3 candles
# then swept a low then bounced" as a learnable sequence.
#
# Architecture: Input(SEQ_LEN, 5) → LSTM(128, 2 layers) → FC → dual heads
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

from utils.logger import logger

_ROOT      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_DIR = os.path.join(_ROOT, 'ml', 'models')
MIN_SERIES = 30
SEQ_LEN    = 50


class CB6LSTM(nn.Module):
    """
    Stacked LSTM over OHLCV candle sequences.
    Input: (batch, SEQ_LEN, 5)
    """
    def __init__(self, input_size=5, hidden=128, n_layers=2, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden, n_layers,
                            batch_first=True, dropout=dropout)
        self.win_head = nn.Sequential(
            nn.Linear(hidden, 64), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(64, 1), nn.Sigmoid()
        )
        self.r_head = nn.Sequential(
            nn.Linear(hidden, 32), nn.ReLU(),
            nn.Linear(32, 1)
        )

    def forward(self, x):
        # x: (batch, seq, features)
        out, (h, _) = self.lstm(x)
        last = out[:, -1, :]    # take last timestep output
        return self.win_head(last).squeeze(-1), self.r_head(last).squeeze(-1)


def _normalise(arr: np.ndarray) -> np.ndarray:
    """Normalise OHLCV: OHLC as % from first close, V by mean."""
    out = arr.copy().astype(np.float32)
    base = out[0, 3] if out[0, 3] != 0 else 1.0   # first close as reference
    for i in range(4):
        out[:, i] = (out[:, i] - base) / base
    vm = out[:, 4].mean()
    if vm > 0:
        out[:, 4] /= vm
    return out


def _load_dataset(market: str, account: str = '') -> Optional[tuple]:
    from ml.data_pipeline import join_trades, load_price_series
    df = join_trades(market, account)
    if df.empty:
        return None
    Xs, ycs, yrs = [], [], []
    for _, row in df.iterrows():
        arr = load_price_series(row.get('trade_id',''), market, account)
        if arr is None or len(arr) < 5:
            continue
        if len(arr) < SEQ_LEN:
            pad = np.zeros((SEQ_LEN - len(arr), 5), dtype=np.float32)
            arr = np.vstack([pad, arr])
        else:
            arr = arr[-SEQ_LEN:]
        Xs.append(_normalise(arr))                  # (SEQ_LEN, 5)
        ycs.append(1.0 if str(row.get('result','')).upper() == 'WIN' else 0.0)
        yrs.append(float(row.get('r_multiple', 0) or 0))
    if len(Xs) < MIN_SERIES:
        return None
    X  = torch.tensor(np.array(Xs, dtype=np.float32))   # (N, SEQ_LEN, 5)
    yc = torch.tensor(np.array(ycs, dtype=np.float32))
    yr = torch.tensor(np.array(yrs, dtype=np.float32))
    return X, yc, yr


def train(market: str, account: str = '',
          epochs: int = 100, lr: float = 5e-4) -> Optional[dict]:
    data = _load_dataset(market, account)
    if data is None:
        logger.warning(f"RNN [{market}/{account}]: insufficient price series — skip")
        return None

    X, yc, yr = data
    dataset    = TensorDataset(X, yc, yr)
    val_sz     = max(1, int(len(dataset) * 0.2))
    tr_sz      = len(dataset) - val_sz
    tr_ds, va_ds = random_split(dataset, [tr_sz, val_sz],
                                generator=torch.Generator().manual_seed(42))
    tr_dl = DataLoader(tr_ds, batch_size=16, shuffle=True)
    va_dl = DataLoader(va_ds, batch_size=16)

    model = CB6LSTM()
    opt   = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sch   = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=lr*5, steps_per_epoch=len(tr_dl), epochs=epochs)
    cls_l = nn.BCELoss()
    reg_l = nn.HuberLoss()

    best_val, best_state = float('inf'), None
    for epoch in range(epochs):
        model.train()
        for xb, yb, rb in tr_dl:
            opt.zero_grad()
            wp, rp = model(xb)
            loss   = cls_l(wp, yb) + 0.5 * reg_l(rp, rb)
            loss.backward(); opt.step(); sch.step()

        model.eval()
        vl = []
        with torch.no_grad():
            for xb, yb, rb in va_dl:
                wp, rp = model(xb)
                vl.append((cls_l(wp, yb) + 0.5 * reg_l(rp, rb)).item())
        mv = float(np.mean(vl))
        if mv < best_val:
            best_val   = mv
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        wp_all, _ = model(X)
    acc = float(((wp_all.numpy() >= 0.5).astype(int) == yc.numpy().astype(int)).mean())

    tag   = f"{account}_" if account else ""
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    mdir  = os.path.join(_MODEL_DIR, market)
    os.makedirs(mdir, exist_ok=True)
    torch.save(best_state, os.path.join(mdir, f"{tag}rnn_latest.pt"))

    meta = {'trained_at': stamp, 'market': market, 'account': account,
            'n_samples': len(X), 'val_loss': round(best_val, 6),
            'accuracy': round(acc, 4)}
    with open(os.path.join(mdir, f"{tag}rnn_meta_latest.json"), 'w') as f:
        json.dump(meta, f, indent=2)

    logger.info(f"RNN [{market}/{account}] trained — N={len(X)} acc={acc:.1%}")
    return meta


def predict(candles: np.ndarray, market: str, account: str = '') -> Optional[dict]:
    """SHADOW MODE — read-only inference, never touches orders."""
    try:
        tag  = f"{account}_" if account else ""
        pt   = os.path.join(_MODEL_DIR, market, f"{tag}rnn_latest.pt")
        if not os.path.exists(pt):
            return None
        arr  = candles[-SEQ_LEN:] if len(candles) >= SEQ_LEN else candles
        if len(arr) < SEQ_LEN:
            pad = np.zeros((SEQ_LEN - len(arr), 5), dtype=np.float32)
            arr = np.vstack([pad, arr])
        arr   = _normalise(arr)
        x     = torch.tensor(arr).unsqueeze(0)      # (1, SEQ_LEN, 5)
        state = torch.load(pt, map_location='cpu', weights_only=True)
        model = CB6LSTM()
        model.load_state_dict(state)
        model.eval()
        with torch.no_grad():
            wp, rp = model(x)
        win_prob = float(wp.item())
        r_hat    = float(rp.item())
        conf = 'HIGH' if win_prob >= 0.70 else ('MEDIUM' if win_prob >= 0.55
               else ('AVOID' if win_prob <= 0.35 else 'LOW'))
        return {'win_prob': round(win_prob, 4), 'r_hat': round(r_hat, 3),
                'confidence': conf, 'model': 'RNN'}
    except Exception as e:
        logger.error(f"RNN predict error: {e}")
        return None
