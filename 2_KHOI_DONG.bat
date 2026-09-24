@echo off
chcp 65001 >nul
title [SERVER RUNNING] STUDIO REVIEW MAT THAN
cd /d "%~dp0"

:: 1. Kiem tra xem venv co ton tai khong
if not exist "venv\Scripts\python.exe" goto :VENV_NOT_FOUND

:: 2. Kiem tra xem venv co hoat dong khong
venv\Scripts\python.exe -c "import sys" >nul 2>&1
if %errorlevel% neq 0 goto :VENV_BROKEN

:: 2.1 Kiem tra va tu dong cai thu vien neu chua co du
venv\Scripts\python.exe -c "import uvicorn, fastapi, yt_dlp" >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Dang tu dong cai dat thu vien can thiet [Vui long doi vai giay]...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
)

:: 3. Kiem tra va tu dong cap nhat ma nguon moi tu GitHub (Neu co mang)
venv\Scripts\python.exe app_updater.py

:: 4. Chay ung dung
cls
echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - HE THONG DANG KHOI CHAY
echo ==============================================================================
echo  - Dia chi may chu: http://127.0.0.1:8686
echo  - Trinh duyet web se tu dong mo sau vai giay...
echo  - DE TAT CHUONG TRINH: Chi can dong cua so nay hoac bam to hop phim Ctrl + C.
echo ==============================================================================
echo.

venv\Scripts\python.exe run.py

echo.
echo [!] Chuong trinh da dung hoat dong.
pause
exit /b 0

:VENV_NOT_FOUND
echo ==============================================================================
echo [!] CHUA TIM THAY MOI TRUONG AO VENV!
echo ==============================================================================
echo May tinh cua ban chua chay cai dat lan dau hoac chua khoi tao moi truong.
echo Vui long chay file '1_CAI_DAT_HE_THONG.bat' de he thong tu dong cai dat.
echo ==============================================================================
echo.
set /p RUN_SETUP="Ban co muon tu dong chay cai dat ngay bay gio khong? [Y/N, mac dinh Y]: "
if /i "%RUN_SETUP%"=="N" goto :END_CANCEL
call "%~dp01_CAI_DAT_HE_THONG.bat"
exit /b 0

:VENV_BROKEN
echo ==============================================================================
echo [!] MOI TRUONG AO VENV BI LOI DUONG DAN [CO THE DO COPY TU MAY KHAC SANG]
echo ==============================================================================
echo Thu muc 'venv' hien tai khong khop voi duong dan cua may tinh nay.
echo He thong can chay lai file '1_CAI_DAT_HE_THONG.bat' de khoi tao lai moi truong chuan.
echo ==============================================================================
echo.
set /p FIX_VENV="Ban co muon tu dong cai dat lai moi truong khong? [Y/N, mac dinh Y]: "
if /i "%FIX_VENV%"=="N" goto :END_CANCEL
echo Dang don dep moi truong cu...
rmdir /s /q "venv" >nul 2>&1
call "%~dp01_CAI_DAT_HE_THONG.bat"
exit /b 0

:END_CANCEL
echo Da huy thao tac.
pause
exit /b 1
