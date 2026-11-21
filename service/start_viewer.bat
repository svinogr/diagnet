@echo off
chcp 65001 >nul
rem Запуск веб-дашборда и открытие его в браузере
call "%~dp0start_viewer_silent.bat"
if errorlevel 2 (
    echo.
    pause
    exit /b 2
)
%SystemRoot%\System32\timeout.exe /t 2 >nul
start "" "http://127.0.0.1:8471/"
