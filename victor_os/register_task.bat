@echo off
:: Victor-OS Task Scheduler Registration
:: Run this ONCE as Administrator. Registers both AutoBoot and Watchdog tasks.

set "VICTOR_DIR=C:\Users\HomePC\Desktop\My Personal Assistant\victor_os"
set "VBS_BOOT=%VICTOR_DIR%\boot_victor_silent.vbs"
set "VBS_WATCHDOG=%VICTOR_DIR%\boot_watchdog_silent.vbs"
set "VENV_PYTHON=%VICTOR_DIR%\..\victor_os_env\Scripts\python.exe"

echo ============================================
echo   Victor-OS Task Scheduler Registration
echo ============================================
echo.

:: --- Task 1: AutoBoot (on login, runs start_victor.bat silently) ---
echo [1/2] Registering VictorOS_AutoBoot...
schtasks /create /tn "VictorOS_AutoBoot" /tr "wscript.exe \"%VBS_BOOT%\"" /sc onlogon /rl highest /f
if %ERRORLEVEL% EQU 0 (
    echo       [OK] VictorOS_AutoBoot registered.
) else (
    echo       [FAIL] VictorOS_AutoBoot failed. Are you running as Administrator?
)

:: --- Task 2: Watchdog (on login, 60s delay, runs watchdog directly) ---
echo [2/2] Registering VictorOS_Watchdog...
schtasks /create /tn "VictorOS_Watchdog" /tr "wscript.exe \"%VBS_WATCHDOG%\"" /sc onlogon /rl highest /delay 0001:00 /f
if %ERRORLEVEL% EQU 0 (
    echo       [OK] VictorOS_Watchdog registered (1 min delay after login).
) else (
    echo       [FAIL] VictorOS_Watchdog failed.
)

echo.
echo Done. Victor will now:
echo   - Launch automatically when you log in (VictorOS_AutoBoot)
echo   - Self-heal if it crashes (VictorOS_Watchdog, starts 1min after login)
echo.
timeout /t 8
