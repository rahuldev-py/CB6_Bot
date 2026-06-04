"""
setup_mt5_terminals.py
======================
CB6 Quantum — Automated MT5 Portable Terminal Setup

Automates everything the checklist asked you to do manually:
  1. Write common.ini  (login credentials + ExpertAdvisor=1 + SavePassword)
  2. Launch each terminal in /portable mode
  3. Wait for terminal to initialise and auto-login
  4. Verify via MT5 Python API (correct login + server match)
  5. Leave terminals running — config is persisted to disk

After this script finishes:
  - All configured terminals are logged in
  - Algo Trading is ON (ExpertAdvisor=1 in config)
  - Credentials are saved (SavePassword=true)
  - Terminals stay running — python forex_main.py will connect to them

Usage (from project root):
    python setup_mt5_terminals.py

Requirements:
  - MT5 installation copied into C:\\CB6_MT5\\MT5_FTMO_10K,
    C:\\CB6_MT5\\MT5_GFT_5K, and C:\\CB6_MT5\\MT5_GFT_1K
    (see C:\\CB6_MT5\\README_SETUP.md Step 1)
  - MetaTrader5 Python package installed:  pip install MetaTrader5
"""

import os
import sys
import subprocess
import time

# Force UTF-8 output so emoji prints correctly on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── Ensure project root on path ──────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import dotenv_values
_env = dotenv_values(os.path.join(_ROOT, '.env'))

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False


# ── Account definitions ──────────────────────────────────────────────────────────

ACCOUNTS = [
    {
        'id'      : 'FTMO_10K',
        'label'   : 'FTMO Free Trial $10K',
        'dir'     : r'C:\CB6_MT5\MT5_FTMO_10K',
        'login'   : _env.get('MT5_LOGIN', ''),
        'password': _env.get('MT5_PASSWORD', ''),
        'server'  : _env.get('MT5_SERVER', ''),
        'magic'   : 62002,
    },
    {
        'id'      : 'GFT_5K',
        'label'   : 'GFT $5K 2-Step GOAT',
        'dir'     : r'C:\CB6_MT5\MT5_GFT_5K',
        'login'   : _env.get('GFT_2STEP_LOGIN', ''),
        'password': _env.get('GFT_2STEP_PASSWORD', ''),
        'server'  : _env.get('GFT_2STEP_SERVER', ''),
        'magic'   : 62001,
    },
    {
        'id'      : 'GFT_1K_INSTANT',
        'label'   : 'GFT $1K Instant',
        'dir'     : r'C:\CB6_MT5\MT5_GFT_1K',
        'login'   : _env.get('GFT_1K_MT5_LOGIN', ''),
        'password': _env.get('GFT_1K_MT5_PASSWORD', ''),
        'server'  : _env.get('GFT_1K_MT5_SERVER', ''),
        'magic'   : 100061,
    },
]

# Seconds to wait for terminal to connect after launch
_TERMINAL_BOOT_SECS = 15


# ── Config writer ────────────────────────────────────────────────────────────────

def write_common_ini(config_dir: str, login: str, password: str, server: str) -> str:
    """
    Write MT5 common.ini with:
      - Account credentials (auto-login on startup)
      - SavePassword=true  (persists across restarts)
      - ExpertAdvisor=1    (Algo Trading ON globally)
      - AllowLiveTrading=1 (Tools > Options > Expert Advisors checkbox)

    MT5 reads this file on startup before any UI interaction.
    Writing it here replaces all 6 manual clicks per terminal.
    """
    os.makedirs(config_dir, exist_ok=True)

    content = (
        "[Common]\n"
        f"Login={login}\n"
        f"Password={password}\n"
        f"Server={server}\n"
        "SavePassword=true\n"
        "ExpertAdvisor=1\n"          # Global Algo Trading toggle → GREEN
        "\n"
        "[Experts]\n"
        "AllowLiveTrading=1\n"       # Tools > Options > Expert Advisors > Allow Automated Trading
        "AllowDllImport=1\n"
        "Enabled=1\n"
        "\n"
        "[StartUp]\n"
        "AutoLogin=1\n"
    )

    path = os.path.join(config_dir, 'common.ini')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


# ── Terminal setup ────────────────────────────────────────────────────────────────

