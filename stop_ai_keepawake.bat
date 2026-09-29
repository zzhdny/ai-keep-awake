@echo off
chcp 65001 >nul
set PID=
for /f "usebackq delims=" %%i in (`type "%~dp0ai_keepawake.pid" 2^>nul`) do set PID=%%i
if "%PID%"=="" (
    echo AI KeepAwake is not running ^(no pid file^).
) else (
    taskkill /f /pid %PID% && echo Stopped AI KeepAwake ^(PID %PID%^).
    del "%~dp0ai_keepawake.pid" >nul 2>&1
)
pause
