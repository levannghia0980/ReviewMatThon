# ==============================================================================
# STUDIO MAT THON REVIEW - POWERSHELL LAUNCHER
# ==============================================================================
$Host.UI.RawUI.WindowTitle = "STUDIO MAT THON REVIEW [PowerShell]"
Set-Location -Path $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "[!] Chua tim thay venv. Dang mo script cai dat..." -ForegroundColor Yellow
    & ".\1_CAI_DAT_HE_THONG.bat"
    exit
}

Write-Host "[*] Dang khoi dong Studio Mat Thon Review qua PowerShell..." -ForegroundColor Cyan
& $pythonExe "run.py"
