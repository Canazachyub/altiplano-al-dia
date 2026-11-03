@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Primera vez: se instalara lo necesario.
  call "%~dp0INSTALAR.bat"
  if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)
rem Si requirements.txt cambio (por ejemplo tras actualizar desde GitHub), instala lo que falte.
fc /b requirements.txt ".venv\requirements.instalado" >nul 2>&1
if errorlevel 1 (
  echo Actualizando dependencias...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
  if not errorlevel 1 copy /y requirements.txt ".venv\requirements.instalado" >nul
)
start "" ".venv\Scripts\pythonw.exe" main.py
