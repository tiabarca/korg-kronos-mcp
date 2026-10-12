@echo off
rem Termina TODOS los procesos del server kronos-mcp (tambien los colgados).
rem Usalo con la app de Claude cerrada; al abrirla arrancara un server limpio.
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match kronos[-_]mcp -and $_.ProcessId -ne $PID }; if ($p) { $p | ForEach-Object { Write-Host (Terminando PID  + $_.ProcessId +   + $_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host No hay procesos kronos-mcp }"
if exist "%~dp0..\logs\pids" del /q "%~dp0..\logs\pids\*.json" 2>nul
pause
