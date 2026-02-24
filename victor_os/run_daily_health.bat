@echo off
cd /d "C:\Users\HomePC\Desktop\My Personal Assistant"
for /f "tokens=1,2 delims==" %%a in ('findstr /B "VICTOR_API_KEY=" victor_os\.env') do set %%a=%%b
python victor_os\ops_health_monitor.py
