"""
ml/bt_trainer.py
CB6 Quantum — Backtest CSV → ML Models trainer

Reads: ml/training_data/bt_combined_2024_2026.csv  (1206 labeled trades)
       May 2024 → May 2026 | NIFTY · BANKNIFTY · MIDCPNIFTY · FINNIFTY
Trains:
  1. DNN  — 18 tabular features → win_prob + r_hat
  2. CNN  — 5-group feature profile → setup pattern classifier
  3. RNN  — daily trade sequences → regime-aware predictor

All models use BCEWithLogitsLoss + pos_weight to prevent recall trap.
Saves models to ml/models/nse/ in the same slot the predictor loads from,
so /ml_scan immediately uses real trained weights.

SHADOW MODE — never touches orders.
"""
from __future__ import annotations
import os, json, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import logger

_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV      = os.path.join(_ROOT, 'ml', 'training_data', 'bt_combined_2024_2026.csv')
_MODEL_DIR = os.path.join(_ROOT, 'ml', 'models', 'nse')
os.makedirs(_MODEL_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# 1. LOAD & FEATURE ENGINEERING
# ══════════════════════════════════════════════════════════════════════════════

def load_and_engineer(path: str = _CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"  Loaded {len(df)} trades from CSV")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Win rate: {df['win'].mean()*100:.1f}%  |  "
          f"Avg R: {df['r'].mean():.3f}")

    # ── Encodings ──────────────────────────────────────────────────────────────
    df['dir_enc']    = (df['dir'] == 'LONG').astype(float)          # 1=LONG 0=SHORT
    df['mss_enc']    = (df['mss'].str.upper() == 'CHOCH').astype(float)  # 1=CHoCH 0=BOS
    df['regime_enc'] = df['regime'].str.upper().map(
        {'TRENDING': 2.0, 'NEUTRAL': 1.0, 'CHOPPY': 0.0}).fillna(1.0)
    df['index_enc']  = df['index'].map(
        {
            'NIFTY': 0,
            'MIDCPNIFTY': 1,
            'BANKNIFTY': 2,
            'FINNIFTY': 3,
            'XAUUSD': 4,
            'XAGUSD': 5,
            'USOIL': 6,
        }).fillna(0)

    # ── Year / market-era encoding (2024=0, 2025=0.5, 2026=1.0) ───────────────
    df['year_enc'] = (pd.to_datetime(df['date']).dt.year - 2024) / 2.0

    # ── FVG position relative to entry (in R-units) ────────────────────────────
    # How far the FVG top/bottom sits from entry — captures how deep price enters gap
    risk_clip = df['risk_pts'].clip(lower=0.01)
    df['fvg_top_dist']    = (df['fvg_top']    - df['entry']).abs() / risk_clip
    df['fvg_bottom_dist'] = (df['fvg_bottom'] - df['entry']).abs() / risk_clip
    # Cap at 5R to prevent outlier domination
    df['fvg_top_dist']    = df['fvg_top_dist'].clip(upper=5.0)
    df['fvg_bottom_dist'] = df['fvg_bottom_dist'].clip(upper=5.0)

    # ── RR geometry (R-multiples to each target from entry) ────────────────────
    df['rr_t1'] = (df['t1'] - df['entry']).abs() / df['risk_pts'].clip(lower=0.01)
    df['rr_t2'] = (df['t2'] - df['entry']).abs() / df['risk_pts'].clip(lower=0.01)
    df['rr_t3'] = (df['t3'] - df['entry']).abs() / df['risk_pts'].clip(lower=0.01)

    # ── FVG relative size ───────────────────────────────────────────────────────
    df['fvg_ratio']  = df['fvg_size'] / df['risk_pts'].clip(lower=0.01)

    # ── Session block (morning / midday / pre-close) ────────────────────────────
    df['session_enc'] = pd.cut(
        df['hour'],
        bins=[9, 11, 13, 15, 16],
        labels=[0, 1, 2, 3],
        right=True
    ).astype(float).fillna(0)

    # ── Outcome one-hot (for multi-class head, optional) ───────────────────────
    df['out_t3']  = (df['outcome'] == 'T3').astype(float)
    df['out_t2']  = (df['outcome'] == 'T2').astype(float)
    df['out_t1']  = (df['outcome'] == 'T1').astype(float)
    df['out_sl']  = (df['outcome'] == 'SL').astype(float)
    df['out_eod'] = (df['outcome'].isin(['EOD','TIMEOUT'])).astype(float)

    return df


