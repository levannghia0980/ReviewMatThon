@echo off
chcp 65001 >nul
title DONG GOI CHUYEN GIAO - REVIEW MAT THAN
cd /d "%~dp0"

echo ==============================================================================
echo        DONG GOI TU DONG BAN CHUYEN GIAO REVIEW MAT THAN [1-CLICK]
echo ==============================================================================
echo.
echo He thong se tu dong:
echo  1. Giu nguyen toan bo file .env (API Keys), CSDL Database, Giao dien React da build.
echo  2. Tu dong loai bo cac thu muc rac / trung lap (venv cu, node_modules).
echo  3. Tao file 'ReviewMatThon_FULL_1CLICK.zip' tai o dia goc de ban gui di.
echo ==============================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\pack.ps1"

echo.
pause
