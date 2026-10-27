$ErrorActionPreference = 'Stop'
$extensionRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $extensionRoot
try {
    node scripts/validate.js
    if ($LASTEXITCODE -ne 0) { throw 'Validation failed; archive not created.' }
    $destination = Join-Path $extensionRoot 'dist'
    if (-not (Test-Path -LiteralPath $destination)) { New-Item -ItemType Directory -Path $destination | Out-Null }
    $archive = Join-Path $destination 'Crous-Cergy-MV3.zip'
    $files = @('manifest.json','src','popup','icons','README.md') | ForEach-Object { Join-Path $extensionRoot $_ }
    Compress-Archive -LiteralPath $files -DestinationPath $archive -Force
    Get-FileHash -LiteralPath $archive -Algorithm SHA256 | Select-Object Path,Hash
} finally { Pop-Location }
