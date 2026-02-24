@echo off
SET LOGFILE="%~dp0boot_log.txt"
SET VICTOR_DIR=%~dp0
echo [%DATE% %TIME%] --- STARTING BOOT SEQUENCE --- > %LOGFILE%
TITLE Victor OS Launcher

cd /d "%VICTOR_DIR%"
echo [%DATE% %TIME%] Dir: %CD% >> %LOGFILE%

:: Activate virtual environment
if exist "..\victor_os_env\Scripts\activate.bat" (
    echo [%DATE% %TIME%] Activating venv... >> %LOGFILE%
    call "..\victor_os_env\Scripts\activate.bat"
) else (
    echo [%DATE% %TIME%] ERROR: venv not found >> %LOGFILE%
    exit /b 1
)

:: Launch Telegram Server
:: The singleton lock in telegram_server.py prevents duplicates automatically.
:: The watchdog handles crash recovery after this.
echo [%DATE% %TIME%] Starting Telegram Server... >> %LOGFILE%
start "Victor Telegram" python telegram_server.py

:: Give telegram_server time to start and write its PID file before watchdog checks
timeout /t 5 /nobreak > nul

:: Launch Watchdog — handles crash recovery from here on
echo [%DATE% %TIME%] Starting Watchdog... >> %LOGFILE%
start "Victor Watchdog" python victor_watchdog.py

:: Launch Dashboard
echo [%DATE% %TIME%] Starting Dashboard... >> %LOGFILE%
start "Victor Dashboard" streamlit run dashboard.py

echo [%DATE% %TIME%] --- BOOT COMPLETE --- >> %LOGFILE%
exit
