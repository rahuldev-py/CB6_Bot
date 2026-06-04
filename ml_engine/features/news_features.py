"""
ml_engine/features/news_features.py

News/sentiment features for ML training.
Currently limited to what's available in the dataset:
  - Whether trade was in 'free_trial' or 'live' mode (proxy for market conditions)
  - News blackout flag (from forex_news_monitor if available at inference time)

Full NLP news scoring is planned for a later step (FinBERT).
At training time, most rows will have NaN for live news features.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def add_news_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Mode: free_trial vs live (Forex journal has this)
    if "mode" in out.columns:
        out["mode_live"] = out["mode"].str.lower().eq("live").astype(float)
    else:
        out["mode_live"] = np.nan

    # Placeholder — filled at inference time from forex_news_monitor
    out["news_blackout"] = np.nan   # 1 = blackout active, 0 = clear
    out["news_impact"]   = np.nan   # 0.0-1.0 impact score (future NLP)

    return out


NEWS_FEATURE_COLS = [
    "mode_live", "news_blackout", "news_impact",
]
