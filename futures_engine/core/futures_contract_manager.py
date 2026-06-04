"""
CB6 Futures Core — Contract Manager
Handles front-month determination, expiry calendar, rollover logic,
and continuous contract stitching for backtesting.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timedelta
from typing import Optional

# Quarterly expiry months for equity index + metal futures
QUARTERLY_MONTHS = [3, 6, 9, 12]  # Mar, Jun, Sep, Dec

# Monthly expiry months for energy (CL, MCL expire every month)
MONTHLY_MONTHS = list(range(1, 13))

MONTHLY_SYMBOLS = {"CL", "MCL", "GC", "MGC", "SI", "SIL", "ZN", "ZB"}

ROLLOVER_DAYS_BEFORE_EXPIRY = 5  # Roll n business days before first notice / expiry


def _third_friday(year: int, month: int) -> date:
    """Return the third Friday of the given month (CME equity index expiry)."""
    d = date(year, month, 1)
    fridays = [d + timedelta(days=i) for i in range(31) if
               (d + timedelta(days=i)).month == month and
               (d + timedelta(days=i)).weekday() == 4]
    return fridays[2]


def _last_business_day_prior_month(year: int, month: int) -> date:
    """Last business day of the month prior to expiry month (energy/metals pattern)."""
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    last = date(prev_year, prev_month, 28)
    # walk forward to find last calendar day
    while True:
        nxt = last + timedelta(days=1)
        if nxt.month != prev_month:
            break
        last = nxt
    # step back to business day
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    return last


def expiry_date(symbol: str, year: int, month: int) -> date:
    sym = symbol.upper()
    if sym in {"ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM"}:
        return _third_friday(year, month)
    if sym in {"GC", "MGC", "SI", "SIL"}:
        return _last_business_day_prior_month(year, month)
    if sym in {"CL", "MCL"}:
        # CL expires 3rd business day before the 25th of the month prior to delivery
        delivery_year, delivery_month = year, month
        prior_month = delivery_month - 1 if delivery_month > 1 else 12
        prior_year = delivery_year if delivery_month > 1 else delivery_year - 1
        d = date(prior_year, prior_month, 25)
        bdays = 0
        while bdays < 3:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                bdays += 1
        return d
    if sym in {"ZN", "ZB"}:
        # CBOT treasuries: last business day of month prior to delivery
        return _last_business_day_prior_month(year, month)
    raise ValueError(f"No expiry rule for symbol '{sym}'")


def front_month(symbol: str, as_of: date) -> tuple[int, int]:
    """Return (year, month) of the active front-month contract as of given date."""
    sym = symbol.upper()
    months = MONTHLY_MONTHS if sym in MONTHLY_SYMBOLS else QUARTERLY_MONTHS
    for year in [as_of.year, as_of.year + 1]:
        for month in months:
            exp = expiry_date(symbol, year, month)
            rollover = exp - timedelta(days=ROLLOVER_DAYS_BEFORE_EXPIRY * 2)
            if rollover >= as_of:
                return year, month
    raise RuntimeError(f"Could not determine front month for {symbol} as of {as_of}")


def contract_code(symbol: str, year: int, month: int) -> str:
    """Return standardised contract code, e.g. 'ESM25' for June 2025 ES."""
    month_codes = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",
                   7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}
    return f"{symbol}{month_codes[month]}{str(year)[-2:]}"


def should_rollover(symbol: str, as_of: date) -> bool:
    """True if today is within the rollover window (n days before expiry)."""
    yr, mo = front_month(symbol, as_of)
    exp = expiry_date(symbol, yr, mo)
    return (exp - as_of).days <= ROLLOVER_DAYS_BEFORE_EXPIRY


class ContractManager:
    """
    Manages contract lifecycle for one symbol.
    Tracks current front-month, detects rollovers, builds continuous series.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._state_path = os.path.join(
            "data", "futures", "contracts", f"{self.symbol}_contract.json"
        )
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self._state_path):
            with open(self._state_path, encoding="utf-8") as f:
                return json.load(f)
        return {"active_contract": None, "rollovers": []}

    def _save_state(self) -> None:
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def active_contract(self, as_of: Optional[date] = None) -> str:
        d = as_of or date.today()
        yr, mo = front_month(self.symbol, d)
        code = contract_code(self.symbol, yr, mo)
        if self._state.get("active_contract") != code:
            old = self._state.get("active_contract", "none")
            self._state["rollovers"].append({
                "from": old, "to": code, "date": d.isoformat()
            })
            self._state["active_contract"] = code
            self._save_state()
        return code

    def is_rollover_day(self, as_of: Optional[date] = None) -> bool:
        return should_rollover(self.symbol, as_of or date.today())

    def expiry(self, as_of: Optional[date] = None) -> date:
        d = as_of or date.today()
        yr, mo = front_month(self.symbol, d)
        return expiry_date(self.symbol, yr, mo)

    def rollover_history(self) -> list[dict]:
        return self._state.get("rollovers", [])
