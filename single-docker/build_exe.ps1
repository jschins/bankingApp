param(
    [string]$Name = "boekh_onedir"
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
uv run --project $projectDir --group build python (Join-Path $projectDir "scripts\build_onedir.py")
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}
