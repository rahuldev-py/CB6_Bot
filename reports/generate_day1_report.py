"""
reports/generate_day1_report.py — CB6 Quantum Day 1 TrueData Production Report

Run this script after the morning Silver Bullet windows close (≈11:05 IST) to
compile and save CB6_TRUEDATA_LIVE_DAY_1_REPORT.md in the project root.

Usage (PowerShell):
    python reports/generate_day1_report.py

The script also wires the live_session_monitor into the TrueData tick path so
metrics accumulate automatically once the bot (main.py) is running.  If you
want to run generate_day1_report.py standalone after a live session just import
the monitor singleton — it retains all metrics recorded by the running bot
process (same Python process only; for a separate process, wire export via JSON).

If run in standalone / offline mode (no live data), the report will still
write with whatever metrics were recorded up to the call time.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.live_session_monitor import get_monitor

REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "CB6_TRUEDATA_LIVE_DAY_1_REPORT.md")


def main() -> None:
    monitor = get_monitor()
    out = monitor.generate_report(os.path.normpath(REPORT_PATH))
    print(f"[CB6] Day 1 report saved → {out}")

    # Print a brief summary to stdout for quick review
    with open(out, encoding="utf-8") as f:
        content = f.read()

    # Pull out the verdict line for a quick console summary
    for line in content.splitlines():
        if line.startswith("## **["):
            print(f"\nVERDICT: {line.strip()}")
            break
    print("\nOpen the .md file for the full report.")


if __name__ == "__main__":
    main()
