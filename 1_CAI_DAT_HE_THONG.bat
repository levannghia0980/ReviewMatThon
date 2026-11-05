@echo off
chcp 65001 >nul
title [CAI DAT HE THONG] STUDIO MAT THON REVIEW
cd /d "%~dp0"

echo ==============================================================================
echo        STUDIO MAT THON REVIEW - BO CAI DAT HE THONG TU DONG 1-CLICK
echo ==============================================================================
echo  Quy trinh tu dong thuc hien:
echo   1. Khoi tao file cau hinh .env tu mau
echo   2. Kiem tra Python [Chuan Python 3.10 - 3.11 cho cac mo hinh AI]
echo   3. Kiem tra FFmpeg ^& FFprobe [Tu dong tai vao thu muc tools]
echo   4. Khoi tao moi truong ao doc lap venv
echo   5. Nhan dien GPU NVIDIA / CPU de cai PyTorch toi uu
echo   6. Cai dat toan bo thu vien can thiet tu requirements.txt
echo   7. Cai dat trinh duyet Chromium cho Playwright (Dich thuat Gemini Web)
echo   8. Tao phim tat khoi dong ngoai man hinh Desktop
echo ==============================================================================
echo.

:: ------------------------------------------------------------------------------
:: BUOC 0: KHOI TAO FILE CAU HINH .ENV
:: ------------------------------------------------------------------------------
if not exist ".env" (
    if exist ".env.example" (
        echo [*] Dang tao file cau hinh .env tu mau .env.example...
        copy ".env.example" ".env" >nul
        echo   [OK] Da tao file .env mac dinh.
    )
)

:: ------------------------------------------------------------------------------
:: BUOC 1: KIEM TRA & CAI DAT PYTHON (YEU CAU CHUAN PYTHON 3.10 HOAC 3.11)
:: ------------------------------------------------------------------------------
echo [1/7] Dang kiem tra Python tren may tinh...
set "PY_CMD="

:: 1. Kiem tra lenh python he thong xem co phai ban 3.10 hoac 3.11 khong
python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 11) else 1)" >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_READY
)

:: 2. Kiem tra trinh khoi chay py voi phien ban 3.11
py -3.11 -c "import sys; sys.exit(0)" >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3.11"
    goto :PYTHON_READY
)

:: 3. Kiem tra duong dan cai dat Python 3.11 tieu chuan tren Windows
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

:: Neu may chua co Python 3.11 hoac dang dung Python 3.12+ (khong tuong thich DeepFilterNet)
echo   [!] May chua co Python 3.11 (hoac dang dung ban 3.12+ khong tuong thich thu vien AI).
echo   [*] Dang tu dong tai bo cai Python 3.11.9 chinh thuc (x64) tu python.org...
set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"

curl.exe -# -L -o "%PY_INSTALLER%" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
if not exist "%PY_INSTALLER%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', '%PY_INSTALLER%')"
)

if not exist "%PY_INSTALLER%" (
    echo   [X] Khong the tai Python installer. Vui long kiem tra ket noi mang.
    pause
    exit /b 1
)

echo   [*] Dang tu dong cai dat Python 3.11.9 [Vui long doi 30-60 giay]...
"%PY_INSTALLER%" /passive PrependPath=1 Include_pip=1 SimpleInstall=1 Include_test=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
del /f /q "%PY_INSTALLER%" >nul 2>&1

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
) else (
    set "PY_CMD=python"
)

:PYTHON_READY
for /f "tokens=*" %%v in ('"%PY_CMD%" --version 2^>^&1') do echo   [OK] Xac nhan Python san sang: %%v

echo.
:: ------------------------------------------------------------------------------
:: BUOC 2: KIEM TRA & CAI DAT FFMPEG ^& FFPROBE VAO THU MUC TOOLS
:: ------------------------------------------------------------------------------
echo [2/7] Dang kiem tra bo cong cu video (FFmpeg, FFprobe) trong tools/...
if not exist "tools" mkdir "tools" >nul 2>&1
if not exist "tools\ffmpeg" mkdir "tools\ffmpeg" >nul 2>&1
if not exist "tools\ffmpeg\bin" mkdir "tools\ffmpeg\bin" >nul 2>&1

set "NEED_DOWNLOAD_FFMPEG=0"
if not exist "tools\ffmpeg.exe" (
    if not exist "tools\ffmpeg\bin\ffmpeg.exe" set "NEED_DOWNLOAD_FFMPEG=1"
)
if not exist "tools\ffprobe.exe" (
    if not exist "tools\ffmpeg\bin\ffprobe.exe" set "NEED_DOWNLOAD_FFMPEG=1"
)

if %NEED_DOWNLOAD_FFMPEG% equ 0 (
    echo   [OK] Bo cong cu FFmpeg va FFprobe da co san trong thu muc tools!
    goto :FFMPEG_DONE
)

echo   [*] Dang tai bo cong cu FFmpeg ^& FFprobe (Gyan Essentials Build)...
set "FFMPEG_ZIP=%TEMP%\ffmpeg_package.zip"
if exist "%FFMPEG_ZIP%" del /f /q "%FFMPEG_ZIP%" >nul 2>&1

