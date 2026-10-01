@echo off
chcp 65001 >nul
title [CAP NHAT MA NGUON] STUDIO REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - DONG BO MA NGUON MOI NHAT TU GITHUB
echo ==============================================================================
echo.

:: 1. Tự động đóng các tiến trình Python cũ đang chiếm dụng file
echo [*] Dang giai phong cac tien trinh cu dang chay...
taskkill /F /FI "WINDOWTITLE eq *STUDIO REVIEW MAT THAN*" >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 1 /nobreak >nul

where git >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if exist ".git" (
        echo [*] Phat hien Git - Dang dong bo toan bo ma nguon va giao dien moi nhat tu GitHub...
        git fetch origin main
        git reset --hard origin/main
        git clean -fd -e database -e input -e output -e temp -e .env -e venv
        echo.
    )
)

if not exist "venv\Scripts\python.exe" (
    echo [*] Dang chay cap nhat he thong...
    python app_updater.py --force
) else (
    echo [*] Dang chay cap nhat he thong qua venv...
    venv\Scripts\python.exe app_updater.py --force
)

echo.
echo ==============================================================================
echo [OK] Hoan tat dong bo ma nguon moi nhat! Ban co the chay '2_KHOI_DONG.bat'.
echo ==============================================================================
pause