# Feature columns used by DNN (tabular) — ENTRY-TIME ONLY
# hold_mins is EXCLUDED: it is a post-trade metric (lookahead bias).
# The model must infer quality purely from what is known at entry.
TABULAR_FEATURES = [
    # Structure & direction (4)
    'dir_enc', 'mss_enc', 'regime_enc', 'index_enc',
    # Setup quality (3)
    'score', 'fvg_size', 'fvg_ratio',
    # Risk geometry (4)
    'risk_pts', 'rr_t1', 'rr_t2', 'rr_t3',
    # Time context (4)
    'hour', 'minute', 'weekday', 'session_enc',
    # NEW: FVG position + market era (3)
    'fvg_top_dist', 'fvg_bottom_dist', 'year_enc',
]
# Total: 18 features  |  hold_mins kept in CSV for post-trade analysis only


# ══════════════════════════════════════════════════════════════════════════════
# 2. DNN TRAINER
# ══════════════════════════════════════════════════════════════════════════════

def train_dnn(df: pd.DataFrame, epochs: int = 200, lr: float = 1e-3,
              batch_size: int = 32) -> dict:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset, random_split
    from sklearn.preprocessing import StandardScaler
    import joblib
    from datetime import datetime

    print("\n" + "─"*55)
    print("  DNN TRAINER")
    print("─"*55)

    X = df[TABULAR_FEATURES].fillna(0).values.astype(np.float32)
    y_cls = df['win'].values.astype(np.float32)
    y_r   = df['r'].values.astype(np.float32)
    n, f  = X.shape

    # ── Class imbalance: compute pos_weight ────────────────────────────────────
    n_pos = float(y_cls.sum())
    n_neg = float(n - n_pos)
    pos_w = n_neg / max(n_pos, 1)   # = n_losses / n_wins ≈ 0.21 at 83% WR
    print(f"  Samples: {n}  |  Features: {f}")
    print(f"  Class dist: {int(n_pos)} wins / {int(n_neg)} losses  "
          f"→ pos_weight={pos_w:.3f}  (down-weights dominant win class)")

    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X).astype(np.float32)

    Xt  = torch.tensor(X_sc)
    yct = torch.tensor(y_cls)
    yrt = torch.tensor(y_r)

    dataset  = TensorDataset(Xt, yct, yrt)
    val_n    = max(1, int(n * 0.15))
    test_n   = max(1, int(n * 0.10))
    train_n  = n - val_n - test_n
    train_ds, val_ds, test_ds = random_split(
        dataset, [train_n, val_n, test_n],
        generator=torch.Generator().manual_seed(42)
    )
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)
    val_dl   = DataLoader(val_ds,   batch_size=batch_size, drop_last=False)
    test_dl  = DataLoader(test_ds,  batch_size=batch_size, drop_last=False)

    # ── Architecture: logit output (no Sigmoid) → BCEWithLogitsLoss ───────────
    class CB6DNN(nn.Module):
        def __init__(self, nf):
            super().__init__()
            self.bn_in  = nn.BatchNorm1d(nf)
            self.shared = nn.Sequential(
                nn.Linear(nf, 256), nn.GELU(), nn.Dropout(0.3),
                nn.Linear(256, 128), nn.GELU(), nn.Dropout(0.2),
                nn.Linear(128, 64),  nn.GELU(), nn.Dropout(0.1),
                nn.Linear(64,  32),  nn.GELU(),
            )
            self.win_head = nn.Linear(32, 1)   # logit — no Sigmoid here
            self.r_head   = nn.Linear(32, 1)
        def forward(self, x):
            x = self.bn_in(x)
            h = self.shared(x)
            return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)

    model     = CB6DNN(f)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched     = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    # BCEWithLogitsLoss + pos_weight to break recall trap
    cls_loss  = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_w]))
    reg_loss  = nn.HuberLoss(delta=1.0)

    best_val, best_state, patience_cnt = float('inf'), None, 0
    PATIENCE = 30

    for epoch in range(epochs):
        model.train()
        for xb, yc, yr in train_dl:
            optimizer.zero_grad()
            wp, rp = model(xb)
            loss   = cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        sched.step()

        model.eval()
        vl_list = []
        with torch.no_grad():
            for xb, yc, yr in val_dl:
                wp, rp = model(xb)
                vl_list.append((cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)).item())
        vl = float(np.mean(vl_list))

        if vl < best_val:
            best_val  = vl
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f"  Early stop at epoch {epoch+1}")
                break

        if (epoch + 1) % 25 == 0:
            print(f"  Epoch {epoch+1:3d}/{epochs}  val_loss={vl:.4f}  best={best_val:.4f}")

    model.load_state_dict(best_state)

    # ── Evaluate on test set ───────────────────────────────────────────────────
    model.eval()
    all_logits, all_true_cls, all_true_r, all_r_hat = [], [], [], []
    with torch.no_grad():
        for xb, yc, yr in test_dl:
            wp, rp = model(xb)
            all_logits.extend(wp.numpy())     # raw logits
            all_true_cls.extend(yc.numpy())
            all_true_r.extend(yr.numpy())
            all_r_hat.extend(rp.numpy())

    # Apply sigmoid to convert logits → probabilities
    probs     = 1 / (1 + np.exp(-np.array(all_logits)))
    preds_bin = (probs >= 0.5).astype(int)
    true_cls  = np.array(all_true_cls).astype(int)
    acc       = float((preds_bin == true_cls).mean())
    prec      = float(np.sum((preds_bin == 1) & (true_cls == 1)) / (np.sum(preds_bin == 1) + 1e-9))
    rec       = float(np.sum((preds_bin == 1) & (true_cls == 1)) / (np.sum(true_cls == 1) + 1e-9))
    f1        = 2 * prec * rec / (prec + rec + 1e-9)
    mae_r     = float(np.mean(np.abs(np.array(all_r_hat) - np.array(all_true_r))))

    print(f"\n  ✅ DNN Results (test set n={len(all_logits)}):")
    print(f"     Accuracy  : {acc*100:.1f}%")
    print(f"     Precision : {prec*100:.1f}%  (TP / predicted wins)")
    print(f"     Recall    : {rec*100:.1f}%  (TP / actual wins)")
    print(f"     F1        : {f1*100:.1f}%")
    print(f"     MAE_R     : {mae_r:.3f}R")
    print(f"     Val Loss  : {best_val:.4f}")
    print(f"     pos_weight: {pos_w:.3f}  (class balance applied)")

    # ── Save ───────────────────────────────────────────────────────────────────
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    torch.save(best_state,  os.path.join(_MODEL_DIR, f"dnn_{stamp}.pt"))
    torch.save(best_state,  os.path.join(_MODEL_DIR, "dnn_latest.pt"))
    joblib.dump(scaler,     os.path.join(_MODEL_DIR, "dnn_scaler_latest.pkl"))

    meta = {
        'trained_at'  : stamp,
        'market'      : 'nse',
        'source'      : 'bt_combined_2024_2026',
        'n_samples'   : n,
        'n_features'  : f,
        'features'    : TABULAR_FEATURES,
        'epochs_run'  : epoch + 1,
        'val_loss'    : round(best_val, 6),
        'test_acc'    : round(acc, 4),
        'test_prec'   : round(prec, 4),
        'test_recall' : round(rec, 4),
        'test_mae_r'  : round(mae_r, 4),
    }
    with open(os.path.join(_MODEL_DIR, "dnn_meta_latest.json"), 'w') as f_:
        json.dump(meta, f_, indent=2)
    print(f"  Saved → ml/models/nse/dnn_latest.pt")
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# 3. CNN TRAINER  (1D conv on the feature vector treated as a sequence)
#    We build a synthetic 5-step sequence per trade from its feature components
#    so the CNN can learn local temporal patterns within each setup's profile
# ══════════════════════════════════════════════════════════════════════════════

