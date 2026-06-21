# register-prune-task.ps1 — install a WEEKLY Windows Scheduled Task that runs
# prune-safe.ps1 (safe Docker cleanup). Idempotent: re-running replaces the task.
#
# Runs only when you are logged on — which is correct, because Docker Desktop
# itself only runs while you are logged on, so there is nothing to prune otherwise.
# Registering a per-user "logon only" task needs no admin elevation.
#
# Run once:  pwsh -File C:\unifiedanalyzer\docker\register-prune-task.ps1

$taskName = 'UnifiedAnalyzer-DockerPruneSafe'
$script   = Join-Path $PSScriptRoot 'prune-safe.ps1'

# Prefer pwsh 7; fall back to Windows PowerShell.
$runner = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $runner) { $runner = (Get-Command powershell).Source }

$action  = New-ScheduledTaskAction -Execute $runner `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""

# Weekly, Sunday 03:00. StartWhenAvailable catches up if the PC was off/asleep.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Weekly safe Docker prune (dangling images + build cache; never volumes/-a).' `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' (weekly Sun 03:00)."
Write-Host "Runner: $runner"
Write-Host "Script: $script"
