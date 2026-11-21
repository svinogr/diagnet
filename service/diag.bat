@echo off
chcp 65001 >nul
title Network Diagnostics
set LOG=%TEMP%\netdiag_full.log
set FAIL=0
set /a N=0

rem Параметры из config.bat (создаётся дашбордом). Значения по умолчанию ниже.
call "%~dp0config.bat" 2>nul

if not defined PING_COUNT set "PING_COUNT=30"
if not defined GW_MODE set "GW_MODE=auto"
if not defined GW_FIXED set "GW_FIXED=192.168.0.1"
if not defined DO_PING_ROUTER set "DO_PING_ROUTER=1"
if not defined DO_PING_8888 set "DO_PING_8888=1"
if not defined DO_NSLOOKUP set "DO_NSLOOKUP=1"
if not defined NSLOOKUP_HOST set "NSLOOKUP_HOST=ya.ru"
if not defined DO_CURL1 set "DO_CURL1=1"
if not defined CURL1_URL set "CURL1_URL=https://ya.ru"
if not defined DO_CURL2 set "DO_CURL2=1"
if not defined CURL2_URL set "CURL2_URL=https://www.google.com"
if not defined DO_DNS_UDP set "DO_DNS_UDP=1"
if not defined DNS1 set "DNS1=8.8.8.8"
if not defined DNS2 set "DNS2=77.88.8.8"
if not defined DO_DNS_TCP set "DO_DNS_TCP=1"
if not defined DNS_TCP_SERVER set "DNS_TCP_SERVER=8.8.8.8"
if not defined DO_TRACERT set "DO_TRACERT=1"
if not defined TRACE_HOST set "TRACE_HOST=ya.ru"
if not defined TRACE_HOPS set "TRACE_HOPS=15"

rem Роутер: из настроек или автоопределение основного шлюза.
set "GW=%GW_FIXED%"
if /i "%GW_MODE%"=="auto" call :detect_gw

echo ============================================================
echo   Network diagnostics - %DATE% %TIME%
echo   Router: %GW%
echo ============================================================
echo.
echo Running tests, please wait...
echo.

echo ============================================================ > "%LOG%"
echo   Network diagnostics - %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo. >> "%LOG%"

if "%DO_PING_ROUTER%"=="1" call :ping_router
if "%DO_PING_8888%"=="1" call :ping_8888
if "%DO_NSLOOKUP%"=="1" call :do_nslookup
if "%DO_CURL1%"=="1" call :do_curl1
if "%DO_CURL2%"=="1" call :do_curl2
if "%DO_DNS_UDP%"=="1" call :do_dns_udp
if "%DO_DNS_TCP%"=="1" call :do_dns_tcp
if "%DO_TRACERT%"=="1" call :do_tracert

echo ============================================================ >> "%LOG%"
echo   End of tests - %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"

type "%LOG%"
exit /b %FAIL%

rem ============ Проверки (по одной подпрограмме) ============
rem Значения из config.bat передаются в PowerShell через $env:ИМЯ,
rem а не вставляются в текст команды - иначе кавычка в значении
rem превращается в выполнение произвольного кода.

:detect_gw
for /f "usebackq delims=" %%G in (`powershell -NoProfile -Command "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop"`) do set "GW=%%G"
exit /b 0

:ping_router
set /a N+=1
echo --- %N%. PING router (%GW%) - %PING_COUNT% packets --- >> "%LOG%"
ping -n %PING_COUNT% %GW% >> "%LOG%"
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:ping_8888
set /a N+=1
echo --- %N%. PING 8.8.8.8 - %PING_COUNT% packets --- >> "%LOG%"
ping -n %PING_COUNT% 8.8.8.8 >> "%LOG%"
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:do_nslookup
set /a N+=1
echo --- %N%. DNS lookup nslookup "%NSLOOKUP_HOST%" (system resolver) --- >> "%LOG%"
nslookup "%NSLOOKUP_HOST%" >> "%LOG%" 2>&1
echo. >> "%LOG%"
exit /b 0

:do_curl1
set /a N+=1
echo --- %N%. Connect time to %CURL1_URL% --- >> "%LOG%"
curl -o NUL -s -w "connect: %%{time_connect}s tls: %%{time_appconnect}s total: %%{time_total}s\n" "%CURL1_URL%" >> "%LOG%"
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:do_curl2
set /a N+=1
echo --- %N%. Connect time to %CURL2_URL% --- >> "%LOG%"
curl -o NUL -s -w "connect: %%{time_connect}s tls: %%{time_appconnect}s total: %%{time_total}s\n" "%CURL2_URL%" >> "%LOG%"
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:do_dns_udp
set /a N+=1
echo --- %N%. DNS/UDP direct to %DNS1% --- >> "%LOG%"
powershell -NoProfile -Command "$h = $env:NSLOOKUP_HOST; $s = $env:DNS1; $r = Resolve-DnsName $h -Server $s -Type A -DnsOnly -ErrorAction SilentlyContinue; if ($r) { Write-Output ('DNS/UDP ' + $s + ': OK -> ' + $r[0].IPAddress) } else { Write-Output ('DNS/UDP ' + $s + ': TIMEOUT/FAIL'); exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
set /a N+=1
echo --- %N%. DNS/UDP direct to %DNS2% --- >> "%LOG%"
powershell -NoProfile -Command "$h = $env:NSLOOKUP_HOST; $s = $env:DNS2; $r = Resolve-DnsName $h -Server $s -Type A -DnsOnly -ErrorAction SilentlyContinue; if ($r) { Write-Output ('DNS/UDP ' + $s + ': OK -> ' + $r[0].IPAddress) } else { Write-Output ('DNS/UDP ' + $s + ': TIMEOUT/FAIL'); exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:do_dns_tcp
set /a N+=1
echo --- %N%. DNS/TCP direct to %DNS_TCP_SERVER% (contrast) --- >> "%LOG%"
powershell -NoProfile -Command "$h = $env:NSLOOKUP_HOST; $s = $env:DNS_TCP_SERVER; $r = Resolve-DnsName $h -Server $s -Type A -TcpOnly -ErrorAction SilentlyContinue; if ($r) { Write-Output ('DNS/TCP ' + $s + ': OK -> ' + $r[0].IPAddress) } else { Write-Output ('DNS/TCP ' + $s + ': TIMEOUT/FAIL'); exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
echo. >> "%LOG%"
exit /b 0

:do_tracert
set /a N+=1
echo --- %N%. Tracert to "%TRACE_HOST%" --- >> "%LOG%"
tracert -d -h %TRACE_HOPS% "%TRACE_HOST%" >> "%LOG%"
echo. >> "%LOG%"
exit /b 0
