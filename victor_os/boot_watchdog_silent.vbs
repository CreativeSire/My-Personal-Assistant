Dim WshShell, sVictorDir, sPython, sScript
WshShell = CreateObject("WScript.Shell")
sVictorDir = "C:\Users\HomePC\Desktop\My Personal Assistant\victor_os"
sPython = sVictorDir & "\..\victor_os_env\Scripts\python.exe"
sScript = sVictorDir & "\victor_watchdog.py"
WshShell.Run chr(34) & sPython & chr(34) & " " & chr(34) & sScript & chr(34), 0
Set WshShell = Nothing
