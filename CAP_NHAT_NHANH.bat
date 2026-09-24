@echo off
chcp 65001 >nul
title [CAP NHAT NHANH] STUDIO REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - CAP NHAT NHANH 5 GIAY (KHONG CAI LAI TU DAU)
echo ==============================================================================
echo  - Giu nguyen moi truong Python & PyTorch da cai san.
echo  - Cap nhat bo sung goi bo tro download va cong cu he thong...
echo ==============================================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [!] Chua tim thay venv. Vui long chay '1_CAI_DAT_HE_THONG.bat'.
    pause
    exit /b 1
)

echo [*] Dang ket noi GitHub de dong bo code moi nhat...
venv\Scripts\python.exe app_updater.py

echo.
echo ==============================================================================
echo [OK] DA HOAN TAT KIEM TRA VA DONG BO!
echo ==============================================================================
echo.
set /p START_NOW="Ban co muon khoi dong Studio ngay khong? [Y/N, mac dinh Y]: "
if /i "%START_NOW%"=="N" exit /b 0

start "" "%~dp02_KHOI_DONG.bat"
exit /b 0
