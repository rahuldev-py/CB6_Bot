"""
ml_engine/chat/dashboard_chat.py

Dashboard-safe ML chat helper.
Read-only advisory responses from ML memory files.
No execution imports, no trade placement, no risk mutation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

MEMORY_DIR = Path("ml_engine/memory")
ANSWER_MD = MEMORY_DIR / "cb6_experience_engine_answer.md"
LEARNING_SUMMARY = MEMORY_DIR / "learning_update_summary.md"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_section(md: str, title: str) -> str:
    pattern = rf"(^##\s+{re.escape(title)}.*?)(?=^##\s+|\Z)"
    m = re.search(pattern, md, flags=re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _best_effort_answer(question: str) -> str:
    q = (question or "").lower().strip()
    primary = _read(ANSWER_MD)
    fallback = _read(LEARNING_SUMMARY)
    source = primary or fallback
    if not source:
        return (
            "I don't have memory files loaded yet. Run the research pipeline first, then ask again.\n\n"
            "Safety: ML chat remains advisory only (no execution/risk changes)."
        )

    section_map = [
        (["nse"], "SECTION 2 - NSE Findings"),
        (["forex", "fx"], "SECTION 3 - Forex Findings"),
        (["long", "short", "direction"], "SECTION 4 - Long vs Short"),
        (["best setup", "best combination"], "SECTION 5 - Best Setup Combination"),
        (["worst", "destroy", "skip"], "SECTION 6 - Worst Setup Combination"),
        (["entry"], "SECTION 7 - Entry Improvements"),
        (["exit", "tp", "trailing"], "SECTION 8 - Exit Improvements"),
        (["risk", "drawdown"], "SECTION 9 - Risk Improvements"),
        (["missing data", "missing"], "SECTION 10 - Future Learning Requirements"),
        (["memory fields", "schema", "log"], "SECTION 11 - Recommended Memory Fields"),
        (["probability", "80", "85", "pf"], "SECTION 12 - Probability of Reaching Targets"),
    ]

    chosen = ""
    for keys, sec in section_map:
        if any(k in q for k in keys):
            chosen = _extract_section(source, sec)
            if chosen:
                break

    if not chosen:
        exec_sum = _extract_section(source, "SECTION 1 - Executive Summary")
        safe = _extract_section(source, "SECTION 13 - Safe Live Recommendations")
        chosen = "\n\n".join([s for s in [exec_sum, safe] if s]) or source[:2500]

    return (
        "CB6 Experience Engine (advisory only)\n"
        f"Time: {datetime.now().isoformat()}\n\n"
        f"{chosen}\n\n"
        "Safety lock: No live execution changes, no risk/SL/TP/lot modifications."
    )


def ask_ml(question: str) -> dict:
    answer = _best_effort_answer(question)
    return {
        "ok": True,
        "question": question,
        "answer": answer,
        "shadow_only": True,
        "execution_unchanged": True,
    }