def train_cnn(df: pd.DataFrame, epochs: int = 150, lr: float = 5e-4,
              batch_size: int = 32) -> dict:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset, random_split
    import joblib
    from datetime import datetime

    print("\n" + "─"*55)
    print("  CNN TRAINER  (1D conv on setup profile)")
    print("─"*55)

    # Build 5-group feature profile: [structure, score, geometry, time, outcome_priors]
    def build_cnn_sequence(df):
        """
        Each trade → (5, C) matrix where each row is a feature group.
        Groups:
          0: structure  (dir, mss, regime, index)
          1: score/fvg  (score, fvg_size, fvg_ratio, risk_pts)
          2: geometry   (rr_t1, rr_t2, rr_t3, hold_mins)
          3: time       (hour, minute, weekday, session_enc)
          4: candle ctx (fvg_top, fvg_bottom, entry, sl)  ← normalized per-trade
        """
        groups = []
        for _, row in df.iterrows():
            ep  = row['entry']  if row['entry']  > 0 else 1.0
            sl  = row['sl']     if row['sl']      > 0 else ep * 0.99
            fvg_top = row.get('fvg_top', ep)
            fvg_bot = row.get('fvg_bottom', sl)

            g0 = [row['dir_enc'], row['mss_enc'], row['regime_enc']/2.0, row['index_enc']/3.0]
            g1 = [row['score']/26.0, min(row['fvg_size']/50.0,1), min(row['fvg_ratio'],5)/5.0, min(row['risk_pts']/100.0,1)]
            g2 = [min(row['rr_t1'],6)/6.0, min(row['rr_t2'],6)/6.0, min(row['rr_t3'],6)/6.0, min(row['hold_mins'],120)/120.0]
            g3 = [row['hour']/15.0, row['minute']/60.0, row['weekday']/4.0, row['session_enc']/3.0]
            g4 = [(fvg_top-ep)/(abs(ep-sl)+1e-6), (fvg_bot-ep)/(abs(ep-sl)+1e-6),
                  (ep-sl)/(abs(ep)+1e-6), row['dir_enc']]
            groups.append([g0, g1, g2, g3, g4])
        return np.array(groups, dtype=np.float32)   # (N, 5, 4)

    X_seq = build_cnn_sequence(df)
    y_cls = df['win'].values.astype(np.float32)
    y_r   = df['r'].values.astype(np.float32)
    n     = len(X_seq)
    print(f"  Sequence shape: {X_seq.shape}  (N=trades, steps=5, channels=4)")

    # Normalize per-channel across dataset
    for c in range(X_seq.shape[2]):
        mu = X_seq[:,:,c].mean()
        sd = X_seq[:,:,c].std() + 1e-8
        X_seq[:,:,c] = (X_seq[:,:,c] - mu) / sd

    Xt  = torch.tensor(X_seq).permute(0, 2, 1)  # (N, C=4, L=5) for Conv1d
    yct = torch.tensor(y_cls)
    yrt = torch.tensor(y_r)

    dataset  = TensorDataset(Xt, yct, yrt)
    val_n    = max(1, int(n * 0.15))
    test_n   = max(1, int(n * 0.10))
    train_n  = n - val_n - test_n
    train_ds, val_ds, test_ds = random_split(
        dataset, [train_n, val_n, test_n],
        generator=torch.Generator().manual_seed(42)
    )
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_dl   = DataLoader(val_ds,   batch_size=batch_size)
    test_dl  = DataLoader(test_ds,  batch_size=batch_size)

    # Class imbalance — same correction as DNN
    n_pos_c = float(y_cls.sum())
    n_neg_c = float(n - n_pos_c)
    pos_w_c = n_neg_c / max(n_pos_c, 1)
    print(f"  Class dist: {int(n_pos_c)} wins / {int(n_neg_c)} losses  "
          f"→ pos_weight={pos_w_c:.3f}")

    class CB6CNN(nn.Module):
        def __init__(self, in_ch=4, seq_len=5):
            super().__init__()
            self.conv = nn.Sequential(
                nn.Conv1d(in_ch, 32, kernel_size=2, padding=1), nn.GELU(),
                nn.Conv1d(32,    64, kernel_size=2, padding=0), nn.GELU(),
                nn.AdaptiveAvgPool1d(1),
            )
            self.head = nn.Sequential(
                nn.Flatten(),
                nn.Linear(64, 32), nn.GELU(), nn.Dropout(0.2),
            )
            self.win_head = nn.Linear(32, 1)   # logit — no Sigmoid, BCEWithLogitsLoss
            self.r_head   = nn.Linear(32, 1)
        def forward(self, x):
            h = self.head(self.conv(x))
            return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)

    model     = CB6CNN()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched     = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    cls_loss  = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_w_c]))  # fixes recall trap
    reg_loss  = nn.HuberLoss(delta=1.0)

    best_val, best_state, patience_cnt = float('inf'), None, 0
    PATIENCE = 25

    for epoch in range(epochs):
        model.train()
        for xb, yc, yr in train_dl:
            optimizer.zero_grad()
            wp, rp = model(xb)
            loss   = cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)
            loss.backward()
            optimizer.step()
        sched.step()

        model.eval()
        vl_list = []
        with torch.no_grad():
            for xb, yc, yr in val_dl:
                wp, rp = model(xb)
                vl_list.append((cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)).item())
        vl = float(np.mean(vl_list))
        if vl < best_val:
            best_val  = vl
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f"  Early stop at epoch {epoch+1}")
                break
        if (epoch + 1) % 25 == 0:
            print(f"  Epoch {epoch+1:3d}/{epochs}  val_loss={vl:.4f}  best={best_val:.4f}")

    model.load_state_dict(best_state)

    model.eval()
    all_logits_c, all_true_c = [], []
    with torch.no_grad():
        for xb, yc, yr in test_dl:
            wp, _ = model(xb)
            all_logits_c.extend(wp.numpy())
            all_true_c.extend(yc.numpy())

    probs_c = 1 / (1 + np.exp(-np.array(all_logits_c)))   # sigmoid on logits
    pb    = (probs_c >= 0.5).astype(int)
    tc    = np.array(all_true_c).astype(int)
    acc   = float((pb == tc).mean())
    prec  = float(np.sum((pb==1)&(tc==1)) / (np.sum(pb==1)+1e-9))
    rec   = float(np.sum((pb==1)&(tc==1)) / (np.sum(tc==1)+1e-9))
    f1_c  = 2 * prec * rec / (prec + rec + 1e-9)

    print(f"\n  ✅ CNN Results (test set n={len(all_logits_c)}):")
    print(f"     Accuracy  : {acc*100:.1f}%")
    print(f"     Precision : {prec*100:.1f}%")
    print(f"     Recall    : {rec*100:.1f}%")
    print(f"     F1        : {f1_c*100:.1f}%")
    print(f"     pos_weight: {pos_w_c:.3f}  (class balance applied)")

    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    torch.save(best_state,  os.path.join(_MODEL_DIR, f"cnn_{stamp}.pt"))
    torch.save(best_state,  os.path.join(_MODEL_DIR, "cnn_latest.pt"))

    # Save the channel normalization params for inference
    ch_stats = {}
    X_seq_raw = build_cnn_sequence(df)
    for c in range(X_seq_raw.shape[2]):
        ch_stats[c] = {'mu': float(X_seq_raw[:,:,c].mean()),
                       'sd': float(X_seq_raw[:,:,c].std() + 1e-8)}
    import joblib
    joblib.dump(ch_stats, os.path.join(_MODEL_DIR, "cnn_ch_stats_latest.pkl"))

    meta = {
        'trained_at': stamp, 'market': 'nse', 'source': 'bt_365d_csv',
        'n_samples': n, 'seq_shape': list(X_seq.shape),
        'val_loss': round(best_val, 6),
        'test_acc': round(acc, 4), 'test_prec': round(prec, 4), 'test_recall': round(rec, 4),
    }
    with open(os.path.join(_MODEL_DIR, "cnn_meta_latest.json"), 'w') as f_:
        json.dump(meta, f_, indent=2)
    print(f"  Saved → ml/models/nse/cnn_latest.pt")
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# 4. RNN TRAINER  (GRU on sequence of trades per day — regime learning)
# ══════════════════════════════════════════════════════════════════════════════

