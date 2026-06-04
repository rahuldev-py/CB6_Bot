"""
ml_engine/models/rnn_sequence_model.py

LSTM sequence model for CB6 trade scoring.

Motivation: The DNN treats each trade independently. The LSTM sees the last
SEQ_LEN trades as context — capturing win/loss streaks, drawdown momentum,
and regime shifts that single-trade features cannot encode.

Architecture:
  Input:  (batch, SEQ_LEN, N_features)
  LSTM:   2 layers, hidden=64, dropout=0.3
  Attn:   Simple dot-product self-attention over timesteps
  Head 1: win_prob  → Linear(64→1) → Sigmoid
  Head 2: exp_r     → Linear(64→1)

SHADOW MODE — outputs logged only, never affect live trading.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger("cb6.ml.rnn_sequence_model")

SEQ_LEN = 10  # look-back window (trades)


class CB6LSTM(nn.Module):
    """LSTM encoder with optional attention pooling, 2 output heads."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        n_layers: int = 2,
        dropout: float = 0.3,
        use_attention: bool = True,
    ):
        super().__init__()
        self.hidden_dim    = hidden_dim
        self.n_layers      = n_layers
        self.use_attention = use_attention

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=n_layers,
            batch_first=True,
            dropout=dropout if n_layers > 1 else 0.0,
        )

        # Attention weights over sequence timesteps
        self.attn_w = nn.Linear(hidden_dim, 1, bias=False)

        self.dropout = nn.Dropout(dropout)

        self.win_head = nn.Sequential(
            nn.Linear(hidden_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )
        self.r_head = nn.Sequential(
            nn.Linear(hidden_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        # x: (B, T, F)
        out, _ = self.lstm(x)          # (B, T, H)

        if self.use_attention:
            scores = self.attn_w(out).squeeze(-1)   # (B, T)
            weights = torch.softmax(scores, dim=-1).unsqueeze(-1)  # (B, T, 1)
            context = (out * weights).sum(dim=1)    # (B, H)
        else:
            context = out[:, -1, :]                 # last timestep only

        context = self.dropout(context)

        return {
            "win_prob"  : self.win_head(context).squeeze(-1),
            "expected_r": self.r_head(context).squeeze(-1),
        }


class RNNTradeScorer:
    """
    Sklearn-style wrapper for CB6LSTM.
    Handles sequence construction, scaling, fit, predict, save, load.

    Sequence construction: for each trade i, the input is trades [i-SEQ_LEN, i-1]
    scaled with a StandardScaler fit on training data only.
    """

    def __init__(
        self,
        input_dim: int = 37,
        hidden_dim: int = 64,
        n_layers: int = 2,
        dropout: float = 0.3,
        seq_len: int = SEQ_LEN,
        use_attention: bool = True,
        device: str = "cpu",
    ):
        self.input_dim    = input_dim
        self.hidden_dim   = hidden_dim
        self.n_layers     = n_layers
        self.dropout      = dropout
        self.seq_len      = seq_len
        self.use_attention = use_attention
        self.device       = torch.device(device)
        self.model: Optional[CB6LSTM] = None
        self.scaler       = None
        self.feature_names: list[str] = []
        self.is_trained   = False
        self.metadata: dict = {}

    def _build_model(self) -> CB6LSTM:
        return CB6LSTM(
            input_dim=self.input_dim,
            hidden_dim=self.hidden_dim,
            n_layers=self.n_layers,
            dropout=self.dropout,
            use_attention=self.use_attention,
        ).to(self.device)

    def _to_tensor(self, arr: np.ndarray, dtype=torch.float32) -> torch.Tensor:
        return torch.tensor(arr, dtype=dtype).to(self.device)

    def _build_sequences(self, X: np.ndarray) -> np.ndarray:
        """
        Build (N, SEQ_LEN, F) sequences from (N, F) feature matrix.
        Trade i gets context [i-SEQ_LEN .. i-1].
        Rows where i < SEQ_LEN are padded with zeros on the left.
        """
        N, F = X.shape
        seqs = np.zeros((N, self.seq_len, F), dtype=np.float32)
        for i in range(N):
            start = max(0, i - self.seq_len)
            chunk = X[start:i]              # up to SEQ_LEN rows of context
            if len(chunk) > 0:
                seqs[i, -len(chunk):, :] = chunk
        return seqs

    def fit(
        self,
        X_train: np.ndarray,
        y_win_train: np.ndarray,
        y_r_train: np.ndarray,
        X_val: np.ndarray,
        y_win_val: np.ndarray,
        y_r_val: np.ndarray,
        epochs: int = 150,
        lr: float = 1e-3,
        batch_size: int = 32,
        patience: int = 20,
        win_loss_weight: float = 1.0,
        r_loss_weight: float = 0.5,
    ) -> dict:
        """Train LSTM. Returns history dict."""
        from sklearn.preprocessing import StandardScaler

        self.scaler = StandardScaler()
        X_train_s = self.scaler.fit_transform(X_train)
        X_val_s   = self.scaler.transform(X_val)

        self.input_dim = X_train.shape[1]
        self.model     = self._build_model()

        # Build sequences
        Xs_tr = self._build_sequences(X_train_s)   # (N_tr, SEQ_LEN, F)
        Xs_val = self._build_sequences(X_val_s)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=8, factor=0.5)

        bce_loss = nn.BCELoss()
        mse_loss = nn.MSELoss()

        Xt  = self._to_tensor(Xs_tr)
        ywt = self._to_tensor(y_win_train.astype(np.float32))
        yrt = self._to_tensor(y_r_train.astype(np.float32))

        Xv  = self._to_tensor(Xs_val)
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
                out = self.model(xb)

                loss_win = bce_loss(out["win_prob"], ywb)
                r_mask = ~torch.isnan(yrb)
                if r_mask.any():
                    loss_r = mse_loss(out["expected_r"][r_mask], yrb[r_mask])
                else:
                    loss_r = torch.tensor(0.0, device=self.device)

                loss = win_loss_weight * loss_win + r_loss_weight * loss_r
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
                logger.info(f"Early stopping at epoch {epoch+1}")
                break

            if (epoch + 1) % 20 == 0:
                logger.info(f"Epoch {epoch+1:3d} | train={avg_loss:.4f} | val={val_loss:.4f} | brier={brier:.4f}")

        if best_state:
            self.model.load_state_dict(best_state)

        self.is_trained = True
        history["best_val_loss"] = round(best_val_loss, 4)
        return history

    def predict(self, X: np.ndarray) -> dict:
        """Run inference on (N, F) feature matrix. Returns dict of arrays."""
        if not self.is_trained or self.model is None:
            return self._neutral_output(len(X))

        try:
            self.model.eval()
            X_s   = self.scaler.transform(X)
            seqs  = self._build_sequences(X_s)
            xt    = self._to_tensor(seqs)

            with torch.no_grad():
                out = self.model(xt)

            win_prob   = out["win_prob"].cpu().numpy()
            expected_r = out["expected_r"].cpu().numpy()
            confidence = np.abs(win_prob - 0.5) * 2.0

            from ml_engine.models.dnn_trade_scorer import CONF_BUCKETS, SHADOW_RISK_MULT, IDX_TO_GRADE
            conf_bucket = np.where(
                confidence >= CONF_BUCKETS["A+"], "A+",
                np.where(confidence >= CONF_BUCKETS["A"], "A",
                np.where(confidence >= CONF_BUCKETS["B"], "B", "C"))
            )
            risk_mult = np.array([SHADOW_RISK_MULT[b] for b in conf_bucket])

            return {
                "win_probability"    : np.round(win_prob, 4),
                "expected_r"         : np.round(expected_r, 3),
                "confidence_score"   : np.round(confidence, 4),
                "confidence_bucket"  : conf_bucket,
                "suggested_risk_mult": np.round(risk_mult, 2),  # SHADOW ONLY
            }
        except Exception as e:
            logger.error(f"LSTM inference error: {e}")
            return self._neutral_output(len(X))

    def predict_one(self, x: np.ndarray, history_X: Optional[np.ndarray] = None) -> dict:
        """
        Predict a single trade. history_X: (SEQ_LEN, F) context rows (optional).
        If history_X provided, concatenates with x; otherwise pads with zeros.
        """
        if history_X is not None:
            ctx = np.vstack([history_X[-self.seq_len:], x.reshape(1, -1)])
        else:
            ctx = x.reshape(1, -1)
        result = self.predict(ctx)
        return {k: v[-1] for k, v in result.items()}

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

        import pickle
        with open(path / "scaler.pkl", "wb") as f:
            pickle.dump(self.scaler, f)

        meta = {
            "input_dim"    : self.input_dim,
            "hidden_dim"   : self.hidden_dim,
            "n_layers"     : self.n_layers,
            "dropout"      : self.dropout,
            "seq_len"      : self.seq_len,
            "use_attention": self.use_attention,
            "feature_names": self.feature_names,
            "is_trained"   : self.is_trained,
            **self.metadata,
        }
        with open(path / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2, default=str)

        logger.info(f"LSTM model saved to {path}")
        return {"path": str(path), "files": ["model_weights.pt", "scaler.pkl", "metadata.json"]}

    @classmethod
    def load(cls, path: str | Path) -> "RNNTradeScorer":
        path = Path(path)
        with open(path / "metadata.json") as f:
            meta = json.load(f)

        scorer = cls(
            input_dim    = meta["input_dim"],
            hidden_dim   = meta["hidden_dim"],
            n_layers     = meta["n_layers"],
            dropout      = meta["dropout"],
            seq_len      = meta["seq_len"],
            use_attention= meta.get("use_attention", True),
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
        logger.info(f"LSTM model loaded from {path}")
        return scorer
