import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime


# ── Low-level cross-process file lock ─────────────────────────────────────────

def _acquire_flock(fh, timeout: float):
    """Acquire exclusive OS-level lock on open file handle `fh`."""
    start = time.time()
    while True:
        try:
            if os.name == 'nt':
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.time() - start >= timeout:
                raise TimeoutError(
                    f"state_lock: could not acquire lock within {timeout}s"
                )
            time.sleep(0.05)


def _release_flock(fh):
    """Release OS-level lock on open file handle `fh`."""
    try:
        if os.name == 'nt':
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


@contextmanager
def file_lock(path: str, timeout: float = 10.0):
    """Cross-process advisory lock using a sidecar .lock file."""
    lock_path = path + '.lock'
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(lock_path, 'a+')
    try:
        _acquire_flock(fh, timeout)
        yield
    finally:
        _release_flock(fh)
        fh.close()


# ── Atomic read-modify-write context manager ───────────────────────────────────

@contextmanager
def state_lock(file_path: str, default: dict = None, timeout: float = 10.0):
    """
    Atomic read-modify-write context manager for JSON state files.

    Acquires an exclusive cross-process file lock (msvcrt on Windows,
    fcntl.flock on POSIX), reads the current JSON into a dict, yields
    that dict for in-place mutation by the caller, then writes back via
    fsync + atomic os.replace so the file is never left partially written.

    Usage::

        with state_lock(STATE_FILE, default={}) as state:
            state['counter'] += 1
            # ← written back automatically on clean exit

    On exception inside the ``with`` block the file is NOT overwritten
    (the write step is skipped), preserving the last-good data.

    Raises:
        TimeoutError  – lock not acquired within *timeout* seconds.
    """
    if default is None:
        default = {}

    lock_path = file_path + '.lock'
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    fh = open(lock_path, 'a+')

    try:
        # ── 1. Acquire exclusive lock ────────────────────────────────────────
        _acquire_flock(fh, timeout)

        # ── 2. Read current state ────────────────────────────────────────────
        data = default.copy()
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            try:
                with open(file_path, 'r', encoding='utf-8') as rf:
                    data = json.load(rf)
            except (json.JSONDecodeError, OSError, ValueError):
                # Corrupt or empty file → seed from default; the write on exit
                # will overwrite the bad data with the caller's mutations.
                data = default.copy()

        # ── 3. Yield to caller for mutations ─────────────────────────────────
        yield data

        # ── 4. Atomic write (only reached when no exception in caller) ────────
        tmp = file_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as wf:
            json.dump(data, wf, indent=2, default=str)
            wf.flush()
            os.fsync(wf.fileno())       # flush OS write buffers to disk
        os.replace(tmp, file_path)      # atomic rename — never leaves partial file

    finally:
        # ── 5. Always release lock ────────────────────────────────────────────
        _release_flock(fh)
        fh.close()


# ── Convenience helpers (unchanged callers still work) ─────────────────────────

def load_json_locked(path: str, default: dict) -> dict:
    with file_lock(path):
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(default, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return default.copy()
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)


def save_json_locked(path: str, data: dict):
    with file_lock(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())        # flush OS write buffers to disk
        os.replace(tmp, path)


def read_state(path: str, default: dict = None) -> dict:
    """Read a JSON state file (thread-safe). Returns copy of default if absent."""
    return load_json_locked(path, default if default is not None else {})


def backup_json_dir(data_dir: str, backup_root: str = None) -> str:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = backup_root or os.path.join(data_dir, 'backups')
    out_dir = os.path.join(backup_root, stamp)
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(data_dir):
        if name.endswith('.json'):
            src = os.path.join(data_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(out_dir, name))
    return out_dir
