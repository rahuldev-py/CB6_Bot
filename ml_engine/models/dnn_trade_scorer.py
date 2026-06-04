"""
ml_engine/models/dnn_trade_scorer.py

Multi-task DNN for CB6 trade scoring.

Architecture:
  Input (N features)
    → Shared backbone: Linear → BatchNorm → ReLU → Dropout (×3 layers)
    → Head 1 (win_prob):    Linear → Sigmoid          [binary classification]
    → Head 2 (expected_r):  Linear                    [regression]
    → Head 3 (grade_logits): Linear → Softmax         [4-class: A+/A/B/C]

Outputs per trade:
  win_probability     — 0.0-1.0 probability of trade being a winner
  expected_r          — expected R-multiple (continuous)
  confidence_score    — 0.0-1.0 (derived from win_prob distance from 0.5)
  trade_grade         — A+ / A / B / C
  suggested_risk_mult — 0.5-1.5 (SHADOW ONLY — ML_CAN_MODIFY_RISK=false)

SHADOW MODE: model outputs are logged but never affect live trading.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("cb6.ml.dnn_trade_scorer")

GRADE_CLASSES  = ["C", "B", "A", "A+"]
GRADE_TO_IDX   = {g: i for i, g in enumerate(GRADE_CLASSES)}
IDX_TO_GRADE   = {i: g for i, g in enumerate(GRADE_CLASSES)}

# Confidence bucket thresholds (win_prob distance from 0.5)
# confidence = abs(win_prob - 0.5) * 2  →  0=uncertain, 1=certain
CONF_BUCKETS = {
    "A+": 0.60,   # win_prob ≥ 0.80 or ≤ 0.20
    "A" : 0.40,   # win_prob ≥ 0.70 or ≤ 0.30
    "B" : 0.20,   # win_prob ≥ 0.60 or ≤ 0.40
    "C" : 0.00,   # everything else
}

# Shadow-only risk multipliers per confidence bucket
SHADOW_RISK_MULT = {"A+": 1.5, "A": 1.0, "B": 0.75, "C": 0.5}


class CB6DNN(nn.Module):
    """
    Multi-task DNN backbone + 3 output heads.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dims: list[int] = (128, 64, 32),
        dropout: float = 0.3,
        n_grade_classes: int = 4,
    ):
        super().__init__()
        self.input_dim = input_dim

        # Shared backbone
        layers = []
        in_dim = input_dim
        for h_dim in hidden_dims:
            layers += [
                nn.Linear(in_dim, h_dim),
                nn.BatchNorm1d(h_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
            ]
            in_dim = h_dim
        self.backbone = nn.Sequential(*layers)

        final_dim = hidden_dims[-1]

        # Head 1: Win probability (binary)
        self.win_head = nn.Sequential(
            nn.Linear(final_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

        # Head 2: Expected R (regression)
        self.r_head = nn.Sequential(
            nn.Linear(final_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

        # Head 3: Trade grade (4-class)
        self.grade_head = nn.Sequential(
            nn.Linear(final_dim, 16),
            nn.ReLU(),
            nn.Linear(16, n_grade_classes),
        )

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        shared = self.backbone(x)
        return {
            "win_prob"    : self.win_head(shared).squeeze(-1),
            "expected_r"  : self.r_head(shared).squeeze(-1),
            "grade_logits": self.grade_head(shared),
        }


class DNNTradeScorer:
    """
    Sklearn-style wrapper around CB6DNN.
    Handles: scaler, model, prediction, save/load.
    """

    def __init__(
        self,
        input_dim: int = 63,
        hidden_dims: tuple = (128, 64, 32),
        dropout: float = 0.3,
        device: str = "cpu",
    ):
        self.input_dim   = input_dim
        self.hidden_dims = hidden_dims
        self.dropout     = dropout
        self.device      = torch.device(device)
        self.model: Optional[CB6DNN] = None
        self.scaler      = None
        self.feature_names: list[str] = []
        self.is_trained  = False
        self.metadata: dict = {}

    def _build_model(self) -> CB6DNN:
        return CB6DNN(
            input_dim=self.input_dim,
            hidden_dims=list(self.hidden_dims),
            dropout=self.dropout,
        ).to(self.device)

    def _to_tensor(self, X: np.ndarray) -> torch.Tensor:
        return torch.tensor(X, dtype=torch.float32).to(self.device)

    def fit(
        self,
        X_train: np.ndarray,
        y_win_train: np.ndarray,
        y_r_train: np.ndarray,
        X_val: np.ndarray,
        y_win_val: np.ndarray,
        y_r_val: np.ndarray,
        epochs: int = 200,
        lr: float = 1e-3,
        batch_size: int = 64,
        patience: int = 20,
        win_loss_weight: float = 1.0,
        r_loss_weight: float = 0.5,
        grade_loss_weight: float = 0.3,
    ) -> dict:
        """Train the DNN. Returns training history dict."""
        from sklearn.preprocessing import StandardScaler

        # Scale features
        self.scaler = StandardScaler()
        X_train_s = self.scaler.fit_transform(X_train)
        X_val_s   = self.scaler.transform(X_val)

        self.input_dim = X_train.shape[1]
        self.model     = self._build_model()

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=10, factor=0.5)

        bce_loss = nn.BCELoss()
        mse_loss = nn.MSELoss()
        ce_loss  = nn.CrossEntropyLoss()

        # Grade labels: derived from R-multiples (NaN R → grade 0 = C)
        def r_to_grade(r: np.ndarray) -> np.ndarray:
            r_safe = np.where(np.isnan(r), -999.0, r)
            g = np.zeros(len(r_safe), dtype=np.int64)
            g[r_safe >= 1.0] = 1   # B
            g[r_safe >= 2.0] = 2   # A
            g[r_safe >= 3.0] = 3   # A+
            return g

        # Convert to tensors
        Xt  = self._to_tensor(X_train_s)
        ywt = self._to_tensor(y_win_train.astype(np.float32))
        yrt = self._to_tensor(y_r_train.astype(np.float32))
        ygt = torch.tensor(r_to_grade(y_r_train), dtype=torch.long).to(self.device)

        Xv  = self._to_tensor(X_val_s)
        ywv = self._to_tensor(y_win_val.astype(np.float32))
        yrv = self._to_tensor(y_r_val.astype(np.float32))

        history = {"train_loss": [], "val_loss": [], "val_auc": [], "val_brier": []}
        best_val_loss  = float("inf")
        best_state     = None
        no_improve     = 0

        n = len(Xt)
        for epoch in range(epochs):
            self.model.train()
            perm = torch.randperm(n)
            epoch_loss = 0.0
            steps = 0

            for start in range(0, n, batch_size):
                idx = perm[start: start + batch_size]
                xb, ywb, yrb, ygb = Xt[idx], ywt[idx], yrt[idx], ygt[idx]

                optimizer.zero_grad()
                out = self.model(xb)

                loss_win   = bce_loss(out["win_prob"], ywb)
                # Mask NaN R-multiples so MSE only trains on labeled rows
                r_mask = ~torch.isnan(yrb)
                if r_mask.any():
                    loss_r = mse_loss(out["expected_r"][r_mask], yrb[r_mask])
                else:
                    loss_r = torch.tensor(0.0, device=self.device)
                loss_grade = ce_loss(out["grade_logits"], ygb)
                loss = (
                    win_loss_weight   * loss_win +
                    r_loss_weight     * loss_r +
                    grade_loss_weight * loss_grade
                )
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                epoch_loss += loss.item()
                steps += 1

            avg_loss = epoch_loss / max(steps, 1)

            # Validation
            self.model.eval()
            with torch.no_grad():
                val_out = self.model(Xv)
                vl_win  = bce_loss(val_out["win_prob"], ywv)
                rv_mask = ~torch.isnan(yrv)
                if rv_mask.any():
                    vl_r = mse_loss(val_out["expected_r"][rv_mask], yrv[rv_mask])
                else:
                    vl_r = torch.tensor(0.0, device=self.device)
                val_loss = (win_loss_weight * vl_win + r_loss_weight * vl_r).item()

                wp_np = val_out["win_prob"].cpu().numpy()
                brier = float(np.mean((wp_np - y_win_val.astype(np.float32)) ** 2))

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
                logger.info(f"Early stopping at epoch {epoch + 1} (no improvement for {patience} epochs)")
                break

            if (epoch + 1) % 20 == 0:
                logger.info(f"Epoch {epoch+1:3d} | train={avg_loss:.4f} | val={val_loss:.4f} | brier={brier:.4f}")

        if best_state:
            self.model.load_state_dict(best_state)

        self.is_trained = True
        history["best_val_loss"] = round(best_val_loss, 4)
        return history

    def predict(self, X: np.ndarray) -> dict:
        """
        Run inference. Returns dict of outputs per row.
        Safe to call — returns neutral output on any error.
        """
        if not self.is_trained or self.model is None:
            return self._neutral_output(len(X))

        try:
            self.model.eval()
            X_s = self.scaler.transform(X)
            xt  = self._to_tensor(X_s)
            with torch.no_grad():
                out = self.model(xt)

            win_prob   = out["win_prob"].cpu().numpy()
            expected_r = out["expected_r"].cpu().numpy()
            grade_idx  = out["grade_logits"].argmax(dim=-1).cpu().numpy()

            # Confidence: distance from 0.5, scaled to 0-1
            confidence = np.abs(win_prob - 0.5) * 2.0

            # Confidence bucket
            conf_bucket = np.where(
                confidence >= CONF_BUCKETS["A+"], "A+",
                np.where(confidence >= CONF_BUCKETS["A"], "A",
                np.where(confidence >= CONF_BUCKETS["B"], "B", "C"))
            )

            risk_mult = np.array([SHADOW_RISK_MULT[b] for b in conf_bucket])

            return {
                "win_probability"     : np.round(win_prob, 4),
                "expected_r"          : np.round(expected_r, 3),
                "confidence_score"    : np.round(confidence, 4),
                "confidence_bucket"   : conf_bucket,
                "trade_grade"         : np.array([IDX_TO_GRADE[i] for i in grade_idx]),
                "suggested_risk_mult" : np.round(risk_mult, 2),  # SHADOW ONLY
            }
        except Exception as e:
            logger.error(f"DNN inference error: {e}")
            return self._neutral_output(len(X))

    def predict_one(self, x: np.ndarray) -> dict:
        """Predict a single sample. x must be 1D array of features."""
        result = self.predict(x.reshape(1, -1))
        return {k: v[0] for k, v in result.items()}

    @staticmethod
    def _neutral_output(n: int) -> dict:
        """Safe fallback when model unavailable."""
        return {
            "win_probability"     : np.full(n, 0.5),
            "expected_r"          : np.zeros(n),
            "confidence_score"    : np.zeros(n),
            "confidence_bucket"   : np.array(["C"] * n),
            "trade_grade"         : np.array(["C"] * n),
            "suggested_risk_mult" : np.ones(n),
        }

    def save(self, path: str | Path) -> dict:
        """Save model weights + scaler + metadata."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), path / "model_weights.pt")

        import pickle
        with open(path / "scaler.pkl", "wb") as f:
            pickle.dump(self.scaler, f)

        meta = {
            "input_dim"    : self.input_dim,
            "hidden_dims"  : list(self.hidden_dims),
            "dropout"      : self.dropout,
            "feature_names": self.feature_names,
            "is_trained"   : self.is_trained,
            **self.metadata,
        }
        with open(path / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2, default=str)

        logger.info(f"Model saved to {path}")
        return {"path": str(path), "files": ["model_weights.pt", "scaler.pkl", "metadata.json"]}

    @classmethod
    def load(cls, path: str | Path) -> "DNNTradeScorer":
        """Load a saved model."""
        path = Path(path)
        with open(path / "metadata.json") as f:
            meta = json.load(f)

        scorer = cls(
            input_dim   = meta["input_dim"],
            hidden_dims = tuple(meta["hidden_dims"]),
            dropout     = meta["dropout"],
        )
        scorer.model = scorer._build_model()
        scorer.model.load_state_dict(torch.load(path / "model_weights.pt", map_location="cpu"))
        scorer.model.eval()

        import pickle
        with open(path / "scaler.pkl", "rb") as f:
            scorer.scaler = pickle.load(f)

        scorer.feature_names = meta.get("feature_names", [])
        scorer.is_trained    = True
        scorer.metadata      = meta
        logger.info(f"Model loaded from {path}")
        return scorer
