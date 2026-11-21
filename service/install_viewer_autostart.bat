@echo off
chcp 65001 >nul
rem Автозапуск дашборда при входе в Windows.
rem Запустите этот файл ОТ ИМЕНИ АДМИНИСТРАТОРА.
rem При входе на секунду мелькнёт консольное окно - это нормально.
schtasks /Create /F /TN "NetDiagViewer" /TR "\"%~dp0start_viewer_silent.bat\"" /SC ONLOGON

if %ERRORLEVEL%==0 (
    echo.
    echo Задача "NetDiagViewer" создана: дашборд будет стартовать при входе.
    echo Адрес: http://127.0.0.1:8471/
) else (
    echo.
    echo ОШИБКА создания задачи. Запустили файл от имени администратора?
)
pause
