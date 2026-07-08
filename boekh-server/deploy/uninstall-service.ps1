#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Stop and remove the NSSM-managed Windows service.
#>
param(
    [string]$ServiceName = 'bankingApp-server',
    [string]$NssmPath = 'nssm'
)

$ErrorActionPreference = 'Stop'

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "Service '$ServiceName' not found; nothing to do." -ForegroundColor Yellow
    return
}

Write-Host "==> Stopping and removing '$ServiceName'..." -ForegroundColor Cyan
& $NssmPath stop $ServiceName | Out-Null
& $NssmPath remove $ServiceName confirm | Out-Null
Write-Host "Removed." -ForegroundColor Green
