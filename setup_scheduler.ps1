# setup_scheduler.ps1
# Registers two Windows Task Scheduler tasks for CB6 Quantum NSE bot.
#
# Task 1: CB6_AutoToken   — runs auto_token.py at 8:45 AM IST (Mon-Fri)
#                           Opens Fyers login browser + sends Telegram link
#                           Launches main.py automatically after login
#
# Task 2: CB6_Watchdog    — runs watchdog.py at 9:10 AM IST (Mon-Fri)
#                           Monitors main.py; auto-restarts on crash/hang
#
# Usage (run as Administrator):
#   powershell -ExecutionPolicy Bypass -File setup_scheduler.ps1
#
# To remove tasks later:
#   Unregister-ScheduledTask -TaskName "CB6_AutoToken" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "CB6_Watchdog"  -Confirm:$false

$BotDir  = "c:\cb6_bot"
$Python  = (Get-Command python).Source
$LogDir  = "$BotDir\logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
}

Write-Host ""
Write-Host "=============================================="
Write-Host "  CB6 QUANTUM — Windows Task Scheduler Setup"
Write-Host "=============================================="
Write-Host ""
Write-Host "Bot directory : $BotDir"
Write-Host "Python        : $Python"
Write-Host ""

# ── Task 1: CB6_AutoToken ──────────────────────────────────────────────────────
Write-Host "Registering Task 1: CB6_AutoToken (8:45 AM Mon-Fri)..."

$Action1 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "auto_token.py" `
    -WorkingDirectory $BotDir

$Trigger1 = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:45AM"

$Settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -WakeToRun

$Principal1 = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest

try {
    Unregister-ScheduledTask -TaskName "CB6_AutoToken" -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName "CB6_AutoToken" `
        -Action   $Action1 `
        -Trigger  $Trigger1 `
        -Settings $Settings1 `
        -Principal $Principal1 `
        -Description "CB6 Quantum: Daily Fyers token refresh + NSE bot auto-launch at 8:45 AM IST" `

        | Out-Null
    Write-Host "  OK  CB6_AutoToken registered" -ForegroundColor Green
} catch {
    Write-Host "  FAIL CB6_AutoToken: $_" -ForegroundColor Red
}


# ── Task 2: CB6_Watchdog ───────────────────────────────────────────────────────
Write-Host "Registering Task 2: CB6_Watchdog (9:10 AM Mon-Fri)..."

$Action2 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "watchdog.py --attach" `
    -WorkingDirectory $BotDir

$Trigger2 = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "09:10AM"

$Settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -StartWhenAvailable `
    -WakeToRun

$Principal2 = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest

try {
    Unregister-ScheduledTask -TaskName "CB6_Watchdog" -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName "CB6_Watchdog" `
        -Action   $Action2 `
        -Trigger  $Trigger2 `
        -Settings $Settings2 `
        -Principal $Principal2 `
        -Description "CB6 Quantum: NSE bot watchdog - monitors + auto-restarts main.py" `
        | Out-Null
    Write-Host "  OK  CB6_Watchdog registered" -ForegroundColor Green
} catch {
    Write-Host "  FAIL CB6_Watchdog: $_" -ForegroundColor Red
}


# ── Summary ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================="
Write-Host "  SETUP COMPLETE"
Write-Host "=============================================="
Write-Host ""
Write-Host "  8:45 AM  CB6_AutoToken runs:"
Write-Host "    - Checks if Fyers token is fresh"
Write-Host "    - If stale: opens browser login + sends Telegram link"
Write-Host "    - After login: saves token, starts main.py"
Write-Host "    - Sends Telegram: Bot Started"
Write-Host ""
Write-Host "  9:10 AM  CB6_Watchdog runs:"
Write-Host "    - Attaches to running main.py"
Write-Host "    - Monitors heartbeat every 60s"
Write-Host "    - Restarts bot if crash or hang (>6 min no heartbeat)"
Write-Host "    - Sends Telegram alerts on crash/restart"
Write-Host ""
Write-Host "  To verify tasks are registered:"
Write-Host "    Get-ScheduledTask -TaskName 'CB6_AutoToken'"
Write-Host "    Get-ScheduledTask -TaskName 'CB6_Watchdog'"
Write-Host ""
Write-Host "  To run manually right now:"
Write-Host "    python auto_token.py    (token refresh + bot start)"
Write-Host "    python watchdog.py      (standalone monitor)"
Write-Host ""

# ── Verify ─────────────────────────────────────────────────────────────────────
$t1 = Get-ScheduledTask -TaskName "CB6_AutoToken" -ErrorAction SilentlyContinue
$t2 = Get-ScheduledTask -TaskName "CB6_Watchdog"  -ErrorAction SilentlyContinue

if ($t1) { Write-Host "  CB6_AutoToken : $($t1.State)" -ForegroundColor Cyan }
if ($t2) { Write-Host "  CB6_Watchdog  : $($t2.State)" -ForegroundColor Cyan }

Write-Host ""
