@echo off
chcp 65001 >nul
title [CAP NHAT MA NGUON] STUDIO REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - DONG BO MA NGUON MOI NHAT TU GITHUB
echo ==============================================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [*] Dang chay cap nhat bang Python...
    python app_updater.py
) else (
    venv\Scripts\python.exe app_updater.py
)

echo.
echo ==============================================================================
echo [OK] Hoan tat kiem tra dong bo! Ban co the chay '2_KHOI_DONG.bat' ngay.
echo ==============================================================================
pause
