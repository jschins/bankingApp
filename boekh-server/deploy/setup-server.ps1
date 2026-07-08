#Requires -RunAsAdministrator
<#
.SYNOPSIS
    One-time prep to make a Windows laptop usable as an always-on server.

.DESCRIPTION
    - Disables sleep/hibernate while on AC power.
    - Sets the "lid close" action to do nothing while on AC power.
    - Opens the app's inbound TCP port in Windows Defender Firewall.

    Run from an elevated PowerShell (Run as Administrator).

.PARAMETER Port
    Inbound TCP port to open. Defaults to the PORT in .env, else 8000.
#>
param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-EnvValue {
    param([string]$Key, [string]$Default)
    $envFile = Join-Path $here '.env'
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
        if ($line) { return ($line -replace "^\s*$Key\s*=\s*", '').Trim() }
    }
    return $Default
}

if ($Port -eq 0) { $Port = [int](Get-EnvValue -Key 'PORT' -Default '8000') }

Write-Host "==> Disabling sleep & hibernate on AC power..." -ForegroundColor Cyan
# 0 = never. Apply to the active power scheme.
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off

Write-Host "==> Setting 'lid close' action to do nothing (AC power)..." -ForegroundColor Cyan
# LIDACTION sub-GUID under Power buttons and lid; 0 = Do nothing.
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

$ruleName = "bankingApp-server (TCP $Port)"
Write-Host "==> Opening inbound firewall port $Port ($ruleName)..." -ForegroundColor Cyan
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "    Rule already exists; skipping."
} else {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
    Write-Host "    Created firewall rule."
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Next: set a static LAN IP (router DHCP reservation is easiest)." -ForegroundColor Yellow
Write-Host "Your current addresses:" -ForegroundColor Yellow
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1' } |
    Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize
