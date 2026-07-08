param(
    [string]$Name = "bankingApp-single-docker"
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
uv run --project $projectDir --group build python (Join-Path $projectDir "scripts\build_exe.py")
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}
