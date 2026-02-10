@echo off
SET LOGFILE="%~dp0boot_log.txt"
echo [%DATE% %TIME%] --- STARTING BOOT SEQUENCE --- > %LOGFILE%
TITLE Victor OS Launcher 🚀

:: 1. Navigate to the project folder
cd /d "%~dp0"
echo [%DATE% %TIME%] Navigated to %CD% >> %LOGFILE%

:: 2. Activate the Brain (Virtual Environment)
if exist "..\victor_os_env\Scripts\activate.bat" (
    echo [%DATE% %TIME%] Activating Venv... >> %LOGFILE%
    call "..\victor_os_env\Scripts\activate.bat"
) else (
    echo [%DATE% %TIME%] ❌ Venv not found at ..\victor_os_env\Scripts\activate.bat >> %LOGFILE%
    exit /b
)

:: 3. Smart Launch Logic
echo [%DATE% %TIME%] Scanning for existing instances... >> %LOGFILE%

:: Check for Telegram Server
tasklist /V /FI "IMAGENAME eq python.exe" | find /I "telegram_server.py" > nul
if errorlevel 1 (
    echo [%DATE% %TIME%] 🚀 Waking up Telegram Server... >> %LOGFILE%
    start "Victor Telegram" python telegram_server.py
) else (
    echo [%DATE% %TIME%] ✅ Telegram Server already running. >> %LOGFILE%
)

:: Always launch Dashboard
echo [%DATE% %TIME%] 🖥️ Launching Dashboard... >> %LOGFILE%
start "Victor Dashboard" streamlit run dashboard.py

echo [%DATE% %TIME%] --- BOOT COMPLETE --- >> %LOGFILE%
exit
