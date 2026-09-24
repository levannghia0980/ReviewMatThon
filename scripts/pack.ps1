$sourceDir = (Resolve-Path "$PSScriptRoot\..").Path
$zipPath = Join-Path (Split-Path -Parent $sourceDir) "ReviewMatThon_FULL_1CLICK.zip"
$tempDir = Join-Path $env:TEMP "ReviewMatThon_PackageTemp"

Write-Host '=============================================================================='
Write-Host 'DANG DONG GOI FULL BAN CHAY SAN (GIU NGUYEN .ENV, KEY, DATABASE VA GIAO DIEN)'
Write-Host '=============================================================================='

if (Test-Path $tempDir) { Remove-Item -Path $tempDir -Recurse -Force }
if (Test-Path $zipPath) { Remove-Item -Path $zipPath -Force }

New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$appTempDir = Join-Path $tempDir "ReviewMatThon"
New-Item -ItemType Directory -Path $appTempDir -Force | Out-Null

$excludeDirs = @("venv", "node_modules", "__pycache__", ".pytest_cache", "scratch", "temp", ".git", ".gemini", ".vscode", ".idea")

Get-ChildItem -Path $sourceDir | ForEach-Object {
    $itemName = $_.Name
    if (-not ($excludeDirs -contains $itemName) -and -not $itemName.EndsWith(".zip")) {
        $dest = Join-Path $appTempDir $itemName
        if ($_.PSIsContainer) {
            Copy-Item -Path $_.FullName -Destination $dest -Recurse -Force
            Get-ChildItem -Path $dest -Recurse -Directory | Where-Object { 
                $_.Name -eq "__pycache__" -or $_.Name -eq "node_modules" -or $_.Name -eq ".pytest_cache" 
            } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Copy-Item -Path $_.FullName -Destination $dest -Force
        }
    }
}

# Dam bao giu nguyen .env
$envSource = Join-Path $sourceDir ".env"
$envDest = Join-Path $appTempDir ".env"
if (Test-Path $envSource) {
    Copy-Item -Path $envSource -Destination $envDest -Force
    Write-Host '[OK] Da giu nguyen file .env (Day du API Keys cua ban de nguoi dung khong can cai dat key)'
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
Write-Host '[*] Dang nen goi thanh file ZIP...'
[System.IO.Compression.ZipFile]::CreateFromDirectory($tempDir, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)

Remove-Item -Path $tempDir -Recurse -Force

$sizeMB = [math]::Round(((Get-Item $zipPath).Length / 1MB), 2)
Write-Host '=============================================================================='
Write-Host '[OK] DONG GOI THANH CONG 100%!'
Write-Host "-> Duong dan file zip: $zipPath"
Write-Host "-> Dung luong: $sizeMB MB"
Write-Host '=============================================================================='
