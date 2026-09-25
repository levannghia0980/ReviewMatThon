@echo off
chcp 65001 >nul
title [CAI DAT HE THONG] STUDIO REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO REVIEW MAT THAN - BO CAI DAT HE THONG TU DONG 1-CLICK
echo ==============================================================================
echo  Quy trinh tu dong thuc hien:
echo   1. Kiem tra Python [Tu dong tai va cai Python 3.11 neu may chua co]
echo   2. Kiem tra FFmpeg [Tu dong cau hinh cong cu video]
echo   3. Khoi tao moi truong ao doc lap venv
echo   4. Nhan dien GPU NVIDIA / CPU de cai PyTorch toi uu
echo   5. Cai dat toan bo thu vien can thiet tu requirements.txt
echo   6. Tao phim tat khoi dong ngoai man hinh Desktop
echo ==============================================================================
echo.

:: ------------------------------------------------------------------------------
:: BUOC 1: KIEM TRA & CAI DAT PYTHON
:: ------------------------------------------------------------------------------
echo [1/6] Dang kiem tra Python tren may tinh...
set "PY_CMD="

python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_READY
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py"
    goto :PYTHON_READY
)

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    goto :PYTHON_READY
)

if exist "C:\Program Files\Python311\python.exe" (
    set "PY_CMD=C:\Program Files\Python311\python.exe"
    goto :PYTHON_READY
)

if exist "C:\Python311\python.exe" (
    set "PY_CMD=C:\Python311\python.exe"
    goto :PYTHON_READY
)

:: Neu chua co Python, tien hanh tai va cai tu dong
echo   [!] May tinh chua cai dat Python.
echo   [*] Dang tu dong tai bo cai Python 3.11.9 chinh thuc tu python.org...
set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"

curl.exe -L -o "%PY_INSTALLER%" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
if not exist "%PY_INSTALLER%" (
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PY_INSTALLER%'"
)

if not exist "%PY_INSTALLER%" (
    echo   [X] Khong the tai Python installer. Vui long kiem tra ket noi mang.
    pause
    exit /b 1
)

echo   [*] Dang tu dong cai dat Python 3.11 [Vui long doi 30-60 giay]...
"%PY_INSTALLER%" /passive PrependPath=1 Include_pip=1 SimpleInstall=1 Include_test=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
del /f /q "%PY_INSTALLER%" >nul 2>&1

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
) else (
    set "PY_CMD=python"
)

:PYTHON_READY
echo   [OK] Da xac nhan Python san sang.

echo.
:: ------------------------------------------------------------------------------
:: BUOC 2: KIEM TRA & CAI DAT FFMPEG & CONG CU BO TRO
:: ------------------------------------------------------------------------------
echo [2/6] Dang kiem tra bo cong cu video va download (FFmpeg, yt-dlp)...
if not exist "tools" mkdir "tools" >nul 2>&1
if not exist "tools\ffmpeg" mkdir "tools\ffmpeg" >nul 2>&1
if not exist "tools\ffmpeg\bin" mkdir "tools\ffmpeg\bin" >nul 2>&1

set "HAS_FFMPEG=0"
if exist "tools\ffmpeg.exe" set "HAS_FFMPEG=1"
if exist "tools\ffmpeg\ffmpeg.exe" set "HAS_FFMPEG=1"
if exist "tools\ffmpeg\bin\ffmpeg.exe" set "HAS_FFMPEG=1"
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 set "HAS_FFMPEG=1"

if %HAS_FFMPEG% equ 1 (
    echo   [OK] Da co bo cong cu FFmpeg san sang.
    goto :FFMPEG_DONE
)

echo   [*] Chua tim thay FFmpeg. Dang thu cai dat qua WinGet hoac tai truc tiep...

:: Thu cach 1: Cai dat bang winget cua Windows
winget --version >nul 2>&1
if %errorlevel% equ 0 (
    echo   [*] Dang cai FFmpeg bang Microsoft WinGet...
    winget install --id Gyan.FFmpeg -e --silent --accept-source-agreements --accept-package-agreements >nul 2>&1
    ffmpeg -version >nul 2>&1
    if %errorlevel% equ 0 (
        echo   [OK] Da cai dat FFmpeg qua WinGet thanh cong!
        goto :FFMPEG_DONE
    )
)

:: Thu cach 2: Tai tu Github Release (Nguon on dinh nhat)
echo   [*] Dang tai bo FFmpeg tu server du phong (Github / Gyan)...
set "FFMPEG_ZIP=%TEMP%\ffmpeg_package.zip"
if exist "%FFMPEG_ZIP%" del /f /q "%FFMPEG_ZIP%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { (New-Object Net.WebClient).DownloadFile('https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip', '%FFMPEG_ZIP%') } catch { (New-Object Net.WebClient).DownloadFile('https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip', '%FFMPEG_ZIP%') }"

