# CB6 SOVEREIGN — Deploy All Agents via Windows Task Scheduler
# Run this once as Administrator to register all scheduled tasks
# Usage: Right-click → Run as Administrator

$Python   = "C:\Users\Rahul\AppData\Local\Programs\Python\Python313\python.exe"
$CB6Root  = "C:\cb6_bot"
$LogDir   = "C:\cb6_bot\agent_reports\logs"

# Create log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  CB6 SOVEREIGN — Deploying All Agents" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ── TASK 1: SOVEREIGN Full Pipeline (daily at 11:30 PM IST = 18:00 UTC) ──────
$Action1 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "agents\sovereign.py" `
    -WorkingDirectory $CB6Root

$Trigger1 = New-ScheduledTaskTrigger -Daily -At "11:30PM"

$Settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "CB6_SOVEREIGN_DailyPipeline" `
    -TaskPath "\CB6\" `
    -Action $Action1 `
    -Trigger $Trigger1 `
    -Settings $Settings1 `
    -Description "CB6 SOVEREIGN: Full agent pipeline (CIPHER→SHADOW→ATLAS→LEDGER→ECHO→REACH→NEXUS). Sends Telegram board report to Rahul." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ SOVEREIGN Full Pipeline → Daily 11:30 PM IST" -ForegroundColor Green

# ── TASK 2: CIPHER Quick Quant Check (every 6 hours during market hours) ──────
$Action2 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.cipher_quant" `
    -WorkingDirectory $CB6Root

# Run at 9 AM, 12 PM, 3 PM, 6 PM IST
$Triggers2 = @(
    $(New-ScheduledTaskTrigger -Daily -At "9:00AM"),
    $(New-ScheduledTaskTrigger -Daily -At "12:00PM"),
    $(New-ScheduledTaskTrigger -Daily -At "3:00PM"),
    $(New-ScheduledTaskTrigger -Daily -At "6:00PM")
)

$Settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "CB6_CIPHER_QuantCheck" `
    -TaskPath "\CB6\" `
    -Action $Action2 `
    -Trigger $Triggers2[0] `
    -Settings $Settings2 `
    -Description "CB6 CIPHER: Reads trade data every 6 hours. Feeds SHADOW ML." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ CIPHER Quant Check      → Every 6 hours (9AM/12PM/3PM/6PM IST)" -ForegroundColor Green

# ── TASK 3: SENTINEL Daily Audit (every morning 8 AM IST) ─────────────────────
$Action3 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.sentinel_audit" `
    -WorkingDirectory $CB6Root

$Trigger3 = New-ScheduledTaskTrigger -Daily -At "8:00AM"

$Settings3 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "CB6_SENTINEL_DailyAudit" `
    -TaskPath "\CB6\" `
    -Action $Action3 `
    -Trigger $Trigger3 `
    -Settings $Settings3 `
    -Description "CB6 SENTINEL: Daily risk audit at 8 AM IST. Checks prop firm rule compliance." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ SENTINEL Daily Audit    → Daily 8:00 AM IST" -ForegroundColor Green

# ── TASK 4: LEDGER Financial Snapshot (daily 7 AM IST) ───────────────────────
$Action4 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.ledger_cfo" `
    -WorkingDirectory $CB6Root

$Trigger4 = New-ScheduledTaskTrigger -Daily -At "7:00AM"

Register-ScheduledTask `
    -TaskName "CB6_LEDGER_FinancialSnapshot" `
    -TaskPath "\CB6\" `
    -Action $Action4 `
    -Trigger $Trigger4 `
    -Settings $Settings3 `
    -Description "CB6 LEDGER: Morning financial snapshot — PnL vs targets, agent costs, SaaS projection." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ LEDGER Financial        → Daily 7:00 AM IST" -ForegroundColor Green

# ── TASK 5: ECHO Content (daily 8:30 AM IST — ready before market open) ──────
$Action5 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.echo_writer" `
    -WorkingDirectory $CB6Root

$Trigger5 = New-ScheduledTaskTrigger -Daily -At "8:30AM"

Register-ScheduledTask `
    -TaskName "CB6_ECHO_DailyContent" `
    -TaskPath "\CB6\" `
    -Action $Action5 `
    -Trigger $Trigger5 `
    -Settings $Settings3 `
    -Description "CB6 ECHO: Generates daily LinkedIn + Twitter content for CB6 Quantum brand." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ ECHO Content            → Daily 8:30 AM IST" -ForegroundColor Green

# ── TASK 6: REACH Growth Strategy (weekly Monday 9 AM IST) ───────────────────
$Action6 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.reach_growth" `
    -WorkingDirectory $CB6Root

$Trigger6 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "9:00AM"

Register-ScheduledTask `
    -TaskName "CB6_REACH_WeeklyGrowth" `
    -TaskPath "\CB6\" `
    -Action $Action6 `
    -Trigger $Trigger6 `
    -Settings $Settings3 `
    -Description "CB6 REACH: Weekly growth strategy refresh — channels, outreach, partnerships." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ REACH Growth Strategy   → Weekly Monday 9:00 AM IST" -ForegroundColor Green

# ── TASK 7: ATLAS Engineering Standup (daily 7:30 AM IST) ────────────────────
$Action7 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m agents.atlas_cto" `
    -WorkingDirectory $CB6Root

$Trigger7 = New-ScheduledTaskTrigger -Daily -At "7:30AM"

Register-ScheduledTask `
    -TaskName "CB6_ATLAS_EngineeringStandup" `
    -TaskPath "\CB6\" `
    -Action $Action7 `
    -Trigger $Trigger7 `
    -Settings $Settings3 `
    -Description "CB6 ATLAS: Morning engineering standup — syntax check, priority tasks, codebase health." `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ ATLAS Engineering       → Daily 7:30 AM IST" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ALL 7 AGENTS DEPLOYED" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Schedule Summary:" -ForegroundColor Yellow
Write-Host "  7:00 AM  → LEDGER  (financial snapshot)"
Write-Host "  7:30 AM  → ATLAS   (engineering standup)"
Write-Host "  8:00 AM  → SENTINEL(daily risk audit)"
Write-Host "  8:30 AM  → ECHO    (content creation)"
Write-Host "  9/12/3/6 → CIPHER  (quant check x4)"
Write-Host "  Monday   → REACH   (weekly growth strategy)"
Write-Host "  11:30 PM → NEXUS   (full pipeline + Telegram to Rahul)"
Write-Host ""
Write-Host "View tasks: Task Scheduler → Task Scheduler Library → CB6"
Write-Host "Logs:       C:\cb6_bot\agent_reports\"
Write-Host ""
Write-Host "Cost: $0/month (Groq free tier)" -ForegroundColor Green
Write-Host ""
