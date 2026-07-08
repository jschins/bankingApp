<#
.SYNOPSIS
    Run the FastAPI app with uvicorn (foreground). Good for testing before
    installing it as a service.

.DESCRIPTION
    Reads HOST/PORT/APP_MODULE/WORKERS from deploy/.env (with sensible defaults),
    activates the repo virtualenv if present, and starts uvicorn bound to all
    interfaces so other devices on the LAN can connect.
#>
param(
    [string]$AppModule,
    [string]$BindHost,
    [int]$Port = 0,
    [int]$Workers = 0
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

if (-not $AppModule) { $AppModule = Get-EnvValue -Key 'APP_MODULE' -Default 'app.main:app' }
if (-not $BindHost)  { $BindHost  = Get-EnvValue -Key 'HOST' -Default '0.0.0.0' }
if ($Port -eq 0)     { $Port      = [int](Get-EnvValue -Key 'PORT' -Default '8000') }
if ($Workers -eq 0)  { $Workers   = [int](Get-EnvValue -Key 'WORKERS' -Default '1') }

$venvActivate = Join-Path $root '.venv\Scripts\Activate.ps1'
if (Test-Path $venvActivate) {
    Write-Host "==> Activating virtualenv" -ForegroundColor Cyan
    . $venvActivate
}

Set-Location $root
Write-Host "==> uvicorn $AppModule --host $BindHost --port $Port --workers $Workers" -ForegroundColor Cyan
uvicorn $AppModule --host $BindHost --port $Port --workers $Workers
