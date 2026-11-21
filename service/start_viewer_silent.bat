@echo off
chcp 65001 >nul
rem Старт сервера дашборда (без открытия браузера).
rem Если дашборд уже работает - не запускаем вторую копию
%SystemRoot%\System32\curl.exe -s -o nul --max-time 2 http://127.0.0.1:8471/ 2>nul
if not errorlevel 1 exit /b 0
rem Python ищется: PATH -> типичные папки -> python\ рядом (скачан ранее) -> авто-загрузка.

set "PYEXE="

rem 1. pythonw.exe в PATH
for %%P in (pythonw.exe) do if not defined PYEXE set "PYEXE=%%~$PATH:P"

rem 2. типичные папки установки
if not defined PYEXE if exist "C:\Python314\pythonw.exe" set "PYEXE=C:\Python314\pythonw.exe"
if not defined PYEXE if exist "C:\Python313\pythonw.exe" set "PYEXE=C:\Python313\pythonw.exe"
if not defined PYEXE if exist "C:\Python312\pythonw.exe" set "PYEXE=C:\Python312\pythonw.exe"
if not defined PYEXE if exist "C:\Python311\pythonw.exe" set "PYEXE=C:\Python311\pythonw.exe"
if not defined PYEXE if exist "C:\Python310\pythonw.exe" set "PYEXE=C:\Python310\pythonw.exe"

rem 3. портативная копия, скачанная ранее в папку python\
if not defined PYEXE if exist "%~dp0python\pythonw.exe" set "PYEXE=%~dp0python\pythonw.exe"
if defined PYEXE goto run

rem 4. авто-загрузка портативного Python (~12 МБ, нужен интернет один раз)
echo Python не найден. Скачиваю портативную копию ~12 МБ, нужен интернет...
set "PYZIP=%TEMP%\pyembed_%RANDOM%.zip"
curl -fsSL -o "%PYZIP%" "https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip" 2>nul
if errorlevel 1 goto dlfail
set "PYEXPECTED=615861FB801E8B04C847598DB4E1E46E4B046295017CAA37CB5486DDE72B5865"
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '%PYZIP%').Hash"`) do set "PYHASH=%%H"
if /i not "%PYHASH%"=="%PYEXPECTED%" (
    echo Скачанный файл не прошёл проверку контрольной суммы - прерываю.
    del /q "%PYZIP%" 2>nul
    exit /b 2
)
mkdir "%~dp0python" 2>nul
tar -xf "%PYZIP%" -C "%~dp0python" 2>nul
del /q "%PYZIP%" 2>nul
if not exist "%~dp0python\pythonw.exe" goto dlfail
set "PYEXE=%~dp0python\pythonw.exe"
echo Готово, портативный Python сохранён в папке python\
goto run

:dlfail
del /q "%PYZIP%" 2>nul
echo.
echo Не удалось автоматически подготовить Python.
echo Установите его с https://python.org, отметив "Add python.exe to PATH",
echo или проверьте интернет и запустите этот файл снова.
exit /b 2

:run
start "" "%PYEXE%" "%~dp0viewer.py"
exit /b 0
