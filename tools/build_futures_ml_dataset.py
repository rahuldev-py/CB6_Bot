"""Build an offline futures ML feature dataset from Databento 3m CSVs.

No labels are invented here. This file only creates clean features from
historical bars for later CB6 ML/research workflows.

Usage:
    python tools/build_futures_ml_dataset.py --symbols MES MNQ MGC CL --timeframe 3m --start 2021-01-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from futures_engine.research.cme_databento_backdata import load_databento_futures_csv

OUT_PATH = ROOT / "ml" / "training_data" / "futures_ml_2021_present.csv"
LOG_PATH = ROOT / "logs" / "futures_ml_dataset.log"


def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cb6.futures.ml_dataset")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    grouped = out.groupby("symbol", group_keys=False)

    out["return_1"] = grouped["close"].pct_change().fillna(0.0)
    out["range"] = out["high"] - out["low"]
    out["body"] = (out["close"] - out["open"]).abs()
    out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
    out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
    out["volume_change"] = grouped["volume"].pct_change().replace([float("inf"), -float("inf")], 0.0).fillna(0.0)
    out["rolling_volatility"] = grouped["return_1"].transform(lambda s: s.rolling(20, min_periods=5).std()).fillna(0.0)
    out["rolling_volume_mean"] = grouped["volume"].transform(lambda s: s.rolling(20, min_periods=5).mean()).fillna(out["volume"])

    ts = pd.to_datetime(out["timestamp"], utc=True)
    out["hour_utc"] = ts.dt.hour
    out["day_of_week"] = ts.dt.dayofweek
    out["is_rth_window"] = out["hour_utc"].between(13, 20).astype(int)
    out["session_bucket"] = pd.cut(
        out["hour_utc"],
        bins=[-1, 6, 12, 20, 23],
        labels=["asia", "london", "us", "late"],
    ).astype(str)
    out["timestamp"] = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def build_dataset(symbols: list[str], timeframe: str, start: str | None, end: str | None, logger: logging.Logger) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        logger.info("Loading %s %s Databento CSV", symbol, timeframe)
        frame = load_databento_futures_csv(symbol, timeframe, start=start, end=end)
        logger.info("%s %s rows=%d", symbol, timeframe, len(frame))
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    base = pd.concat(frames, ignore_index=True)
    return add_features(base)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CB6 futures ML dataset from Databento CSVs.")
    parser.add_argument("--symbols", nargs="+", default=["MES", "MNQ", "MGC", "CL"])
    parser.add_argument("--timeframe", default="3m", choices=["1m", "3m"])
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()

    logger = setup_logging()
    try:
        dataset = build_dataset([s.upper() for s in args.symbols], args.timeframe, args.start, args.end, logger)
    except Exception as exc:
        logger.error("Futures ML dataset build failed: %s", exc)
        print(f"FAILED: {exc}")
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output, index=False)
    first_ts = dataset["timestamp"].iloc[0] if not dataset.empty else ""
    last_ts = dataset["timestamp"].iloc[-1] if not dataset.empty else ""
    print("Futures ML dataset build complete")
    print(f"  output: {output}")
    print(f"  rows: {len(dataset)}")
    print(f"  coverage: {first_ts} -> {last_ts}")
    print(f"  symbols: {', '.join(sorted(dataset['symbol'].unique())) if not dataset.empty else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
