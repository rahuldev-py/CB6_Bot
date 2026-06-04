"""
tools/audit.py — CB6 Quantum architecture audit runner

Usage:
    python tools/audit.py              # full audit
    python tools/audit.py --dead       # dead code only (vulture)
    python tools/audit.py --unused     # unused imports only (autoflake)
    python tools/audit.py --loc        # LOC and large-file report only
    python tools/audit.py --circular   # circular import grep
    python tools/audit.py --bom        # find BOM-encoded files
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {"CB6_PRE_LIVE_RESUME_20260523", ".pytest_tmp", ".venv", "__pycache__", ".git"}

# ── helpers ──────────────────────────────────────────────────────────────────

def iter_py_files():
    for p in ROOT.rglob("*.py"):
        if any(ex in p.parts for ex in EXCLUDE_DIRS):
            continue
        yield p


def run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        return (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return f"[TOOL NOT FOUND: {cmd[0]}]"


# ── checks ───────────────────────────────────────────────────────────────────

def check_loc():
    print("\n" + "="*60)
    print("LOC REPORT")
    print("="*60)
    data = []
    for p in iter_py_files():
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").count("\n")
            data.append((lines, p.relative_to(ROOT)))
        except Exception:
            pass
    data.sort(reverse=True)
    total = sum(d[0] for d in data)
    print(f"Total: {len(data)} files, {total:,} LOC\n")
    print(f"{'LOC':>6}  {'File'}")
    print("-" * 60)
    for loc, path in data[:25]:
        marker = "  ← ⚠ >1000" if loc > 1000 else ("  ← ✓ >500" if loc > 500 else "")
        print(f"{loc:>6}  {path}{marker}")
    over_1k = [(l, p) for l, p in data if l > 1000]
    print(f"\nFiles >1000 LOC: {len(over_1k)}")
    for loc, path in over_1k:
        print(f"  {loc:>5}  {path}")


def check_bom():
    print("\n" + "="*60)
    print("BOM-ENCODED FILES")
    print("="*60)
    found = []
    for p in iter_py_files():
        try:
            raw = p.read_bytes()
            if raw[:3] == b"\xef\xbb\xbf":
                found.append(p.relative_to(ROOT))
        except Exception:
            pass
    if found:
        print(f"Found {len(found)} BOM-encoded files (run tools/audit.py to strip):")
        for f in found:
            print(f"  {f}")
    else:
        print("No BOM-encoded files found. ✓")


def check_syntax():
    print("\n" + "="*60)
    print("SYNTAX ERRORS")
    print("="*60)
    errors = []
    for p in iter_py_files():
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            errors.append((p.relative_to(ROOT), e))
    if errors:
        print(f"Found {len(errors)} syntax errors:")
        for path, err in errors:
            print(f"  {path}:{err.lineno}: {err.msg}")
    else:
        print("No syntax errors. ✓")


def check_dead_code():
    print("\n" + "="*60)
    print("DEAD CODE (vulture ≥80%)")
    print("="*60)
    out = run([
        sys.executable, "-m", "vulture", ".",
        "--min-confidence", "80",
        "--exclude", "CB6_PRE_LIVE_RESUME_20260523,.pytest_tmp,.venv,tests,trial",
    ])
    print(out or "No dead code found. ✓")


def check_unused_imports():
    print("\n" + "="*60)
    print("UNUSED IMPORTS (autoflake)")
    print("="*60)
    out = run([
        sys.executable, "-m", "autoflake",
        "--check", "--remove-all-unused-imports", "--recursive",
        "--exclude", "CB6_PRE_LIVE_RESUME_20260523,.pytest_tmp,.venv",
        ".",
    ])
    print(out or "No unused imports found. ✓")


def check_circular():
    print("\n" + "="*60)
    print("CIRCULAR IMPORT RISK (grep-based)")
    print("="*60)
    # scanner importing trader/execution
    risks = [
        ("scanner → trader",   "scanner",          r"from trader|import trader"),
        ("utils  → execution", "utils",            r"open_paper_trade|place_order|live_trader"),
        ("dashboard → broker", "dashboard.py",     r"mt5_connector|binance_adapter|fyers.place_order"),
        ("backtest → live",    "backtest",         r"live_trader|from trader.order"),
        ("ml → execution",     "ml",               r"place_order|open_paper_trade|order_send"),
    ]
    found_any = False
    for label, path, pattern in risks:
        full = ROOT / path
        if not full.exists():
            continue
        files = [full] if full.is_file() else list(full.rglob("*.py"))
        for f in files:
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    import re
                    if re.search(pattern, line) and not line.strip().startswith("#"):
                        print(f"  [{label}] {f.relative_to(ROOT)}:{i}: {line.strip()}")
                        found_any = True
            except Exception:
                pass
    if not found_any:
        print("No dangerous cross-layer imports detected. ✓")


def check_complexity():
    print("\n" + "="*60)
    print("COMPLEXITY (radon — rank D/E/F functions)")
    print("="*60)
    out = run([
        sys.executable, "-m", "radon", "cc", ".",
        "--min", "D", "--show-complexity",
        "--exclude", "CB6_PRE_LIVE_RESUME_20260523,tests,trial",
    ])
    print(out or "No high-complexity functions found. ✓")


def check_unreferenced_modules():
    print("\n" + "="*60)
    print("POTENTIALLY UNREFERENCED MODULES")
    print("="*60)
    py_files = list(iter_py_files())
    module_names: dict[str, Path] = {}
    for p in py_files:
        rel = p.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
        module_names[rel] = p

    imports: set[str] = set()
    for p in py_files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    unused = []
    for mod, path in module_names.items():
        short = mod.split(".")[-1]
        if path.name == "__init__.py":
            continue
        if not any(
            mod == imp or mod.startswith(imp + ".")
            or short == imp.split(".")[-1]
            for imp in imports
        ):
            unused.append(path.relative_to(ROOT))

    if unused:
        print(f"Potentially unreferenced ({len(unused)} files):")
        for p in sorted(unused):
            print(f"  {p}")
    else:
        print("No unreferenced modules found. ✓")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    args = set(sys.argv[1:])
    run_all = not args

    if run_all or "--bom" in args:
        check_bom()
    if run_all or "--syntax" in args:
        check_syntax()
    if run_all or "--loc" in args:
        check_loc()
    if run_all or "--dead" in args:
        check_dead_code()
    if run_all or "--unused" in args:
        check_unused_imports()
    if run_all or "--circular" in args:
        check_circular()
    if run_all or "--complexity" in args:
        check_complexity()
    if run_all or "--unreferenced" in args:
        check_unreferenced_modules()

    print("\n" + "="*60)
    print("AUDIT COMPLETE")
    print("="*60)


if __name__ == "__main__":
    main()
