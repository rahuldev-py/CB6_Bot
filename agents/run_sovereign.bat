@echo off
title CB6 SOVEREIGN — Agent Pipeline
cd /d C:\cb6_bot
echo.
echo ============================================================
echo   CB6 SOVEREIGN — Running Agent Pipeline
echo   %date% %time%
echo ============================================================
echo.
C:\Users\Rahul\AppData\Local\Programs\Python\Python313\python.exe agents\sovereign.py
echo.
echo ============================================================
echo   Pipeline complete — check Telegram for board report
echo ============================================================
echo.
