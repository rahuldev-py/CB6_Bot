from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytz
_IST = pytz.timezone('Asia/Kolkata')


def evaluate_expiry_risk(expiry: str | date | None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    exp = _parse_date(expiry)
    if exp is None:
        return {"expiry_risk": "UNKNOWN", "warning": "expiry unavailable", "dte": None}
    dte = max((exp - date.today()).days, 0)
    risk = "LOW"
    warning = ""
    if dte == 0:
        risk = "HIGH"
        warning = "expiry day theta risk"
    elif dte == 1:
        risk = "MEDIUM"
        warning = "1-DTE theta risk"
    if dte == 0 and datetime.now(_IST).hour >= 14:
        risk = "HIGH"
        warning = "late-day expiry caution"
    return {"expiry_risk": risk, "warning": warning, "dte": dte}


def _parse_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            pass
    return None