def setup_terminal(account: dict) -> bool:
    """
    Full setup for one MT5 portable terminal:
      1. Pre-write common.ini (credentials + algo trading)
      2. Launch terminal64.exe /portable
      3. Wait for auto-login
      4. Verify via MT5 Python API
    Returns True on success.
    """
    name         = account['id']
    label        = account['label']
    terminal_dir = account['dir']
    terminal_exe = os.path.join(terminal_dir, 'terminal64.exe')
    config_dir   = os.path.join(terminal_dir, 'config')
    login        = account['login']
    password     = account['password']
    server       = account['server']

    _sep()
    _log(f"  {label}")
    _log(f"  Terminal : {terminal_exe}")
    _log(f"  Login    : {login}   Server: {server}")
    _sep()

    # ── Guard: terminal file must exist ─────────────────────────────────────────
    if not os.path.isfile(terminal_exe):
        _err(f"terminal64.exe NOT FOUND at {terminal_exe}")
        _err("Copy your MT5 installation first:")
        _err(f'  Copy-Item "C:\\Program Files\\MetaTrader 5\\*" "{terminal_dir}\\" -Recurse -Force')
        return False

    if not login or not password or not server:
        _err(f"Missing credentials for {name} — check .env")
        return False

    # ── Step 1: Write config BEFORE launch ───────────────────────────────────────
    ini_path = write_common_ini(config_dir, login, password, server)
    _ok(f"Config written → {ini_path}")
    _ok("  ExpertAdvisor=1     (Algo Trading ON)")
    _ok("  AllowLiveTrading=1  (Allow Automated Trading)")
    _ok(f"  SavePassword=true   (credentials persisted)")

    # ── Step 2: Launch terminal in portable mode ─────────────────────────────────
    _log(f"\n  Launching terminal /portable ...")
    try:
        proc = subprocess.Popen(
            [terminal_exe, '/portable'],
            cwd=terminal_dir,
        )
        _ok(f"Terminal launched — PID {proc.pid}")
    except Exception as e:
        _err(f"Launch failed: {e}")
        return False

    # ── Step 3: Wait for terminal to boot and auto-login ────────────────────────
    _log(f"\n  Waiting {_TERMINAL_BOOT_SECS}s for terminal to boot and connect...")
    for i in range(_TERMINAL_BOOT_SECS, 0, -1):
        print(f"\r  [{i:>2}s remaining] ", end='', flush=True)
        time.sleep(1)
    print()

    # ── Step 4: Verify via MT5 Python API ────────────────────────────────────────
    if not _MT5_AVAILABLE:
        _warn("MetaTrader5 package not installed — skipping API verification")
        _warn("Install: pip install MetaTrader5")
        _ok("Config written and terminal launched — setup complete")
        return True

    _log("\n  Verifying connection via MT5 Python API ...")
    try:
        ok = mt5.initialize(
            path     = terminal_exe,
            login    = int(login),
            password = password,
            server   = server,
        )

        if not ok:
            err_code = mt5.last_error()
            _err(f"mt5.initialize() failed: {err_code}")
            _warn("Terminal may need more time — try running the script again in 30s")
            return False

        info = mt5.account_info()
        if not info:
            _err("mt5.account_info() returned None after connect")
            mt5.shutdown()
            return False

        # ── Critical: verify we connected to the RIGHT account ──────────────────
        if info.login != int(login):
            _err(f"ACCOUNT MISMATCH — expected {login}, got {info.login}")
            _err("Wrong terminal path or credentials — check .env")
            mt5.shutdown()
            return False

        _ok(f"✅ CONNECTED  login={info.login}  balance=${info.balance:.2f}")
        _ok(f"   Server    : {info.server}")
        _ok(f"   Currency  : {info.currency}")
        _ok(f"   Leverage  : 1:{info.leverage}")

        # ── Check terminal algo trading state ────────────────────────────────────
        term_info = mt5.terminal_info()
        if term_info:
            algo_on = getattr(term_info, 'trade_allowed', None)
            if algo_on:
                _ok("   Algo Trading: ✅ ENABLED (trade_allowed=True)")
            else:
                _warn("   Algo Trading: flag not confirmed via API")
                _warn("   Config ExpertAdvisor=1 written — should apply on next restart")

        mt5.shutdown()
        return True

    except Exception as e:
        _err(f"Verification error: {e}")
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False


# ── Output helpers ────────────────────────────────────────────────────────────────

def _sep():  print("=" * 62)
def _log(m): print(m)
def _ok(m):  print(f"  ✅ {m}")
def _err(m): print(f"  ❌ {m}", file=sys.stderr)
def _warn(m):print(f"  ⚠️  {m}")


# ── Entry point ───────────────────────────────────────────────────────────────────

def main():
    _sep()
    print("  CB6 QUANTUM — AUTOMATED MT5 PORTABLE TERMINAL SETUP")
    _sep()

    # Safety: must be run from project root
    if not os.path.exists(os.path.join(_ROOT, '.env')):
        _err("Run from C:\\cb6_bot:  cd C:\\cb6_bot && python setup_mt5_terminals.py")
        sys.exit(1)

    results = []

    for i, account in enumerate(ACCOUNTS):
        ok = setup_terminal(account)
        results.append((account['id'], account['label'], ok))

        # Brief pause between accounts so MT5 library state is clean
        if i < len(ACCOUNTS) - 1:
            _log("\n  Pausing 5s before next account...")
            time.sleep(5)

    # ── Final report ─────────────────────────────────────────────────────────────
    print()
    _sep()
    print("  SETUP COMPLETE — FINAL STATUS")
    _sep()

    all_ok = True
    for acc_id, label, ok in results:
        icon   = "✅" if ok else "❌"
        status = "READY" if ok else "NEEDS ATTENTION"
        print(f"  {icon} {acc_id:<15}  {label:<28}  {status}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        _sep()
        print("  🟢 CONFIGURED TERMINALS ARE RUNNING")
        print()
        print("  Next steps:")
        print("  1. Terminal windows are open — verify Algo Trading")
        print("     button in each toolbar is GREEN ✅")
        print("  2. Close terminal windows")
        print("     (this flushes all settings to disk)")
        print("  3. Start CB6:")
        print("     python forex_main.py")
        print()
        print("  Verify isolation:")
        print("     /fx_terminals")
        print("     Expected: ONLINE ✅  ENABLED (Magic: 62002/62001/100061)")
        print("               Shared Session Collision Risk: 0.0%")
        _sep()
    else:
        _sep()
        print("  ⚠️  One or more accounts need attention — see errors above")
        print("  Most common fix: copy MT5 installation into the terminal folder:")
        print(r'  $src = "C:\Program Files\MetaTrader 5"')
        print(r'  Copy-Item "$src\*" "C:\CB6_MT5\MT5_FTMO_10K\" -Recurse -Force')
        print(r'  Copy-Item "$src\*" "C:\CB6_MT5\MT5_GFT_5K\"   -Recurse -Force')
        print(r'  Copy-Item "$src\*" "C:\CB6_MT5\MT5_GFT_1K\"   -Recurse -Force')
        _sep()


if __name__ == '__main__':
    main()
