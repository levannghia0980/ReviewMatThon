@echo off
chcp 65001 >nul
title TAO PHIM TAT REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        REVIEW MAT THAN - TAO PHIM TAT NGOAI MAN HINH DESKTOP
echo ==============================================================================
echo.
echo Dang tao phim tat 'Review Mat Than' ngoai Desktop...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Review Mat Than.lnk')); $s.TargetPath = '%~dp02_KHOI_DONG.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = 'shell32.dll,14'; $s.Description = 'Khoi dong Studio Review Mat Than'; $s.Save()"

if %errorlevel% equ 0 (
    echo [OK] Da tao phim tat 'Review Mat Than' ngoai man hinh Desktop thanh cong!
) else (
    echo [!] Khong the tao phim tat tu dong. Ban co the tao shortcut thu cong tu file 2_KHOI_DONG.bat.
)

echo.
pause
