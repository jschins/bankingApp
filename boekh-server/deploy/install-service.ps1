#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the FastAPI app as a Windows service using NSSM.

.DESCRIPTION
    Wraps `uvicorn` in a Windows service that starts on boot and restarts on
    crash. Requires NSSM (https://nssm.cc) on PATH or via -NssmPath.

    Run from an elevated PowerShell (Run as Administrator).

.PARAMETER ServiceName
    Service name. Defaults to "bankingApp-server".

.PARAMETER Port
    Port to bind. Defaults to PORT in .env, else 8000.

.PARAMETER NssmPath
    Full path to nssm.exe if it is not on PATH.
#>
param(
    [string]$ServiceName = 'bankingApp-server',
    [string]$AppModule,
    [string]$BindHost,
    [int]$Port = 0,
    [int]$Workers = 0,
    [string]$NssmPath = 'nssm'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

function Get-EnvValue {
    param([string]$Key, [string]$Default)
    $envFile = Join-Path $here '.env'
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
        if ($line) { return ($line -replace "^\s*$Key\s*=\s*", '').Trim() }
    }
    return $Default
}

# Resolve config
if (-not $AppModule) { $AppModule = Get-EnvValue -Key 'APP_MODULE' -Default 'app.main:app' }
if (-not $BindHost)  { $BindHost  = Get-EnvValue -Key 'HOST' -Default '0.0.0.0' }
if ($Port -eq 0)     { $Port      = [int](Get-EnvValue -Key 'PORT' -Default '8000') }
if ($Workers -eq 0)  { $Workers   = [int](Get-EnvValue -Key 'WORKERS' -Default '1') }

# Verify NSSM is available
try { & $NssmPath version | Out-Null }
catch { throw "NSSM not found. Install it (choco install nssm) or pass -NssmPath C:\path\to\nssm.exe" }

# Locate the python/uvicorn from the venv if present, else system uvicorn
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $exe  = $venvPython
    $args = "-m uvicorn $AppModule --host $BindHost --port $Port --workers $Workers"
} else {
    $exe  = (Get-Command uvicorn -ErrorAction Stop).Source
    $args = "$AppModule --host $BindHost --port $Port --workers $Workers"
}

$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$envFile = Join-Path $here '.env'

Write-Host "==> Installing service '$ServiceName'" -ForegroundColor Cyan
Write-Host "    exe : $exe"
Write-Host "    args: $args"

# Remove a previous install of the same name, if any
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "    Existing service found; reinstalling..." -ForegroundColor Yellow
    & $NssmPath stop $ServiceName | Out-Null
    & $NssmPath remove $ServiceName confirm | Out-Null
}

& $NssmPath install $ServiceName $exe $args
& $NssmPath set $ServiceName AppDirectory $root
& $NssmPath set $ServiceName AppStdout (Join-Path $logDir 'stdout.log')
& $NssmPath set $ServiceName AppStderr (Join-Path $logDir 'stderr.log')
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 5000
if (Test-Path $envFile) {
    & $NssmPath set $ServiceName AppEnvironmentExtra "DOTENV_FILE=$envFile"
}

& $NssmPath start $ServiceName

Write-Host ""
Write-Host "Service '$ServiceName' installed and started." -ForegroundColor Green
Write-Host "  Status : nssm status $ServiceName"
Write-Host "  Logs   : $logDir"
Write-Host "  Remove : ./uninstall-service.ps1 -ServiceName $ServiceName"
