@echo off
chcp 65001 >nul
rem ============================================
rem   Сетевая диагностика - единая точка входа.
rem   Запустите этот файл, всё остальное - в браузере.
rem ============================================

rem При первом запуске создаём ярлык с иконкой
if not exist "%~dp0Сетевая диагностика.lnk" (
    powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%~dp0Сетевая диагностика.lnk'); $s.TargetPath='%~dp0ЗАПУСК.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0icon.ico,0'; $s.Description='Сетевая диагностика'; $s.Save()" >nul 2>&1
)

rem Запуск сервера дашборда (Python подберётся сам)
call "%~dp0service\start_viewer_silent.bat"
if errorlevel 2 (
    echo.
    echo Дашборд не удалось запустить - смотрите сообщения выше.
    pause
    exit /b 2
)

%SystemRoot%\System32\timeout.exe /t 2 >nul
start "" "http://127.0.0.1:8471/"
