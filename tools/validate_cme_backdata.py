"""Validate CB6 CME Databento historical CSV files.

Usage:
    python tools/validate_cme_backdata.py
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas package missing. Install dependencies with:")
    print("  pip install pandas")
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "cme_databento_symbols.json"
DATA_DIR = ROOT / "data" / "futures" / "historical"
MANIFEST_PATH = DATA_DIR / "manifest.json"
QUALITY_REPORT_PATH = ROOT / "reports" / "futures_backdata_quality" / "databento_quality_report.json"
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "symbol", "source", "timeframe"]


@dataclass
class ValidationReport:
    path: Path
    symbol: str
    timeframe: str
    exists: bool
    passed: bool
    rows: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""
    duplicate_timestamps: int = 0
    gap_count: int = 0
    largest_gap: str = ""
    null_ohlcv_count: int = 0
    invalid_ohlc_count: int = 0
    zero_volume_bar_count: int = 0
    manifest_row_count: int | None = None
    manifest_match: bool | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None


def load_symbols() -> list[str]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    return list(cfg.get("symbols", {}).keys())


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def manifest_lookup(manifest: dict) -> dict[tuple[str, str], dict]:
    rows = manifest.get("files", []) if isinstance(manifest, dict) else []
    lookup: dict[tuple[str, str], dict] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        timeframe = str(row.get("timeframe", ""))
        if symbol and timeframe:
            lookup[(symbol, timeframe)] = row
    return lookup


def expected_path(symbol: str, timeframe: str) -> Path:
    return DATA_DIR / f"{symbol}_{timeframe}_2021_present.csv"


def summarize_gaps(ts: pd.Series, timeframe: str) -> tuple[int, str]:
    if len(ts) < 2:
        return 0, ""
    expected_minutes = 1 if timeframe == "1m" else 3
    deltas = ts.sort_values().diff().dropna()
    # CME has regular maintenance and weekend gaps. This is a summary, not a hard failure.
    threshold = pd.Timedelta(minutes=expected_minutes * 2)
    gaps = deltas[deltas > threshold]
    if gaps.empty:
        return 0, ""
    return int(len(gaps)), str(gaps.max())


def validate_file(path: Path, symbol: str, timeframe: str, manifest_entry: dict | None = None) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return ValidationReport(path, symbol, timeframe, False, False, errors=[f"file not found: {path}"], warnings=[])

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return ValidationReport(path, symbol, timeframe, True, False, errors=[f"read failed: {exc}"], warnings=[])

    if list(df.columns) != REQUIRED_COLUMNS:
        errors.append(f"columns mismatch: expected {REQUIRED_COLUMNS}, got {list(df.columns)}")

    if df.empty:
        errors.append("file has zero rows")
        return ValidationReport(path, symbol, timeframe, True, False, rows=0, errors=errors, warnings=warnings)

    ts = pd.to_datetime(df.get("timestamp"), utc=True, errors="coerce")
    if ts.isna().any():
        errors.append(f"invalid timestamps: {int(ts.isna().sum())}")

    dup_count = int(ts.duplicated().sum())
    if dup_count:
        errors.append(f"duplicate timestamps: {dup_count}")

    null_ohlcv_count = 0
    for col in ["open", "high", "low", "close"]:
        values = pd.to_numeric(df.get(col), errors="coerce")
        if values.isna().any():
            n = int(values.isna().sum())
            null_ohlcv_count += n
            errors.append(f"{col} has null/non-numeric values: {n}")

    open_ = pd.to_numeric(df.get("open"), errors="coerce")
    high = pd.to_numeric(df.get("high"), errors="coerce")
    low = pd.to_numeric(df.get("low"), errors="coerce")
    close = pd.to_numeric(df.get("close"), errors="coerce")
    volume = pd.to_numeric(df.get("volume"), errors="coerce")

    high_bad = ((high < open_) | (high < close) | (high < low)).fillna(False)
    low_bad = ((low > open_) | (low > close) | (low > high)).fillna(False)
    invalid_ohlc_count = int((high_bad | low_bad).sum())
    if bool(high_bad.any()):
        errors.append(f"high is below open/close/low in {int(high_bad.sum())} rows")
    if bool(low_bad.any()):
        errors.append(f"low is above open/close/high in {int(low_bad.sum())} rows")
    if volume.isna().any():
        n = int(volume.isna().sum())
        null_ohlcv_count += n
        errors.append(f"volume has null/non-numeric values: {n}")
    if (volume < 0).fillna(False).any():
        errors.append(f"negative volume rows: {int((volume < 0).sum())}")
    zero_volume_bar_count = int((volume == 0).fillna(False).sum())

    if "symbol" in df.columns and not (df["symbol"].astype(str).str.upper() == symbol).all():
        errors.append("symbol column contains unexpected values")
    if "source" in df.columns and not (df["source"].astype(str).str.lower() == "databento").all():
        warnings.append("source column is not consistently 'databento'")
    if "timeframe" in df.columns and not (df["timeframe"].astype(str) == timeframe).all():
        errors.append("timeframe column contains unexpected values")

    valid_ts = ts.dropna().sort_values()
    first = valid_ts.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ") if not valid_ts.empty else ""
    last = valid_ts.iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ") if not valid_ts.empty else ""

    if not valid_ts.empty and valid_ts.iloc[0] > pd.Timestamp("2021-02-01", tz="UTC"):
        warnings.append(f"data does not start near 2021: first timestamp {first}")

    manifest_row_count = None
    manifest_match = None
    if manifest_entry:
        manifest_row_count = int(manifest_entry.get("row_count", -1))
        manifest_match = manifest_row_count == len(df)
        if not manifest_match:
            warnings.append(f"manifest row_count={manifest_row_count} but file rows={len(df)}")

    gap_count, largest_gap = summarize_gaps(valid_ts, timeframe)
    passed = not errors
    return ValidationReport(
        path=path,
        symbol=symbol,
        timeframe=timeframe,
        exists=True,
        passed=passed,
        rows=len(df),
        first_timestamp=first,
        last_timestamp=last,
        duplicate_timestamps=dup_count,
        gap_count=gap_count,
        largest_gap=largest_gap,
        null_ohlcv_count=null_ohlcv_count,
        invalid_ohlc_count=invalid_ohlc_count,
        zero_volume_bar_count=zero_volume_bar_count,
        manifest_row_count=manifest_row_count,
        manifest_match=manifest_match,
        errors=errors,
        warnings=warnings,
    )


def report_to_dict(report: ValidationReport) -> dict:
    return {
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "source": "databento",
        "file_path": str(report.path.relative_to(ROOT)) if report.path.is_absolute() else str(report.path),
        "exists": report.exists,
        "status": "PASS" if report.passed else "FAIL",
        "row_count": report.rows,
        "date_coverage": {
            "first_timestamp": report.first_timestamp,
            "last_timestamp": report.last_timestamp,
        },
        "largest_timestamp_gap": report.largest_gap,
        "gap_count": report.gap_count,
        "duplicate_timestamps": report.duplicate_timestamps,
        "null_ohlcv_count": report.null_ohlcv_count,
        "invalid_ohlc_count": report.invalid_ohlc_count,
        "zero_volume_bar_count": report.zero_volume_bar_count,
        "manifest_row_count": report.manifest_row_count,
        "manifest_match": report.manifest_match,
        "errors": report.errors or [],
        "warnings": report.warnings or [],
    }


def write_quality_report(reports: list[ValidationReport], manifest: dict) -> None:
    QUALITY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)),
        "manifest_present": bool(manifest),
        "overall_status": "PASS" if all(r.passed for r in reports) else "FAIL",
        "files": [report_to_dict(r) for r in reports],
    }
    with QUALITY_REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def print_report(reports: list[ValidationReport]) -> None:
    print("CB6 CME backdata validation")
    print("=" * 80)
    for report in reports:
        status = "PASS" if report.passed else "FAIL"
        print(
            f"{report.symbol} {report.timeframe}: {status} | rows={report.rows} | "
            f"coverage={report.first_timestamp} -> {report.last_timestamp} | "
            f"dupes={report.duplicate_timestamps} | largest_gap={report.largest_gap or 'none'}"
        )
        for err in report.errors or []:
            print(f"  ERROR: {err}")
        for warn in report.warnings or []:
            print(f"  WARNING: {warn}")
    print("=" * 80)
    if all(r.passed for r in reports):
        print("VALIDATION RESULT: PASS")
    else:
        print("VALIDATION RESULT: FAIL")
    print(f"Quality report: {QUALITY_REPORT_PATH.relative_to(ROOT)}")
    print(f"Manifest: {MANIFEST_PATH.relative_to(ROOT)} ({'present' if MANIFEST_PATH.exists() else 'missing'})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CB6 CME Databento CSV files.")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to validate")
    parser.add_argument("--timeframes", nargs="+", default=["1m", "3m"], help="Timeframes to validate")
    args = parser.parse_args()

    symbols = [s.upper() for s in (args.symbols or load_symbols())]
    manifest = load_manifest()
    lookup = manifest_lookup(manifest)
    reports = [
        validate_file(expected_path(symbol, timeframe), symbol, timeframe, lookup.get((symbol, timeframe)))
        for symbol in symbols
        for timeframe in args.timeframes
    ]
    write_quality_report(reports, manifest)
    print_report(reports)
    return 0 if all(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