if exist "%FFMPEG_ZIP%" (
    echo   [*] Dang giai nen va cau hinh FFmpeg vao thu muc tools...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%TEMP%\ffmpeg_unpack' -Force; Get-ChildItem -Path '%TEMP%\ffmpeg_unpack' -Recurse -Filter 'ffmpeg.exe' | ForEach-Object { Copy-Item -Path $_.FullName -Destination 'tools' -Force; Copy-Item -Path $_.FullName -Destination 'tools\ffmpeg\bin' -Force }; Get-ChildItem -Path '%TEMP%\ffmpeg_unpack' -Recurse -Filter 'ffprobe.exe' | ForEach-Object { Copy-Item -Path $_.FullName -Destination 'tools' -Force; Copy-Item -Path $_.FullName -Destination 'tools\ffmpeg\bin' -Force }"
    rmdir /s /q "%TEMP%\ffmpeg_unpack" >nul 2>&1
    del /f /q "%FFMPEG_ZIP%" >nul 2>&1
)

if exist "tools\ffmpeg.exe" (
    echo   [OK] Da cau hinh thanh cong FFmpeg vao thu muc tools!
) else if exist "tools\ffmpeg\bin\ffmpeg.exe" (
    echo   [OK] Da cau hinh thanh cong FFmpeg vao thu muc tools!
) else (
    echo   [!] Khong the tai tu dong do ket noi mang.
    echo   [*] Huong dan: Vui long copy file ffmpeg.exe va ffprobe.exe bo vao thu muc tools/
)

:FFMPEG_DONE
echo.
:: ------------------------------------------------------------------------------
:: BUOC 3: TAO MOI TRUONG AO VENV
:: ------------------------------------------------------------------------------
echo [3/6] Dang thiet lap moi truong ao venv doc lap...

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -c "import sys" >nul 2>&1
    if %errorlevel% equ 0 (
        echo   [OK] Moi truong ao venv hop le va da ton tai san.
        goto :VENV_READY
    )
    echo   [*] Thu muc venv cu bi hong hoac copy tu may khac. Dang khoi tao lai...
    rmdir /s /q "venv" >nul 2>&1
)

"%PY_CMD%" -m venv venv
if %errorlevel% neq 0 (
    echo   [X] Khong the tao thu muc venv! Vui long kiem tra quyen hoac duong dan.
    pause
    exit /b 1
)
echo   [OK] Da khoi tao moi truong ao venv thanh cong.

:VENV_READY
echo.
:: ------------------------------------------------------------------------------
:: BUOC 4: NHAN DIEN GPU & CAI PYTORCH
:: ------------------------------------------------------------------------------
echo [4/6] Dang kiem tra phan cung GPU va PyTorch...
set "HAS_NVIDIA=0"

nvidia-smi >nul 2>&1
if %errorlevel% equ 0 set "HAS_NVIDIA=1"

for /f "tokens=*" %%g in ('powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2^>nul ^| findstr /i "NVIDIA"') do (
    set "HAS_NVIDIA=1"
    echo   [OK] Tim thay card do hoa: %%g
)

call venv\Scripts\activate.bat
echo   [*] Dang nang cap pip...
python -m pip install --upgrade pip >nul 2>&1

if %HAS_NVIDIA% equ 1 (
    echo   [*] May co NVIDIA GPU! Dang cai PyTorch CUDA 12.1 [Tang toc AI tren GPU]...
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
) else (
    echo   [*] Khong phat hien card roi NVIDIA. Dang cai PyTorch CPU chuan...
    pip install torch torchaudio
)

echo.
:: ------------------------------------------------------------------------------
:: BUOC 5: CAI DAT CAC THU VIEN CON LAI
:: ------------------------------------------------------------------------------
echo [5/6] Dang cai dat toan bo goi phu thuoc tu requirements.txt...
pip install -r requirements.txt

echo.
:: ------------------------------------------------------------------------------
:: BUOC 6: TAO PHIM TAT NGOAI DESKTOP
:: ------------------------------------------------------------------------------
echo [6/6] Dang tao phim tat khoi dong ngoai man hinh Desktop...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Review Mat Than.lnk')); $s.TargetPath = '%~dp02_KHOI_DONG.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = 'shell32.dll,14'; $s.Description = 'Khoi dong Studio Review Mat Than'; $s.Save()"
echo   [OK] Da tao phim tat 'Review Mat Than' ngoai Desktop!

echo.
echo ==============================================================================
echo   CHUC MUNG! HE THONG DA DUOC CAI DAT HOAN TAT 100%!
echo ==============================================================================
echo   - Tu nay ban chi can nhan vao bieu tuong 'Review Mat Than' ngoai Desktop
echo     hoac chay file '2_KHOI_DONG.bat' de mo chuong trinh.
echo   - Khi muon tat chuong trinh, chi can dong cua so dong lenh [hoac bam Ctrl+C].
echo ==============================================================================
echo.
set /p START_NOW="Ban co muon khoi dong Studio ngay bay gio khong? [Y/N, mac dinh Y]: "
if /i "%START_NOW%"=="N" goto :END_FINISH

start "" "%~dp02_KHOI_DONG.bat"
exit /b 0

:END_FINISH
echo Da hoan tat cai dat. Tam biet!
pause
exit /b 0