:: Ưu tiên 1: Tải bằng curl.exe có hiển thị thanh % tiến trình trực quan
curl.exe -# -L -o "%FFMPEG_ZIP%" "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

:: Dự phòng: Nếu gyan.dev lỗi, tải từ mirror BtbN GitHub
if not exist "%FFMPEG_ZIP%" (
    echo   [*] Thu tai lai tu mirror du phong (GitHub Builds)...
    curl.exe -# -L -o "%FFMPEG_ZIP%" "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)

if exist "%FFMPEG_ZIP%" (
    echo   [*] Dang giai nen va cau hinh FFmpeg ^& FFprobe vao tools/...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%TEMP%\ffmpeg_unpack' -Force; Get-ChildItem -Path '%TEMP%\ffmpeg_unpack' -Recurse -Filter 'ffmpeg.exe' | ForEach-Object { Copy-Item -Path $_.FullName -Destination 'tools' -Force; Copy-Item -Path $_.FullName -Destination 'tools\ffmpeg\bin' -Force }; Get-ChildItem -Path '%TEMP%\ffmpeg_unpack' -Recurse -Filter 'ffprobe.exe' | ForEach-Object { Copy-Item -Path $_.FullName -Destination 'tools' -Force; Copy-Item -Path $_.FullName -Destination 'tools\ffmpeg\bin' -Force }"
    rmdir /s /q "%TEMP%\ffmpeg_unpack" >nul 2>&1
    del /f /q "%FFMPEG_ZIP%" >nul 2>&1
)

if exist "tools\ffmpeg.exe" (
    echo   [OK] Da cau hinh thanh cong FFmpeg vao thu muc tools!
) else if exist "tools\ffmpeg\bin\ffmpeg.exe" (
    echo   [OK] Da cau hinh thanh cong FFmpeg vao thu muc tools!
) else (
    echo   [!] Chua the tai FFmpeg tu dong (he thong se tu dong truyen qua imageio-ffmpeg o Buoc 5).
)

:FFMPEG_DONE
echo.
:: ------------------------------------------------------------------------------
:: BUOC 3: TAO MOI TRUONG AO VENV
:: ------------------------------------------------------------------------------
echo [3/7] Dang thiet lap moi truong ao venv doc lap...

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
echo [4/7] Dang kiem tra phan cung GPU va PyTorch...
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
:: BUOC 5: CAI DAT CAC THU VIEN TU REQUIREMENTS.TXT
:: ------------------------------------------------------------------------------
echo [5/7] Dang cai dat toan bo goi phu thuoc tu requirements.txt...
pip install -r requirements.txt
python -c "import hashlib, pathlib; p = pathlib.Path('requirements.txt'); pathlib.Path('.pip_hash').write_text(hashlib.md5(p.read_bytes()).hexdigest(), encoding='utf-8') if p.exists() else None" >nul 2>&1

:: Trích xuất tự động FFmpeg từ imageio-ffmpeg nếu tools vẫn chưa có
if not exist "tools\ffmpeg.exe" (
    echo   [*] Dang tu dong trích xuất FFmpeg tu goi imageio-ffmpeg vao tools/...
    python -c "import shutil, pathlib, imageio_ffmpeg; src = imageio_ffmpeg.get_ffmpeg_exe(); dst = pathlib.Path('tools/ffmpeg.exe'); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst) if not dst.exists() else None; dst2 = pathlib.Path('tools/ffmpeg/bin/ffmpeg.exe'); dst2.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst2) if not dst2.exists() else None" >nul 2>&1
    if exist "tools\ffmpeg.exe" echo   [OK] Da trích xuất FFmpeg vao tools thanh cong!
)

echo.
:: ------------------------------------------------------------------------------
:: BUOC 6: CAI DAT TRINH DUYET CHROMIUM CHO PLAYWRIGHT (GEMINI WEB ON-DEMAND)
:: ------------------------------------------------------------------------------
echo [6/7] Dang cai dat trinh duyet Chromium cho Playwright (Dung cho dich thuat)...
python -m playwright install chromium
echo   [OK] Trinh duyet Chromium da san sang!

echo.
:: ------------------------------------------------------------------------------
:: BUOC 7: TAO PHIM TAT NGOAI DESKTOP
:: ------------------------------------------------------------------------------
echo [7/7] Dang tao phim tat khoi dong ngoai man hinh Desktop...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Mat Thon Review.lnk')); $s.TargetPath = '%~dp02_KHOI_DONG.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = 'shell32.dll,14'; $s.Description = 'Khoi dong Studio Mat Thon Review'; $s.Save()"
echo   [OK] Da tao phim tat 'Mat Thon Review' ngoai Desktop!

echo.
echo ==============================================================================
echo   CHUC MUNG! HE THONG DA DUOC CAI DAT HOAN TAT 100%!
echo ==============================================================================
echo   - Tu nay ban chi can nhan vao bieu tuong 'Mat Thon Review' ngoai Desktop
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