def train_rnn(df: pd.DataFrame, epochs: int = 120, lr: float = 5e-4,
              batch_size: int = 16) -> dict:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset, random_split
    from datetime import datetime

    print("\n" + "─"*55)
    print("  RNN TRAINER  (GRU — daily trade sequences)")
    print("─"*55)

    # Group trades by date → each date is a sequence of up to 8 trades
    SEQ_LEN  = 8     # max trades per day sequence
    FEAT_DIM = 8     # per-trade feature vector

    def trade_vec(row):
        return [
            row['dir_enc'],
            row['mss_enc'],
            row['regime_enc'] / 2.0,
            row['score']      / 26.0,
            min(row['fvg_ratio'], 5) / 5.0,
            row['session_enc'] / 3.0,
            row['weekday']    / 4.0,
            row['index_enc']  / 3.0,
        ]

    df_sorted = df.sort_values(['date', 'time']).reset_index(drop=True)
    sequences = []
    labels    = []
    r_labels  = []

    for date, grp in df_sorted.groupby('date'):
        grp = grp.reset_index(drop=True)
        vecs = [trade_vec(row) for _, row in grp.iterrows()]

        # Pad / truncate to SEQ_LEN
        if len(vecs) >= SEQ_LEN:
            vecs = vecs[:SEQ_LEN]
        else:
            pad = [[0.0]*FEAT_DIM] * (SEQ_LEN - len(vecs))
            vecs = vecs + pad

        # Label = last trade's win + mean R of the day
        sequences.append(vecs)
        labels.append(float(grp['win'].iloc[-1]))
        r_labels.append(float(grp['r'].mean()))

    X_rnn = np.array(sequences, dtype=np.float32)   # (days, SEQ_LEN, FEAT_DIM)
    y_cls = np.array(labels,    dtype=np.float32)
    y_r   = np.array(r_labels,  dtype=np.float32)
    n     = len(X_rnn)
    print(f"  Daily sequences: {n}  |  shape: {X_rnn.shape}")

    Xt  = torch.tensor(X_rnn)
    yct = torch.tensor(y_cls)
    yrt = torch.tensor(y_r)

    dataset  = TensorDataset(Xt, yct, yrt)
    val_n    = max(1, int(n * 0.15))
    test_n   = max(1, int(n * 0.10))
    train_n  = n - val_n - test_n
    if train_n <= 0:
        print("  ⚠ Not enough daily sequences for RNN — skipping")
        return {}
    train_ds, val_ds, test_ds = random_split(
        dataset, [train_n, val_n, test_n],
        generator=torch.Generator().manual_seed(42)
    )
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_dl   = DataLoader(val_ds,   batch_size=batch_size)
    test_dl  = DataLoader(test_ds,  batch_size=batch_size)

    class CB6RNN(nn.Module):
        def __init__(self, feat=FEAT_DIM, hidden=64, layers=2):
            super().__init__()
            self.gru     = nn.GRU(feat, hidden, layers, batch_first=True,
                                   dropout=0.2, bidirectional=False)
            self.win_head = nn.Sequential(
                nn.Linear(hidden, 16), nn.GELU(),
                nn.Linear(16, 1), nn.Sigmoid()
            )
            self.r_head = nn.Linear(hidden, 1)
        def forward(self, x):
            out, _ = self.gru(x)
            h      = out[:, -1, :]   # last step
            return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)

    model     = CB6RNN()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched     = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    cls_loss  = nn.BCELoss()
    reg_loss  = nn.HuberLoss(delta=1.0)

    best_val, best_state, patience_cnt = float('inf'), None, 0
    PATIENCE = 20

    for epoch in range(epochs):
        model.train()
        for xb, yc, yr in train_dl:
            optimizer.zero_grad()
            wp, rp = model(xb)
            loss   = cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        sched.step()

        model.eval()
        vl_list = []
        with torch.no_grad():
            for xb, yc, yr in val_dl:
                wp, rp = model(xb)
                vl_list.append((cls_loss(wp, yc) + 0.5 * reg_loss(rp, yr)).item())
        vl = float(np.mean(vl_list))
        if vl < best_val:
            best_val   = vl
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f"  Early stop at epoch {epoch+1}")
                break
        if (epoch + 1) % 20 == 0:
            print(f"  Epoch {epoch+1:3d}/{epochs}  val_loss={vl:.4f}  best={best_val:.4f}")

    model.load_state_dict(best_state)

    model.eval()
    all_preds, all_true = [], []
    with torch.no_grad():
        for xb, yc, yr in test_dl:
            wp, _ = model(xb)
            all_preds.extend(wp.numpy())
            all_true.extend(yc.numpy())

    pb  = (np.array(all_preds) >= 0.5).astype(int)
    tc  = np.array(all_true).astype(int)
    acc = float((pb == tc).mean()) if len(pb) else 0.0

    print(f"\n  ✅ RNN Results (test set n={len(all_preds)}):")
    print(f"     Accuracy  : {acc*100:.1f}%")
    print(f"     Val Loss  : {best_val:.4f}")

    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    torch.save(best_state, os.path.join(_MODEL_DIR, f"rnn_{stamp}.pt"))
    torch.save(best_state, os.path.join(_MODEL_DIR, "rnn_latest.pt"))

    meta = {
        'trained_at': stamp, 'market': 'nse', 'source': 'bt_365d_csv',
        'n_days': n, 'seq_len': SEQ_LEN, 'feat_dim': FEAT_DIM,
        'val_loss': round(best_val, 6), 'test_acc': round(acc, 4),
    }
    with open(os.path.join(_MODEL_DIR, "rnn_meta_latest.json"), 'w') as f_:
        json.dump(meta, f_, indent=2)
    print(f"  Saved → ml/models/nse/rnn_latest.pt")
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# 5. INFERENCE — predict_from_setup()
#    Called by predictor.py for every live /ml_scan signal.
#    Builds the 16-feature vector from the setup dict and runs DNN + CNN ensemble.
# ══════════════════════════════════════════════════════════════════════════════

