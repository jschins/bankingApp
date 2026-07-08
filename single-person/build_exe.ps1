param(
    [string]$Name = "bankingApp-single-person"
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
$buildOutputDir = Join-Path $projectDir "dist"
$workDir = Join-Path $projectDir "build"
New-Item -ItemType Directory -Path $buildOutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

$entryPoint = Join-Path $projectDir "entry.py"
$pyinstallerArgs = @(
    "--onefile", "--clean", "--noconfirm",
    "--name", $Name,
    "--paths", $projectDir,
    "--distpath", $buildOutputDir,
    "--workpath", $workDir,
    "--specpath", $projectDir,
    "--collect-submodules", "textual",
    "--copy-metadata", "textual",
    $entryPoint
)

uv run --project $projectDir --group build pyinstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exePath = Join-Path $buildOutputDir "$Name.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed: $exePath was not created"
}

foreach ($folder in @("input", "output", "both")) {
    $src = Join-Path $projectDir $folder
    $dest = Join-Path $buildOutputDir $folder
    if (Test-Path $src) {
        if (Test-Path $dest) {
            Remove-Item -Path $dest -Recurse -Force
        }
        Copy-Item -Path $src -Destination $dest -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
    }
}

Write-Host "Built $exePath" -ForegroundColor Green
Write-Host "Dist layout:" -ForegroundColor Green
Write-Host "  $buildOutputDir\$Name.exe"
Write-Host "  $buildOutputDir\input\"
Write-Host "  $buildOutputDir\output\"
Write-Host "  $buildOutputDir\both\"
