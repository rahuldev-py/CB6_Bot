# forex_engine/reports/trade_journal.py
# Re-exports the existing forex_trade_journal module under the new structure.
# Original file: forex_engine/forex_trade_journal.py (untouched).

from forex_engine.forex_trade_journal import (
    JOURNAL_CSV,
    JOURNAL_JSON,
    CSV_FIELDS,
    build_record,
    save_records,
    print_analysis,
)

__all__ = [
    'JOURNAL_CSV',
    'JOURNAL_JSON',
    'CSV_FIELDS',
    'build_record',
    'save_records',
    'print_analysis',
]