def _setup_to_tabular(setup: dict, index_name: str = 'NIFTY') -> np.ndarray:
    """
    Convert a scan_silver_bullet() setup dict → 18-feature numpy vector
    matching TABULAR_FEATURES order exactly.
    hold_mins is NOT included — post-trade lookahead.
    """
    from datetime import datetime
    sig    = setup.get('entry_signal', {})
    entry  = float(sig.get('entry',  0))
    sl     = float(sig.get('stop_loss', entry * 0.99))
    t1     = float(sig.get('target1', 0))
    t2     = float(sig.get('target2', 0))
    t3     = float(sig.get('target3', 0))
    risk   = float(sig.get('risk', max(abs(entry - sl), 0.01)))

    dirn   = setup.get('direction', 'BULLISH')
    mss    = str(setup.get('mss_type', 'BOS')).upper()
    regime = str(setup.get('regime', 'NEUTRAL')).upper()
    score  = float(setup.get('confluence', 0))
    fvg    = setup.get('fvg', {})
    fvg_sz = float(fvg.get('size', 0))
    fvg_top    = float(fvg.get('top',    entry))
    fvg_bottom = float(fvg.get('bottom', entry))

    now     = datetime.now()
    year    = float(now.year)

    dir_enc    = 1.0 if dirn == 'BULLISH' else 0.0
    mss_enc    = 1.0 if mss  == 'CHOCH'   else 0.0
    reg_enc    = {'TRENDING': 2.0, 'NEUTRAL': 1.0, 'CHOPPY': 0.0}.get(regime, 1.0)
    idx_enc    = {'NIFTY': 0.0, 'MIDCPNIFTY': 1.0, 'BANKNIFTY': 2.0, 'FINNIFTY': 3.0}.get(
                    index_name.upper(), 0.0)
    fvg_ratio  = min(fvg_sz / max(risk, 0.01), 10.0)
    rr_t1      = abs(t1 - entry) / max(risk, 0.01)
    rr_t2      = abs(t2 - entry) / max(risk, 0.01)
    rr_t3      = abs(t3 - entry) / max(risk, 0.01)
    hour       = float(now.hour)
    minute     = float(now.minute)
    weekday    = float(now.weekday())
    sess       = 0.0 if hour < 11 else (1.0 if hour < 13 else (2.0 if hour < 15 else 3.0))

    # NEW features
    year_enc       = min((year - 2024) / 2.0, 1.0)   # 2024=0, 2025=0.5, 2026=1.0
    fvg_top_dist   = min(abs(fvg_top    - entry) / max(risk, 0.01), 5.0)
    fvg_bottom_dist= min(abs(fvg_bottom - entry) / max(risk, 0.01), 5.0)

    return np.array([
        # Structure (4)
        dir_enc, mss_enc, reg_enc, idx_enc,
        # Quality (3)
        score, fvg_sz, fvg_ratio,
        # Geometry (4)
        risk, rr_t1, rr_t2, rr_t3,
        # Time (4)
        hour, minute, weekday, sess,
        # FVG position + era (3)
        fvg_top_dist, fvg_bottom_dist, year_enc,
    ], dtype=np.float32)   # total: 18


