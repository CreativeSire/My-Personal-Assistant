@echo off
set "TASK_NAME=VictorOS_AutoBoot"
set "SCRIPT_PATH=C:\Users\HomePC\Desktop\My Personal Assistant\victor_os\boot_victor_silent.vbs"

echo Registering Victor-OS Auto-Boot Task...
schtasks /create /tn "%TASK_NAME%" /tr "wscript.exe \"%SCRIPT_PATH%\"" /sc onlogon /rl highest /f

if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Victor-OS will now launch silently on login.
) else (
    echo [FAILED] Please run this batch file as Administrator manually.
)
timeout /t 5
