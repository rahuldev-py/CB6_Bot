import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(
    LOG_DIR,
    f"cb6_{datetime.now().strftime('%Y%m%d')}.log"
)

# Fix Windows emoji encoding issue
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


class _WindowsSafeRotatingHandler(TimedRotatingFileHandler):
    """
    TimedRotatingFileHandler with Windows-safe rollover.

    On Windows, os.rename() fails with PermissionError (WinError 32) when any
    thread still holds the log file open.  The fix:
      1. Close our own stream before renaming (releases our handle).
      2. Retry the rename up to 5 times with a short sleep (other threads may
         still be writing and will release their handle momentarily).
      3. If rename still fails after retries, skip rotation silently and keep
         writing to the same file — logging continues uninterrupted.
    """

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        try:
            super().doRollover()
        except PermissionError:
            # Retry a few times — other threads may hold a brief write lock
            for _ in range(5):
                time.sleep(0.2)
                try:
                    super().doRollover()
                    return
                except PermissionError:
                    continue
            # All retries exhausted — reopen original file and keep logging
            if self.stream is None:
                self.stream = self._open()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        _WindowsSafeRotatingHandler(
            log_file,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8',
        ),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("CB6Bot")
