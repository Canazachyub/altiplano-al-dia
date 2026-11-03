@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Instalador - Altiplano al Dia
echo.
echo  ==========================================
echo    ALTIPLANO AL DIA - Instalacion
echo  ==========================================
echo.

set "PYVER=3.12.10"
set "PYDIR=%~dp0python"
set "PYEXE=%PYDIR%\python.exe"
set "BASEPY="

rem --- 1. Python: el local del proyecto, uno ya instalado (3.11-3.13) o se descarga
if exist "%PYEXE%" set "BASEPY=%PYEXE%"
if not defined BASEPY (
  for %%V in (3.12 3.13 3.11) do (
    if not defined BASEPY (
      py -%%V -c "import tkinter" >nul 2>&1 && set "BASEPY=py -%%V"
    )
  )
)
if defined BASEPY (
  echo [1/4] Python encontrado: %BASEPY%
  goto venv
)

echo [1/4] No hay Python. Descargando Python %PYVER% desde python.org ...
set "INST=%TEMP%\python-%PYVER%-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe' -OutFile '%INST%'"
if not exist "%INST%" (
  echo  ERROR: no se pudo descargar Python. Revisa la conexion a internet.
  pause & exit /b 1
)
echo       Instalando Python en "%PYDIR%" (sin permisos de administrador) ...
"%INST%" /quiet InstallAllUsers=0 TargetDir="%PYDIR%" PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0 Include_tcltk=1 Include_pip=1
del "%INST%" >nul 2>&1
if not exist "%PYEXE%" (
  echo  ERROR: la instalacion de Python fallo.
  pause & exit /b 1
)
set "BASEPY=%PYEXE%"

:venv
rem --- 2. Entorno virtual + dependencias
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creando entorno virtual ...
  if "%BASEPY:~0,3%"=="py " (%BASEPY% -m venv .venv) else ("%BASEPY%" -m venv .venv)
)
if not exist ".venv\Scripts\python.exe" (
  echo  ERROR: no se pudo crear el entorno virtual.
  pause & exit /b 1
)
echo [3/4] Instalando dependencias (puede tardar unos minutos) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
  echo  ERROR: fallo la instalacion de dependencias.
  pause & exit /b 1
)
copy /y requirements.txt ".venv\requirements.instalado" >nul

rem --- 3. Navegador: Chrome o Edge (viene con Windows). Si no hay ninguno, Chromium de Playwright.
set "HAYNAV="
for %%P in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do (
  if exist %%P set "HAYNAV=1"
)
if not defined HAYNAV (
  echo [4/4] No hay Chrome ni Edge: descargando Chromium para Playwright ...
  ".venv\Scripts\python.exe" -m playwright install chromium
) else (
  echo [4/4] Navegador encontrado (Chrome/Edge^).
)

if not exist ".env" copy ".env.example" ".env" >nul

rem --- acceso directo en el escritorio
if not defined NO_ACCESO_DIRECTO powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Altiplano al Dia.lnk'); $s.TargetPath='%~dp0Iniciar Monitor.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\imageres.dll,186'; $s.WindowStyle=7; $s.Save()" >nul 2>&1

echo.
echo  Listo. Abre la app con "Iniciar Monitor.bat" o el acceso directo del escritorio.
echo  Primer uso: pestana Configuracion ^> pega las API keys ^> "Iniciar sesion en Facebook".
echo.
if not defined NO_PAUSA pause
