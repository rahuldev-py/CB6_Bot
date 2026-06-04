"""Download CME futures backdata from Databento for CB6 research.

This is intentionally isolated from live execution, NSE/Fyers, and Forex code.

Usage:
    python tools/download_cme_databento.py --start 2021-01-01 --end today --symbols MES MNQ MGC CL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

try:
    import pandas as pd
except ImportError:
    print("Databento package missing. Install dependencies with:")
    print("  pip install databento pandas")
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "cme_databento_symbols.json"
OUTPUT_DIR = ROOT / "data" / "futures" / "historical"
LOG_PATH = ROOT / "logs" / "cme_databento_download.log"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "symbol", "source", "timeframe"]
DOWNLOADER_VERSION = "2026-06-01.2"


@dataclass
class DownloadSummary:
    symbol: str
    ok: bool
    one_min_path: str = ""
    three_min_path: str = ""
    rows_1m: int = 0
    rows_3m: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""
    duplicates_removed_1m: int = 0
    duplicates_removed_3m: int = 0
    error: str = ""


def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cb6.cme_databento")
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


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_end(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.now(timezone.utc).date().isoformat()
    return value


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_span_days(start: str, end: str) -> int:
    return (parse_date(end) - parse_date(start)).days


def iter_chunks(start: str, end: str, chunk_days: int) -> Iterable[tuple[str, str]]:
    start_date = parse_date(start)
    end_date = parse_date(end)
    cursor = start_date
    while cursor < end_date:
        nxt = min(cursor + timedelta(days=chunk_days), end_date)
        yield cursor.isoformat(), nxt.isoformat()
        cursor = nxt


def ensure_dependencies():
    try:
        import databento as db  # noqa: F401
    except ImportError:
        print("Databento package missing. Install dependencies with:")
        print("  pip install databento pandas")
        raise SystemExit(2)


def get_api_key() -> str:
    key = os.getenv("DATABENTO_API_KEY", "").strip()
    if not key:
        print("DATABENTO_API_KEY is missing.")
        print("Set it first, for example in PowerShell:")
        print("  $env:DATABENTO_API_KEY='db-...your-key...'")
        raise SystemExit(2)
    return key


def normalize_databento_df(df: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index()

    # Databento OHLCV frames commonly use ts_event as the datetime index/name.
    lower_cols = {str(c).lower(): c for c in out.columns}
    ts_col = None
    for candidate in ("ts_event", "timestamp", "time", "datetime", "index"):
        if candidate in lower_cols:
            ts_col = lower_cols[candidate]
            break
    if ts_col is None:
        first_col = out.columns[0]
        if pd.api.types.is_datetime64_any_dtype(out[first_col]):
            ts_col = first_col
    if ts_col is None:
        raise ValueError(f"Cannot locate timestamp column in Databento frame columns={list(out.columns)}")

    rename_map = {}
    for col in out.columns:
        low = str(col).lower()
        if low in {"open", "high", "low", "close", "volume"}:
            rename_map[col] = low
    out = out.rename(columns=rename_map)

    missing = [c for c in ["open", "high", "low", "close", "volume"] if c not in out.columns]
    if missing:
        raise ValueError(f"Databento frame missing OHLCV columns: {missing}; columns={list(out.columns)}")

    ts = pd.to_datetime(out[ts_col], utc=True, errors="coerce")
    clean = pd.DataFrame(
        {
            "timestamp": ts,
            "open": pd.to_numeric(out["open"], errors="coerce"),
            "high": pd.to_numeric(out["high"], errors="coerce"),
            "low": pd.to_numeric(out["low"], errors="coerce"),
            "close": pd.to_numeric(out["close"], errors="coerce"),
            "volume": pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64"),
            "symbol": symbol.upper(),
            "source": "databento",
            "timeframe": timeframe,
        }
    )
    clean = clean.dropna(subset=["timestamp", "open", "high", "low", "close"])
    clean = clean.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    clean["timestamp"] = clean["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return clean[REQUIRED_COLUMNS]


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, columns=REQUIRED_COLUMNS)


def append_chunks_to_file(chunks: list[pd.DataFrame], path: Path) -> tuple[pd.DataFrame, int]:
    if not chunks:
        empty = pd.DataFrame(columns=REQUIRED_COLUMNS)
        write_csv(empty, path)
        return empty, 0
    df = pd.concat(chunks, ignore_index=True)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp_dt"])
    before = len(df)
    df = df.sort_values("timestamp_dt").drop_duplicates(subset=["timestamp_dt"], keep="last")
    duplicates_removed = before - len(df)
    df = df.drop(columns=["timestamp_dt"])
    write_csv(df, path)
    return df, duplicates_removed


def resample_to_3m(one_min: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if one_min.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    df = one_min.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    agg = df.resample("3min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"]).reset_index()
    agg["symbol"] = symbol.upper()
    agg["source"] = "databento"
    agg["timeframe"] = "3m"
    agg["volume"] = agg["volume"].fillna(0).astype("int64")
    agg["timestamp"] = pd.to_datetime(agg["timestamp"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return agg[REQUIRED_COLUMNS]


def count_duplicate_timestamps(df: pd.DataFrame) -> int:
    if df.empty or "timestamp" not in df.columns:
        return 0
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return int(ts.duplicated().sum())


def fetch_symbol(client, cfg: dict, symbol: str, start: str, end: str, chunk_days: int, logger: logging.Logger) -> DownloadSummary:
    sym_cfg = cfg["symbols"][symbol]
    dataset = cfg.get("dataset", "GLBX.MDP3")
    schema = cfg.get("schema", "ohlcv-1m")
    continuous_symbol = sym_cfg["continuous_symbol"]
    one_min_path = OUTPUT_DIR / f"{symbol}_1m_2021_present.csv"
    three_min_path = OUTPUT_DIR / f"{symbol}_3m_2021_present.csv"

    chunks: list[pd.DataFrame] = []
    try:
        for chunk_start, chunk_end in iter_chunks(start, end, chunk_days):
            logger.info("%s: requesting %s %s -> %s", symbol, continuous_symbol, chunk_start, chunk_end)
            data = client.timeseries.get_range(
                dataset=dataset,
                schema=schema,
                symbols=continuous_symbol,
                stype_in=sym_cfg.get("stype_in", "continuous"),
                start=chunk_start,
                end=chunk_end,
            )
            chunk_df = normalize_databento_df(data.to_df(), symbol, "1m")
            logger.info("%s: received %d rows for %s -> %s", symbol, len(chunk_df), chunk_start, chunk_end)
            if not chunk_df.empty:
                chunks.append(chunk_df)
    except Exception as exc:
        logger.exception("%s: continuous download failed", symbol)
        explicit = sym_cfg.get("explicit_contracts") or []
        if explicit:
            return DownloadSummary(symbol=symbol, ok=False, error=(
                f"Continuous symbol {continuous_symbol} failed: {exc}. "
                "Explicit contract fallback is configured but not stitched automatically yet; "
                f"contracts={explicit}."
            ))
        return DownloadSummary(symbol=symbol, ok=False, error=(
            f"Continuous symbol {continuous_symbol} failed: {exc}. "
            "No explicit contract fallback configured in config/cme_databento_symbols.json."
        ))

    one_min, duplicates_removed_1m = append_chunks_to_file(chunks, one_min_path)
    three_min = resample_to_3m(one_min, symbol)
    duplicates_removed_3m = count_duplicate_timestamps(three_min)
    if duplicates_removed_3m:
        three_min["timestamp_dt"] = pd.to_datetime(three_min["timestamp"], utc=True, errors="coerce")
        three_min = three_min.sort_values("timestamp_dt").drop_duplicates(subset=["timestamp_dt"], keep="last")
        three_min = three_min.drop(columns=["timestamp_dt"])
    write_csv(three_min, three_min_path)

    first_ts = one_min["timestamp"].iloc[0] if not one_min.empty else ""
    last_ts = one_min["timestamp"].iloc[-1] if not one_min.empty else ""
    return DownloadSummary(
        symbol=symbol,
        ok=True,
        one_min_path=str(one_min_path.relative_to(ROOT)),
        three_min_path=str(three_min_path.relative_to(ROOT)),
        rows_1m=len(one_min),
        rows_3m=len(three_min),
        first_timestamp=first_ts,
        last_timestamp=last_ts,
        duplicates_removed_1m=duplicates_removed_1m,
        duplicates_removed_3m=duplicates_removed_3m,
    )


def manifest_entry(symbol: str, timeframe: str, path: str, row_count: int, first_ts: str, last_ts: str, duplicate_count_removed: int) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": "databento",
        "file_path": path,
        "row_count": row_count,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "duplicate_count_removed": duplicate_count_removed,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "downloader_version": DOWNLOADER_VERSION,
    }


def write_manifest(summaries: list[DownloadSummary]) -> None:
    entries: list[dict] = []
    for item in summaries:
        if not item.ok:
            continue
        entries.append(manifest_entry(
            item.symbol, "1m", item.one_min_path, item.rows_1m,
            item.first_timestamp, item.last_timestamp, item.duplicates_removed_1m,
        ))
        entries.append(manifest_entry(
            item.symbol, "3m", item.three_min_path, item.rows_3m,
            item.first_timestamp, item.last_timestamp, item.duplicates_removed_3m,
        ))
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "downloader_version": DOWNLOADER_VERSION,
                   "files": entries}, f, indent=2)


def planned_file_rows(symbols: list[str]) -> list[dict]:
    rows = []
    for symbol in symbols:
        rows.append({"symbol": symbol, "timeframe": "1m", "file": str((OUTPUT_DIR / f"{symbol}_1m_2021_present.csv").relative_to(ROOT))})
        rows.append({"symbol": symbol, "timeframe": "3m", "file": str((OUTPUT_DIR / f"{symbol}_3m_2021_present.csv").relative_to(ROOT))})
    return rows


def print_plan(symbols: list[str], start: str, end: str, chunk_days: int, dry_run: bool, test_mode: bool) -> None:
    print("CME Databento download plan")
    print("=" * 80)
    print(f"mode: {'test-mode' if test_mode else 'normal'}{' dry-run' if dry_run else ''}")
    print(f"date range: {start} -> {end} ({date_span_days(start, end)} days)")
    print(f"chunk days: {chunk_days}")
    print(f"symbols: {', '.join(symbols)}")
    print("files:")
    for row in planned_file_rows(symbols):
        print(f"  {row['symbol']} {row['timeframe']}: {row['file']}")
    print("=" * 80)


def print_command_examples() -> None:
    print("\nCommand examples:")
    print("  Safe test:")
    print("    python tools/download_cme_databento.py --test-mode")
    print("  Dry run:")
    print("    python tools/download_cme_databento.py --start 2021-01-01 --end today --symbols MES MNQ MGC CL --dry-run")
    print("  Full:")
    print("    python tools/download_cme_databento.py --start 2021-01-01 --end today --symbols MES MNQ MGC CL")
    print("  Validate:")
    print("    python tools/validate_cme_backdata.py")


def print_summary(summaries: list[DownloadSummary]) -> None:
    print("\nCME Databento download summary")
    print("=" * 80)
    for item in summaries:
        if item.ok:
            print(f"{item.symbol}: OK")
            print(f"  1m: {item.one_min_path} rows={item.rows_1m}")
            print(f"  3m: {item.three_min_path} rows={item.rows_3m}")
            print(f"  coverage: {item.first_timestamp} -> {item.last_timestamp}")
            print(f"  duplicates removed: 1m={item.duplicates_removed_1m} 3m={item.duplicates_removed_3m}")
        else:
            print(f"{item.symbol}: FAILED")
            print(f"  error: {item.error}")
    print("\nValidation:")
    print("  python tools/validate_cme_backdata.py")
    print("\nNext CB6 futures backtest command:")
    print("  python futures_main.py --mode backtest --symbols MES,MNQ,MGC,CL")
    print(f"\nManifest: {MANIFEST_PATH.relative_to(ROOT)}")
    print_command_examples()


def run_validation(symbols: list[str]) -> bool:
    try:
        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from validate_cme_backdata import expected_path, print_report, validate_file
    except Exception as exc:
        print(f"\nValidation result: SKIPPED ({exc})")
        return False

    reports = [
        validate_file(expected_path(symbol, timeframe), symbol, timeframe)
        for symbol in symbols
        for timeframe in ("1m", "3m")
    ]
    print()
    print_report(reports)
    ok = all(r.passed for r in reports)
    print(f"Validation result: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Download CME futures 1m/3m Databento backdata for CB6.")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="today", help="End date YYYY-MM-DD or 'today'")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to download")
    parser.add_argument("--chunk-days", type=int, default=31, help="Databento request chunk size in calendar days")
    parser.add_argument("--test-mode", action="store_true", help="Safe test: MES only for 7 days unless overridden")
    parser.add_argument("--max-days", type=int, default=None, help="Abort if requested date span exceeds this many days")
    parser.add_argument("--dry-run", action="store_true", help="Print planned downloads without calling Databento")
    args = parser.parse_args()

    logger = setup_logging()
    cfg = load_config()
    end = parse_end(args.end)
    if args.test_mode:
        symbols = [s.upper() for s in (args.symbols or ["MES"])]
        if args.start is None:
            end_date = parse_date(end)
            start = (end_date - timedelta(days=7)).isoformat()
        else:
            start = args.start
    else:
        symbols = [s.upper() for s in (args.symbols or ["MES", "MNQ", "MGC", "CL"])]
        start = args.start or "2021-01-01"

    bad = [s for s in symbols if s not in cfg.get("symbols", {})]
    if bad:
        print(f"Unknown symbols in config: {bad}")
        return 2

    span = date_span_days(start, end)
    if span <= 0:
        print(f"Invalid date range: start={start} end={end}")
        return 2
    if args.max_days is not None and span > args.max_days:
        print(f"Requested date span is {span} days, exceeding --max-days {args.max_days}.")
        print("Use --dry-run to inspect the plan or raise --max-days intentionally.")
        print_command_examples()
        return 2

    print_plan(symbols, start, end, args.chunk_days, args.dry_run, args.test_mode)
    if args.dry_run:
        print("DRY RUN: no Databento calls made and no files written.")
        print_command_examples()
        return 0

    ensure_dependencies()
    key = get_api_key()

    import databento as db

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = db.Historical(key)

    summaries: list[DownloadSummary] = []
    for symbol in symbols:
        summary = fetch_symbol(client, cfg, symbol, start, end, args.chunk_days, logger)
        summaries.append(summary)

    if any(s.ok for s in summaries):
        write_manifest(summaries)
    print_summary(summaries)
    if any(s.ok for s in summaries):
        run_validation([s.symbol for s in summaries if s.ok])
    return 0 if all(s.ok for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
