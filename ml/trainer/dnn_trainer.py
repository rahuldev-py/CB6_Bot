# ml/trainer/dnn_trainer.py
#
# Deep Neural Network (fully-connected) for trade quality prediction.
# Two heads:
#   1. WIN/LOSS classifier  (sigmoid → probability of win)
#   2. R-multiple regressor (linear  → expected R)
#
# Architecture: Input → BN → 256 → 128 → 64 → dual heads
# Trained on tabular features from data_pipeline.py
#
# SHADOW MODE ONLY — this module never places or modifies orders.

from __future__ import annotations
import os, json
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split
from sklearn.preprocessing import StandardScaler
import joblib

from utils.logger import logger

_ROOT       = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_DIR  = os.path.join(_ROOT, 'ml', 'models')
MIN_SAMPLES = 30   # minimum completed trades before training


# ── Model definition ───────────────────────────────────────────────────────────

class CB6DNN(nn.Module):
    """
    Dual-head DNN:
      win_prob  — P(trade is a win)  [0, 1]
      r_hat     — expected R-multiple (unbounded)
    """
    def __init__(self, n_features: int):
        super().__init__()
        self.bn_in  = nn.BatchNorm1d(n_features)
        self.shared = nn.Sequential(
            nn.Linear(n_features, 256), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(256, 128),        nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 64),         nn.ReLU(),
        )
        self.win_head = nn.Sequential(nn.Linear(64, 1), nn.Sigmoid())
        self.r_head   = nn.Linear(64, 1)

    def forward(self, x):
        x   = self.bn_in(x)
        h   = self.shared(x)
        return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)


# ── Train ──────────────────────────────────────────────────────────────────────

def train(market: str, account: str = '',
          epochs: int = 150, lr: float = 1e-3,
          batch_size: int = 32) -> Optional[dict]:
    """
    Train DNN on available completed trades.
    Returns metrics dict or None if insufficient data.
    """
    from ml.data_pipeline import join_trades, build_nse_features, build_forex_features

    df = join_trades(market, account)
    if len(df) < MIN_SAMPLES:
        logger.warning(f"DNN [{market}/{account}]: only {len(df)} trades, "
                       f"need {MIN_SAMPLES} — skipping training")
        return None

    # Feature extraction
    if market == 'nse':
        X, y_cls, y_r = build_nse_features(df)
    else:
        X, y_cls, y_r = build_forex_features(df)

    # Scale features
    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X).astype(np.float32)

    Xt  = torch.tensor(X_sc)
    yct = torch.tensor(y_cls)
    yrt = torch.tensor(y_r)

    dataset    = TensorDataset(Xt, yct, yrt)
    val_size   = max(1, int(len(dataset) * 0.2))
    train_size = len(dataset) - val_size
    train_ds, val_ds = random_split(dataset, [train_size, val_size],
                                    generator=torch.Generator().manual_seed(42))

    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_dl   = DataLoader(val_ds,   batch_size=batch_size)

    model     = CB6DNN(X_sc.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=15, factor=0.5)
    cls_loss  = nn.BCELoss()
    reg_loss  = nn.HuberLoss()

    best_val, best_state = float('inf'), None

    for epoch in range(epochs):
        model.train()
        for xb, yc, yr in train_dl:
            optimizer.zero_grad()
            wp, rp = model(xb)
            loss   = cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)
            loss.backward()
            optimizer.step()

        # Validation
        model.eval()
        val_losses = []
        with torch.no_grad():
            for xb, yc, yr in val_dl:
                wp, rp   = model(xb)
                vl       = cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)
                val_losses.append(vl.item())
        vl_mean = float(np.mean(val_losses))
        scheduler.step(vl_mean)
        if vl_mean < best_val:
            best_val   = vl_mean
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)

    # Accuracy on full dataset
    model.eval()
    with torch.no_grad():
        wp_all, _ = model(Xt)
    preds     = (wp_all.numpy() >= 0.5).astype(int)
    acc       = float((preds == y_cls.astype(int)).mean())
    precision = float(np.sum((preds == 1) & (y_cls == 1)) / (np.sum(preds == 1) + 1e-9))
    recall    = float(np.sum((preds == 1) & (y_cls == 1)) / (np.sum(y_cls == 1) + 1e-9))

    # Save
    tag    = f"{account}_" if account else ""
    stamp  = datetime.now().strftime('%Y%m%d_%H%M')
    mdir   = os.path.join(_MODEL_DIR, market)
    os.makedirs(mdir, exist_ok=True)

    torch.save(best_state,  os.path.join(mdir, f"{tag}dnn_{stamp}.pt"))
    torch.save(best_state,  os.path.join(mdir, f"{tag}dnn_latest.pt"))
    joblib.dump(scaler,     os.path.join(mdir, f"{tag}dnn_scaler_latest.pkl"))

    meta = {
        'trained_at' : stamp,
        'market'     : market,
        'account'    : account,
        'n_samples'  : len(df),
        'n_features' : X_sc.shape[1],
        'epochs'     : epochs,
        'val_loss'   : round(best_val, 6),
        'accuracy'   : round(acc, 4),
        'precision'  : round(precision, 4),
        'recall'     : round(recall, 4),
    }
    with open(os.path.join(mdir, f"{tag}dnn_meta_latest.json"), 'w') as f:
        json.dump(meta, f, indent=2)

    logger.info(
        f"DNN [{market}/{account}] trained — "
        f"N={len(df)} acc={acc:.1%} precision={precision:.1%} recall={recall:.1%}"
    )
    return meta


# ── Predict ────────────────────────────────────────────────────────────────────

def predict(features: np.ndarray, market: str, account: str = '') -> Optional[dict]:
    """
    Run inference on a single feature vector.
    Returns {'win_prob': float, 'r_hat': float, 'confidence': str} or None.
    NEVER places or modifies orders.
    """
    try:
        from ml.data_pipeline import NSE_FEATURES, FOREX_FEATURES
        tag   = f"{account}_" if account else ""
        mdir  = os.path.join(_MODEL_DIR, market)
        pt    = os.path.join(mdir, f"{tag}dnn_latest.pt")
        sc    = os.path.join(mdir, f"{tag}dnn_scaler_latest.pkl")
        meta  = os.path.join(mdir, f"{tag}dnn_meta_latest.json")

        if not all(os.path.exists(p) for p in [pt, sc, meta]):
            return None     # model not trained yet

        with open(meta) as f:
            m = json.load(f)
        n_feat = m['n_features']

        if features.shape[0] != n_feat:
            return None

        scaler = joblib.load(sc)
        state  = torch.load(pt, map_location='cpu', weights_only=True)
        model  = CB6DNN(n_feat)
        model.load_state_dict(state)
        model.eval()

        x_sc = scaler.transform(features.reshape(1, -1)).astype(np.float32)
        with torch.no_grad():
            wp, rp = model(torch.tensor(x_sc))
        win_prob = float(wp.item())
        r_hat    = float(rp.item())

        if win_prob >= 0.70:
            conf = 'HIGH'
        elif win_prob >= 0.55:
            conf = 'MEDIUM'
        elif win_prob <= 0.35:
            conf = 'AVOID'
        else:
            conf = 'LOW'

        return {'win_prob': round(win_prob, 4), 'r_hat': round(r_hat, 3),
                'confidence': conf, 'model': 'DNN'}
    except Exception as e:
        logger.error(f"DNN predict error: {e}")
        return None
