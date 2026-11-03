@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Primera vez: se instalara lo necesario.
  call "%~dp0INSTALAR.bat"
  if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
