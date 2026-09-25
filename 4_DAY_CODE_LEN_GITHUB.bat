@echo off
chcp 65001 >nul
title [MAY CHU DEV] DAY TOAN BO CODE MOI LEN GITHUB
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - DAY CODE MOI LEN GITHUB CHO NGUOI DUNG
echo ==============================================================================
echo.

:: 1. Kiem tra xem co lenh git khong
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Khong tim thay cong cu Git tren may tinh nay.
    echo Vui long cai dat Git de thuc hien day code.
    pause
    exit /b 1
)

:: 2. Build Frontend neu co thay doi
echo [*] Dang kiem tra va build giao dien React Frontend...
if exist "frontend\node_modules" (
    cd frontend
    call npm run build
    cd ..
)

echo.
echo [*] Dang gom toan bo file ma nguon moi nhat...
git add .

set /p COMMIT_MSG="Nhap mo ta ban cap nhat (Bam Enter de dung mac dinh): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Cap nhat tinh nang va sua loi he thong moi nhat

echo [*] Dang tao Commit: "%COMMIT_MSG%"...
git commit -m "%COMMIT_MSG%"

echo.
echo [*] Dang day code len GitHub Repository...
git push origin main

if %errorlevel% equ 0 (
    echo.
    echo ==============================================================================
    echo [✔] DA DAY TOAN BO CODE MOI LEN GITHUB THANH CONG 100%!
    echo.
    echo Gio day, tat ca cac may khach khac chi can mo '2_KHOI_DONG.bat' hoac
    echo chay '3_CAP_NHAT_CODE.bat' la se tu dong dong bo ban moi nay ve may.
    echo ==============================================================================
) else (
    echo.
    echo [!] Co loi xay ra khi day code len GitHub. Vui long kiem tra lai mang hoac token quyen push.
)

echo.
pause
