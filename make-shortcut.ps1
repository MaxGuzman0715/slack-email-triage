# Registers (or removes) the scheduled task that keeps the triage bot running.
# A scheduled task is used rather than a Startup shortcut because it survives
# reboots, restarts the bot if it ever stops, and is not tied to any console
# window or terminal session.
param([switch]$Remove)

$taskName = 'InterviewTriage'
$runBat   = Join-Path $PSScriptRoot 'run.bat'
$oldLink  = Join-Path ([Environment]::GetFolderPath('Startup')) 'InterviewTriage.lnk'

# An older version of this script installed a Startup shortcut. Left in place
# it would launch a SECOND bot alongside the task, and both would post cards.
if (Test-Path $oldLink) { Remove-Item $oldLink -Force; "Removed the old Startup shortcut." }

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask  -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        "Autostart removed."
    } else { "Autostart was not enabled." }
    exit 0
}

if (-not (Test-Path $runBat)) { Write-Error "run.bat not found next to this script."; exit 1 }

$action  = New-ScheduledTaskAction -Execute $runBat -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$p       = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $set -Principal $p -Description 'Interview email triage bot' -Force | Out-Null

if (-not (Get-ScheduledTask -TaskName $taskName | Where-Object State -eq 'Running')) {
    Start-ScheduledTask -TaskName $taskName
}

"Autostart enabled - starts at login, restarts itself if it stops."
"Check with:  schtasks /query /tn InterviewTriage"
