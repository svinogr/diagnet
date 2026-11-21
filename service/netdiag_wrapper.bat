@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem ═══════════ НАСТРОЙКИ ═══════════
rem Путь к ВАШЕМУ батнику с диагностикой.
rem По умолчанию ищется файл diag.bat рядом с этим скриптом.
set "DIAG=%~dp0diag.bat"
rem Файл с результатами в формате Markdown
if not exist "%~dp0..\data" mkdir "%~dp0..\data"
set "MDLOG=%~dp0../data/netdiag_results.md"
rem ══════════════════════════════════

if not exist "%DIAG%" (
    echo [netdiag_wrapper] Не найден файл диагностики: %DIAG% 1>&2
    pause
    exit /b 1
)

echo Диагностика запущена. Вывод скрыт - он попадет в отчет.
echo Окно закроется автоматически через ~2 минуты.
echo Отчет: %MDLOG%
echo.

rem Имена переменных ниже уникальны, чтобы диагностируемый
rem батник не перезаписал их через "set" (переменные общие).
set "ND_TMP=%TEMP%\netdiag_%RANDOM%%RANDOM%.tmp"

call "%DIAG%" > "%ND_TMP%" 2>&1
set "ND_RC=%ERRORLEVEL%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0append_to_md.ps1" -Log "%MDLOG%" -Tmp "%ND_TMP%" -ExitCode %ND_RC%

del /q "%ND_TMP%" 2>nul

echo.
if %ND_RC%==0 (echo Готово. Результат: OK) else (echo Готово. Результат: ОШИБКА, код %ND_RC%)
echo Запись добавлена в: %MDLOG%
%SystemRoot%\System32\timeout.exe /t 5 >nul

exit /b %ND_RC%
