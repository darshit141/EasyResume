# One-time setup: registers a Windows Scheduled Task that runs
# start_celery.ps1 automatically whenever you log in, so the Celery
# worker/beat don't have to be started by hand every dev session.
#
# Run once, from an elevated PowerShell (Run as Administrator):
#   powershell -ExecutionPolicy Bypass -File scripts\windows\register_celery_startup.ps1
#
# To remove it later:
#   Unregister-ScheduledTask -TaskName "IntelliResume-Celery" -Confirm:$false

$ErrorActionPreference = "Stop"
$taskName = "IntelliResume-Celery"
$startScript = Join-Path $PSScriptRoot "start_celery.ps1"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This needs to run elevated (Run as Administrator) - registering a scheduled task requires it."
    exit 1
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

# Don't require the machine to be plugged in/idle - this is a dev
# convenience task, not a battery-sensitive background job.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Registered '$taskName' - Celery worker + beat will now start automatically at login."
Write-Host "Redis (e.g. the 'intelliresume-redis' Docker container) still needs to be running on its own - this task only covers Celery."
