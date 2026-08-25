# Starts the Celery worker and beat scheduler for local Windows dev.
#
# Why this exists: every AI-backed feature (resume parsing, job analysis,
# tailored resume generation, etc.) is dispatched onto a Celery queue -
# "manage.py runserver" alone only enqueues the work, it never runs it.
# Without a worker process actually consuming that queue, uploads and
# similar actions hang or fail even though Redis and the web server both
# look fine. This script is what keeps that from being a recurring
# "why isn't this working" moment.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\windows\start_celery.ps1
#
# Logs land in scripts\windows\logs\ (created on first run, gitignored).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$venvPython = Join-Path $repoRoot "venv\Scripts\python.exe"
$logDir = Join-Path $PSScriptRoot "logs"

if (-not (Test-Path $venvPython)) {
    Write-Error "venv not found at $venvPython - run this from a machine with the backend's venv already set up."
    exit 1
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Fail fast and loud if Redis isn't reachable, rather than starting a
# worker that will just sit there silently unable to connect.
$redisUp = Test-NetConnection -ComputerName localhost -Port 6379 -WarningAction SilentlyContinue
if (-not $redisUp.TcpTestSucceeded) {
    Write-Error "Redis isn't reachable on localhost:6379. Start it first (e.g. 'docker start intelliresume-redis')."
    exit 1
}

Set-Location $repoRoot

# --pool=solo: Celery's default prefork pool needs os.fork(), which Windows
# doesn't have. solo is the standard workaround for local Windows dev -
# single-threaded, fine for development traffic.
Write-Host "Starting Celery worker..."
Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "celery", "-A", "config", "worker", "--loglevel=info", "--pool=solo" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $logDir "worker.log") `
    -RedirectStandardError (Join-Path $logDir "worker.err.log") `
    -WindowStyle Hidden

Write-Host "Starting Celery beat (daily OTP cleanup, etc.)..."
Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "celery", "-A", "config", "beat", "--loglevel=info" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $logDir "beat.log") `
    -RedirectStandardError (Join-Path $logDir "beat.err.log") `
    -WindowStyle Hidden

Write-Host "Both started. Logs: $logDir"
