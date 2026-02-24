@echo off
setlocal

set ROOT=C:\Users\HomePC\Desktop\My Personal Assistant
set VOS=%ROOT%\victor_os
set LOGDIR=%ROOT%\docs\reports
set PY=%ROOT%\victor_os_env\Scripts\python.exe

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%ROOT%"

echo [phase23] Starting agent API...
start "Victor API" cmd /c set APP_ENV=prod ^&^& "%PY%" agent_framework.py 1>>"%LOGDIR%\agent_framework.out.log" 2>>"%LOGDIR%\agent_framework.err.log"

echo [phase23] Starting Telegram server...
start "Victor Telegram" cmd /c set TELEGRAM_SINGLETON_DISABLE=1 ^&^& cd /d "%VOS%" ^&^& "%PY%" telegram_server.py 1>>"%LOGDIR%\telegram_server.out.log" 2>>"%LOGDIR%\telegram_server.err.log"

echo [phase23] Starting Desktop server...
start "Victor Desktop" cmd /c cd /d "%VOS%" ^&^& "%PY%" desktop_server.py 1>>"%LOGDIR%\desktop_server.out.log" 2>>"%LOGDIR%\desktop_server.err.log"

echo [phase23] Services launched.
endlocal