def predict_from_setup(setup: dict, index_name: str = 'NIFTY') -> dict:
    """
    Run DNN (+ CNN if available) on a live setup dict.
    Returns {'win_prob', 'r_hat', 'confidence', 'models_used'} or empty dict.
    SHADOW MODE — never places orders.
    """
    import torch
    import joblib

    feat = _setup_to_tabular(setup, index_name=index_name)

    results = []

    # ── DNN ────────────────────────────────────────────────────────────────────
    try:
        import torch.nn as nn
        scaler_path = os.path.join(_MODEL_DIR, 'dnn_scaler_latest.pkl')
        model_path  = os.path.join(_MODEL_DIR, 'dnn_latest.pt')
        meta_path   = os.path.join(_MODEL_DIR, 'dnn_meta_latest.json')
        if all(os.path.exists(p) for p in [scaler_path, model_path, meta_path]):
            with open(meta_path) as f_:
                m = json.load(f_)
            n_feat = m['n_features']
            if feat.shape[0] == n_feat:
                scaler = joblib.load(scaler_path)
                x_sc   = scaler.transform(feat.reshape(1, -1)).astype(np.float32)

                class _DNN(nn.Module):
                    def __init__(self, nf):
                        super().__init__()
                        self.bn_in  = nn.BatchNorm1d(nf)
                        self.shared = nn.Sequential(
                            nn.Linear(nf, 256), nn.GELU(), nn.Dropout(0.3),
                            nn.Linear(256, 128), nn.GELU(), nn.Dropout(0.2),
                            nn.Linear(128, 64),  nn.GELU(), nn.Dropout(0.1),
                            nn.Linear(64,  32),  nn.GELU(),
                        )
                        self.win_head = nn.Linear(32, 1)   # logit — sigmoid applied at inference
                        self.r_head   = nn.Linear(32, 1)
                    def forward(self, x):
                        x = self.bn_in(x); h = self.shared(x)
                        return self.win_head(h).squeeze(-1), self.r_head(h).squeeze(-1)

                model = _DNN(n_feat)
                model.load_state_dict(torch.load(model_path, map_location='cpu',
                                                  weights_only=True))
                model.eval()
                with torch.no_grad():
                    logit, rp = model(torch.tensor(x_sc))
                # Sigmoid converts logit → probability (model outputs raw logits)
                win_prob = float(torch.sigmoid(logit).item())
                results.append({'model': 'DNN',
                                'win_prob': round(win_prob, 4),
                                'r_hat'   : round(float(rp.item()), 3)})
    except Exception as e:
        logger.debug(f"bt_trainer DNN predict: {e}")

    if not results:
        return {}

    # Ensemble (DNN only for now — CNN needs price series)
    wp_avg = float(np.mean([r['win_prob'] for r in results]))
    rh_avg = float(np.mean([r['r_hat']   for r in results]))

    if wp_avg >= 0.70:   conf = 'HIGH'
    elif wp_avg >= 0.55: conf = 'MEDIUM'
    elif wp_avg <= 0.35: conf = 'AVOID'
    else:                conf = 'LOW'

    return {
        'win_prob'   : round(wp_avg, 4),
        'r_hat'      : round(rh_avg, 3),
        'confidence' : conf,
        'models_used': [r['model'] for r in results],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. FEATURE IMPORTANCE (quick permutation check on DNN)
# ══════════════════════════════════════════════════════════════════════════════

def feature_importance(df: pd.DataFrame) -> None:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import cross_val_score

    print("\n" + "─"*55)
    print("  FEATURE IMPORTANCE  (GBM permutation, 5-fold CV)")
    print("─"*55)

    X = df[TABULAR_FEATURES].fillna(0).values
    y = df['win'].values.astype(int)

    gbm = GradientBoostingClassifier(n_estimators=200, max_depth=4,
                                      learning_rate=0.05, random_state=42)
    scores = cross_val_score(gbm, X, y, cv=5, scoring='accuracy')
    print(f"  GBM 5-fold CV accuracy: {scores.mean()*100:.1f}% ± {scores.std()*100:.1f}%")

    gbm.fit(X, y)
    imp = sorted(zip(TABULAR_FEATURES, gbm.feature_importances_),
                 key=lambda x: -x[1])
    print(f"\n  Top features:")
    for feat, score in imp[:10]:
        bar = '█' * int(score * 200)
        print(f"    {feat:20s}  {score:.4f}  {bar}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description='CB6 Quantum ML Trainer')
    parser.add_argument('--csv', type=str, default=_CSV,
                        help='Path to labeled trades CSV (default: bt_365d_trades.csv)')
    args = parser.parse_args()
    csv_path = args.csv

    print("=" * 55)
    print("  CB6 QUANTUM — ML Trainer")
    print(f"  CSV  : {os.path.basename(csv_path)}")
    print(f"  Time : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    if not os.path.exists(csv_path):
        print(f"ERROR: CSV not found at {csv_path}")
        sys.exit(1)

    df = load_and_engineer(csv_path)

    # Feature importance check first (fast, no GPU needed)
    try:
        feature_importance(df)
    except Exception as e:
        print(f"  ⚠ Feature importance skipped: {e}")

    # Train all 3 models
    results = {}
    try:
        results['dnn'] = train_dnn(df, epochs=200)
    except Exception as e:
        print(f"\n  ❌ DNN failed: {e}")
        import traceback; traceback.print_exc()

    try:
        results['cnn'] = train_cnn(df, epochs=150)
    except Exception as e:
        print(f"\n  ❌ CNN failed: {e}")
        import traceback; traceback.print_exc()

    try:
        results['rnn'] = train_rnn(df, epochs=120)
    except Exception as e:
        print(f"\n  ❌ RNN failed: {e}")
        import traceback; traceback.print_exc()

    print("\n" + "=" * 55)
    print("  TRAINING COMPLETE")
    print("=" * 55)
    for model_name, meta in results.items():
        if meta:
            acc = meta.get('test_acc', meta.get('val_loss', '?'))
            print(f"  {model_name.upper():4s}  acc={meta.get('test_acc',0)*100:.1f}%  "
                  f"val_loss={meta.get('val_loss','?')}")
    print(f"\n  Models saved → ml/models/nse/")
    print(f"  Predictor active immediately — /ml_scan uses new weights")
