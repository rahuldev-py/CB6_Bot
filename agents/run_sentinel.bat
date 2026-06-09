@echo off
title CB6 SENTINEL — Risk Audit
cd /d C:\cb6_bot
echo Running SENTINEL audit...
C:\Users\Rahul\AppData\Local\Programs\Python\Python313\python.exe agents\sovereign.py --sentinel
pause
