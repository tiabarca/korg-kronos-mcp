@echo off
REM Comprobaciones de solo lectura contra el Kronos. Resultado en logs\checks_*.json
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\run_checks.py
timeout /t 8
