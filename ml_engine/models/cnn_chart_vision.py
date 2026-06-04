"""
ml_engine/models/cnn_chart_vision.py

CNN chart-vision model for CB6 — RESEARCH ONLY.

Concept: instead of hand-engineered features, feed raw OHLCV candles directly
as a 2-D "chart image". The CNN learns its own pattern detectors (FVG shapes,
OB bodies, CHoCH wick structures) from data rather than from rules.

Image encoding (OHLCVEncoder):
  - Take last CANDLE_WINDOW candles before entry
  - Normalise price to [0, H_PIXELS] grid
  - Paint each candle: wick as 1-pixel column, body as filled rectangle
  - Volume bar at bottom (optional channel)
  - Output: (C, H_PIXELS, CANDLE_WINDOW) float tensor  C=1 (price) or C=2 (+vol)

Architecture (CB6CNN2D):
  Input  (1, 64, 50)
  Conv2d 16 @ 3x3 → BN → ReLU → MaxPool 2x2
  Conv2d 32 @ 3x3 → BN → ReLU → MaxPool 2x2
  Conv2d 64 @ 3x3 → BN → ReLU → AdaptiveAvgPool(4,4)
  Flatten → FC(256, 64) → Dropout
  Head 1: win_prob  → Sigmoid
  Head 2: expected_r → Linear

Status: RESEARCH — not wired to shadow inference until AUC >= 0.60 on 500+ samples.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("cb6.ml.cnn_chart_vision")

CANDLE_WINDOW = 50   # candles before entry used as input
H_PIXELS      = 64   # price-axis resolution
USE_VOLUME    = True  # add volume channel
N_CHANNELS    = 2 if USE_VOLUME else 1


# ── Image encoder ─────────────────────────────────────────────────────────────

class OHLCVEncoder:
    """
    Converts a window of OHLCV candles into a 2-D image tensor.

    Output shape: (N_CHANNELS, H_PIXELS, CANDLE_WINDOW)
      Channel 0: price image  (wick + body painted on grid)
      Channel 1: volume bars  (only if USE_VOLUME=True)
    """

    def __init__(
        self,
        candle_window: int = CANDLE_WINDOW,
        h_pixels: int = H_PIXELS,
        use_volume: bool = USE_VOLUME,
    ):
        self.candle_window = candle_window
        self.h_pixels      = h_pixels
        self.use_volume    = use_volume
        self.n_channels    = 2 if use_volume else 1

    def encode(self, ohlcv: np.ndarray) -> np.ndarray:
        """
        ohlcv: (T, 5) array — columns [open, high, low, close, volume]
               T >= 1; if T < candle_window, pad with zeros on the left.

        Returns float32 array (N_CHANNELS, H_PIXELS, candle_window).
        """
        T = len(ohlcv)
        C, H, W = self.n_channels, self.h_pixels, self.candle_window

        img = np.zeros((C, H, W), dtype=np.float32)

        # Pad or trim to candle_window columns
        if T < W:
            start_col = W - T   # left-pad with zeros
        else:
            ohlcv     = ohlcv[-W:]
            start_col = 0
            T         = W

        # Price normalisation across the visible window
        # Replace NaN prices with forward/backward fill then zeros
        ohlcv = ohlcv.copy()
        for col_idx in range(4):
            col_data = ohlcv[:, col_idx]
            if np.any(np.isnan(col_data)):
                # forward fill
                mask = np.isnan(col_data)
                idx  = np.where(~mask, np.arange(len(col_data)), 0)
                np.maximum.accumulate(idx, out=idx)
                col_data = col_data[idx]
                # if still NaN (all NaN), set to 100.0 placeholder
                col_data = np.where(np.isnan(col_data), 100.0, col_data)
                ohlcv[:, col_idx] = col_data

        lo_all = ohlcv[:, 2].min()
        hi_all = ohlcv[:, 1].max()
        price_range = hi_all - lo_all
        if price_range == 0 or np.isnan(price_range):
            price_range = 1.0  # flat candles → avoid div-by-zero

        def price_to_row(p: float) -> int:
            if np.isnan(p):
                return 0
            r = int((p - lo_all) / price_range * (H - 1))
            return max(0, min(H - 1, r))

        for i, row in enumerate(ohlcv):
            col = start_col + i
            o, h, lo, c, v = float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]) if len(row) > 4 else 0.0

            r_high  = price_to_row(h)
            r_low   = price_to_row(lo)
            r_open  = price_to_row(o)
            r_close = price_to_row(c)

            # Wick: full column from low to high at 0.4 intensity
            img[0, r_low:r_high + 1, col] = 0.4

            # Body: from min(open,close) to max(open,close) at 1.0
            r_body_lo = min(r_open, r_close)
            r_body_hi = max(r_open, r_close)
            if r_body_lo == r_body_hi:
                r_body_hi = r_body_lo + 1   # doji — 1-pixel body
            img[0, r_body_lo:r_body_hi + 1, col] = 1.0

            # Volume channel (normalised per-window)
            if self.use_volume and len(row) > 4:
                img[1, :, col] = 0.0         # filled below
                vol_bar = int(v * (H - 1))   # placeholder — normalised below

        # Normalise volume channel 0→1 across the window
        if self.use_volume:
            vols = ohlcv[:, 4] if ohlcv.shape[1] > 4 else np.zeros(T)
            vol_max = vols.max()
            if vol_max > 0:
                for i, v in enumerate(vols):
                    col    = start_col + i
                    bar_h  = int((v / vol_max) * (H // 4))   # bottom 25% reserved
                    if bar_h > 0:
                        img[1, :bar_h, col] = v / vol_max

        return img

    def encode_batch(self, windows: list[np.ndarray]) -> np.ndarray:
        """Encode a list of OHLCV windows. Returns (N, C, H, W) array."""
        return np.stack([self.encode(w) for w in windows], axis=0)


# ── CNN model ─────────────────────────────────────────────────────────────────

class CB6CNN2D(nn.Module):
    """
    2-D convolutional chart-pattern recogniser.
    Input: (B, N_CHANNELS, H_PIXELS, CANDLE_WINDOW)
    """

    def __init__(
        self,
        n_channels: int = N_CHANNELS,
        h_pixels: int = H_PIXELS,
        candle_window: int = CANDLE_WINDOW,
        dropout: float = 0.4,
    ):
        super().__init__()

        self.features = nn.Sequential(
            # Block 1
            nn.Conv2d(n_channels, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(),
            nn.MaxPool2d(2),            # → (16, H/2, W/2)

            # Block 2
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2),            # → (32, H/4, W/4)

            # Block 3
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),  # → (64, 4, 4) = 1024
        )

        flat_dim = 64 * 4 * 4  # 1024

        self.classifier = nn.Sequential(
            nn.Linear(flat_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, 64),
            nn.ReLU(),
            nn.Dropout(dropout / 2),
        )

        self.win_head = nn.Sequential(nn.Linear(64, 1), nn.Sigmoid())
        self.r_head   = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        feat = self.features(x)                 # (B, 64, 4, 4)
        flat = feat.view(feat.size(0), -1)      # (B, 1024)
        z    = self.classifier(flat)            # (B, 64)
        return {
            "win_prob"  : self.win_head(z).squeeze(-1),
            "expected_r": self.r_head(z).squeeze(-1),
        }


# ── Hybrid CNN: image branch + feature branch ────────────────────────────────

class CB6CNNHybrid(nn.Module):
    """
    Optional: merge chart image features with hand-engineered features.
    CNN branch processes image; FC branch processes engineered features.
    Fusion at the embedding level before output heads.

    Use this when both OHLCV candles AND the feature pipeline are available.
    """

    def __init__(
        self,
        feat_dim: int,
        n_channels: int = N_CHANNELS,
        dropout: float = 0.4,
    ):
        super().__init__()

        self.img_branch = nn.Sequential(
            nn.Conv2d(n_channels, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
        )
        self.img_fc = nn.Sequential(nn.Linear(1024, 64), nn.ReLU(), nn.Dropout(dropout))

        self.feat_fc = nn.Sequential(
            nn.Linear(feat_dim, 64), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(64, 32), nn.ReLU(),
        )

        self.fusion = nn.Sequential(
            nn.Linear(64 + 32, 64), nn.ReLU(), nn.Dropout(dropout / 2),
        )

        self.win_head = nn.Sequential(nn.Linear(64, 1), nn.Sigmoid())
        self.r_head   = nn.Linear(64, 1)

    def forward(self, img: torch.Tensor, feat: torch.Tensor) -> dict[str, torch.Tensor]:
        img_z  = self.img_branch(img).view(img.size(0), -1)   # (B, 1024)
        img_z  = self.img_fc(img_z)                            # (B, 64)
        feat_z = self.feat_fc(feat)                            # (B, 32)
        z      = self.fusion(torch.cat([img_z, feat_z], dim=1))
        return {
            "win_prob"  : self.win_head(z).squeeze(-1),
            "expected_r": self.r_head(z).squeeze(-1),
        }


# ── Scorer wrapper ────────────────────────────────────────────────────────────

class CNNChartScorer:
    """
    Sklearn-style wrapper for CB6CNN2D.

    Input to fit/predict: list of OHLCV windows (each is np.ndarray of shape (T, 5)).
    The encoder converts each window to a (C, H, W) image internally.

    RESEARCH STATUS: not wired to live shadow inference.
    Minimum 500 labeled chart windows needed before activation gate applies.
    """

    def __init__(
        self,
        candle_window: int = CANDLE_WINDOW,
        h_pixels: int = H_PIXELS,
        use_volume: bool = USE_VOLUME,
        dropout: float = 0.4,
        device: str = "cpu",
    ):
        self.candle_window = candle_window
        self.h_pixels      = h_pixels
        self.use_volume    = use_volume
        self.dropout       = dropout
        self.device        = torch.device(device)
        self.encoder       = OHLCVEncoder(candle_window, h_pixels, use_volume)
        self.model: Optional[CB6CNN2D] = None
        self.is_trained    = False
        self.metadata: dict = {}

    def _build_model(self) -> CB6CNN2D:
        return CB6CNN2D(
            n_channels=self.encoder.n_channels,
            h_pixels=self.h_pixels,
            candle_window=self.candle_window,
            dropout=self.dropout,
        ).to(self.device)

    def _to_tensor(self, arr: np.ndarray) -> torch.Tensor:
        return torch.tensor(arr, dtype=torch.float32).to(self.device)

    def fit(
        self,
        ohlcv_windows_train: list[np.ndarray],
        y_win_train: np.ndarray,
        y_r_train: np.ndarray,
        ohlcv_windows_val: list[np.ndarray],
        y_win_val: np.ndarray,
        y_r_val: np.ndarray,
        epochs: int = 100,
        lr: float = 5e-4,
        batch_size: int = 16,
        patience: int = 15,
        win_loss_weight: float = 1.0,
        r_loss_weight: float = 0.3,
    ) -> dict:
        """
        Train CNN on chart images.
        ohlcv_windows: list of (T, 5) arrays — one per trade.
        """
        # Encode to images
        imgs_tr  = self.encoder.encode_batch(ohlcv_windows_train)   # (N, C, H, W)
        imgs_val = self.encoder.encode_batch(ohlcv_windows_val)

        self.model = self._build_model()
        optimizer  = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler  = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=6, factor=0.5)
        bce_loss   = nn.BCELoss()
        mse_loss   = nn.MSELoss()

        Xt  = self._to_tensor(imgs_tr)
        ywt = self._to_tensor(y_win_train.astype(np.float32))
        yrt = self._to_tensor(y_r_train.astype(np.float32))
        Xv  = self._to_tensor(imgs_val)
        ywv = self._to_tensor(y_win_val.astype(np.float32))
        yrv = self._to_tensor(y_r_val.astype(np.float32))

        history = {"train_loss": [], "val_loss": [], "val_brier": []}
        best_val_loss = float("inf")
        best_state    = None
        no_improve    = 0
        n = len(Xt)

        for epoch in range(epochs):
            self.model.train()
            perm = torch.randperm(n)
            epoch_loss = 0.0
            steps = 0

            for start in range(0, n, batch_size):
                idx = perm[start: start + batch_size]
                xb, ywb, yrb = Xt[idx], ywt[idx], yrt[idx]

                optimizer.zero_grad()
                out      = self.model(xb)
                loss_win = bce_loss(out["win_prob"], ywb)
                r_mask   = ~torch.isnan(yrb)
                loss_r   = mse_loss(out["expected_r"][r_mask], yrb[r_mask]) if r_mask.any() else torch.tensor(0.0, device=self.device)
                loss     = win_loss_weight * loss_win + r_loss_weight * loss_r
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                epoch_loss += loss.item()
                steps += 1

            avg_loss = epoch_loss / max(steps, 1)

            self.model.eval()
            with torch.no_grad():
                val_out  = self.model(Xv)
                vl_win   = bce_loss(val_out["win_prob"], ywv)
                rv_mask  = ~torch.isnan(yrv)
                vl_r     = mse_loss(val_out["expected_r"][rv_mask], yrv[rv_mask]) if rv_mask.any() else torch.tensor(0.0, device=self.device)
                val_loss = (win_loss_weight * vl_win + r_loss_weight * vl_r).item()
                wp_np    = val_out["win_prob"].cpu().numpy()
                brier    = float(np.mean((wp_np - y_win_val.astype(np.float32)) ** 2))

            scheduler.step(val_loss)
            history["train_loss"].append(round(avg_loss, 4))
            history["val_loss"].append(round(val_loss, 4))
            history["val_brier"].append(round(brier, 4))

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state    = {k: v.clone() for k, v in self.model.state_dict().items()}
                no_improve    = 0
            else:
                no_improve += 1

            if no_improve >= patience:
                logger.info(f"Early stopping at epoch {epoch+1}")
                break

            if (epoch + 1) % 20 == 0:
                logger.info(f"Epoch {epoch+1:3d} | train={avg_loss:.4f} | val={val_loss:.4f} | brier={brier:.4f}")

        if best_state:
            self.model.load_state_dict(best_state)
        self.is_trained = True
        history["best_val_loss"] = round(best_val_loss, 4)
        return history

    def predict(self, ohlcv_windows: list[np.ndarray]) -> dict:
        """Run inference on a list of OHLCV windows."""
        if not self.is_trained or self.model is None:
            return self._neutral_output(len(ohlcv_windows))

        try:
            self.model.eval()
            imgs = self.encoder.encode_batch(ohlcv_windows)
            xt   = self._to_tensor(imgs)
            with torch.no_grad():
                out = self.model(xt)

            win_prob   = out["win_prob"].cpu().numpy()
            expected_r = out["expected_r"].cpu().numpy()
            confidence = np.abs(win_prob - 0.5) * 2.0

            from ml_engine.models.dnn_trade_scorer import CONF_BUCKETS, SHADOW_RISK_MULT
            conf_bucket = np.where(
                confidence >= CONF_BUCKETS["A+"], "A+",
                np.where(confidence >= CONF_BUCKETS["A"], "A",
                np.where(confidence >= CONF_BUCKETS["B"], "B", "C"))
            )
            return {
                "win_probability"  : np.round(win_prob, 4),
                "expected_r"       : np.round(expected_r, 3),
                "confidence_score" : np.round(confidence, 4),
                "confidence_bucket": conf_bucket,
                "suggested_risk_mult": np.array([SHADOW_RISK_MULT[b] for b in conf_bucket]),
            }
        except Exception as e:
            logger.error(f"CNN inference error: {e}")
            return self._neutral_output(len(ohlcv_windows))

    @staticmethod
    def _neutral_output(n: int) -> dict:
        return {
            "win_probability"    : np.full(n, 0.5),
            "expected_r"         : np.zeros(n),
            "confidence_score"   : np.zeros(n),
            "confidence_bucket"  : np.array(["C"] * n),
            "suggested_risk_mult": np.ones(n),
        }

    def save(self, path: str | Path) -> dict:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.model.state_dict(), path / "model_weights.pt")
        meta = {
            "candle_window": self.candle_window,
            "h_pixels"     : self.h_pixels,
            "use_volume"   : self.use_volume,
            "dropout"      : self.dropout,
            "is_trained"   : self.is_trained,
            **self.metadata,
        }
        with open(path / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2, default=str)
        logger.info(f"CNN model saved to {path}")
        return {"path": str(path)}

    @classmethod
    def load(cls, path: str | Path) -> "CNNChartScorer":
        path = Path(path)
        with open(path / "metadata.json") as f:
            meta = json.load(f)
        scorer = cls(
            candle_window=meta["candle_window"],
            h_pixels=meta["h_pixels"],
            use_volume=meta["use_volume"],
            dropout=meta["dropout"],
        )
        scorer.model = scorer._build_model()
        scorer.model.load_state_dict(torch.load(path / "model_weights.pt", map_location="cpu"))
        scorer.model.eval()
        scorer.is_trained = True
        scorer.metadata   = meta
        return scorer
