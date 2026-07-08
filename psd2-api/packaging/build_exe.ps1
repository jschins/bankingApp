<#
.SYNOPSIS
  Build one family member's psd2-api executable, with their Enable Banking
  credentials and bankingApp-server destination baked in.

.DESCRIPTION
  Reads a per-person profile folder that must contain:
    profile.json   - person id, Enable Banking app id, bank, redirect url
                     (see profile.json.example)
    <key_file>     - that person's Enable Banking private key (.pem), named by
                     the "key_file" field inside profile.json

  The shared bankingApp-server destination (server_url + server_api_key) lives in a
  single central file, packaging\server.json (see server.json.example), and is
  merged into every person's profile at build time. Change the server address
  once there and rebuild; you never edit it per person.

  It produces a single-file .exe under dist\ that, when double-clicked, guides
  the person through re-authorization and reports a small consent record to the
  server. No secrets are ever passed on the command line.

.EXAMPLE
  .\packaging\build_exe.ps1 -ProfileDir .\packaging\profiles\js
#>
param(
    [Parameter(Mandatory = $true)][string]$ProfileDir,
    [string]$Name
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

$profilePath = Join-Path $ProfileDir "profile.json"
if (-not (Test-Path $profilePath)) { throw "profile.json not found in $ProfileDir" }

$profile = Get-Content $profilePath -Raw | ConvertFrom-Json
$person = $profile.person
$keyFile = $profile.key_file
if (-not $person)  { throw "profile.json is missing 'person'" }
if (-not $keyFile) { throw "profile.json is missing 'key_file'" }

$keyPath = Join-Path $ProfileDir $keyFile
if (-not (Test-Path $keyPath)) { throw "Private key '$keyFile' not found in $ProfileDir" }
if (-not $Name) { $Name = "bankingApp-reauthorize-$person" }

# Merge the central server settings (URL + shared API key) into this person's
# profile so the address is defined in exactly one place.
$serverPath = Join-Path $PSScriptRoot "server.json"
if (-not (Test-Path $serverPath)) {
    throw "server.json not found in $PSScriptRoot (copy server.json.example and fill it in)"
}
$server = Get-Content $serverPath -Raw | ConvertFrom-Json

$merged = [ordered]@{}
foreach ($p in $profile.PSObject.Properties) { $merged[$p.Name] = $p.Value }
$merged["server_url"] = $server.server_url
$merged["server_api_key"] = $server.server_api_key

$buildDir = Join-Path ([System.IO.Path]::GetTempPath()) ("bankingApp-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $buildDir | Out-Null
$mergedProfile = Join-Path $buildDir "profile.json"
# Write UTF-8 *without* BOM; the loader reads strict utf-8 and a BOM breaks it.
[System.IO.File]::WriteAllText($mergedProfile, ($merged | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))

# PyInstaller --add-data uses 'src;dest' on Windows. Bundle the merged profile
# and key at the package root so profile.load_profile() finds them via _MEIPASS.
Push-Location $repoRoot
try {
    uv run --group build pyinstaller --onefile --clean --noconfirm `
        --name $Name `
        --paths "$repoRoot" `
        --collect-submodules psd2_api `
        --add-data "$mergedProfile;." `
        --add-data "$keyPath;." `
        "packaging\entry.py"
}
finally {
    Pop-Location
    Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Built dist\$Name.exe for person '$person'." -ForegroundColor Green
Write-Host "Hand this single file to $person; double-clicking it runs the guided flow."
