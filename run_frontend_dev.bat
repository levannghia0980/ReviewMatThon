@echo off
chcp 65001 >nul
title REACT FRONTEND DEV - REVIEW MAT THON
cd /d "%~dp0\frontend"

echo ======================================================================
echo   ⚡ REACT STUDIO UI DEV SERVER (PORT 3000)
echo   Giao dien Live Hot-Reload: http://localhost:3000
echo   API Backend Proxy:         http://127.0.0.1:8000
echo ======================================================================
echo.

npm run dev
if %errorlevel% neq 0 (
    echo [THONG BAO] React dev server bi dung.
    pause
)
exit
