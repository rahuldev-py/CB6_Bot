@echo off
setlocal enabledelayedexpansion
title CB6 Quantum — All Engines
color 0A
cls

echo.
echo  ██████╗██████╗  ██████╗      ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗████████╗██╗   ██╗███╗   ███╗
echo  ██╔════╝██╔══██╗██╔════╝     ██╔═══██╗██║   ██║██╔══██╗████╗  ██║╚══██╔══╝██║   ██║████╗ ████║
echo  ██║     ██████╔╝███████╗     ██║   ██║██║   ██║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
echo  ██║     ██╔══██╗██╔═══╝ ██   ██║▄▄ ██║██║   ██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
echo  ╚██████╗██████╔╝███████╗╚█   ╚██████╔╝╚██████╔╝██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
echo   ╚═════╝╚═════╝ ╚══════╝      ╚══▀▀═╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝
echo.
echo  Engines  : NSE (NIFTY/BN/FIN/MID)  ^|  FTMO FOREX  ^|  GFT FOREX
echo  Strategy : ICT Silver Bullet — DOL → MSS → FVG → Entry
echo  Dashboard: https://brokera.in
echo  Kill     : create data\kill_all.flag
echo.
echo ════════════════════════════════════════════════════════════════════════════════
echo.

set PYTHON=C:\Users\Rahul\AppData\Local\Programs\Python\Python313\python.exe
set BOT_DIR=C:\cb6_bot

REM ── Verify Python ──────────────────────────────────────────────────────────
if not exist "%PYTHON%" (
    echo  [ERROR] Python not found at: %PYTHON%
    echo  Edit PYTHON= in this file to point to your python.exe
    echo.
    pause
    exit /b 1
)

REM ── Verify MT5 is running (Forex needs it) ─────────────────────────────────
echo  Checking MetaTrader 5...
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I "terminal64.exe" >NUL
if errorlevel 1 (
    echo.
    echo  [WARN] MetaTrader 5 (terminal64.exe) is NOT running.
    echo  Forex engine will fail to connect without MT5 open.
    echo.
    set /p mt5choice="  Start bot anyway? (y/N): "
    if /i not "!mt5choice!"=="y" (
        echo  Open MT5 first, then re-run this launcher.
        pause
        exit /b 1
    )
) else (
    echo  MT5 is running — OK
)

echo.
echo  Starting all engines...
echo  (Ctrl+C to stop everything cleanly)
echo.
echo ════════════════════════════════════════════════════════════════════════════════
echo.

cd /d "%BOT_DIR%"
"%PYTHON%" orchestrator.py

echo.
echo ════════════════════════════════════════════════════════════════════════════════
echo  CB6 Quantum stopped.
echo ════════════════════════════════════════════════════════════════════════════════
echo.
pause
