@echo off
setlocal EnableExtensions
rem Se copia a TEMP y se ejecuta desde ahi: asi puede reemplazarse a si mismo sin romperse.
if /i not "%~1"=="--desde-temp" (
  copy /y "%~f0" "%TEMP%\altiplano_actualizar.bat" >nul
  "%TEMP%\altiplano_actualizar.bat" --desde-temp "%~dp0"
  exit /b
)
set "APP=%~2"
cd /d "%APP%"
title Actualizar - Altiplano al Dia
echo.
echo  ==========================================
echo    ALTIPLANO AL DIA - Actualizar
echo  ==========================================
echo.
echo  Se descarga la version nueva desde GitHub. Se conservan tus datos:
echo  .env (API keys), data\ (noticias, diseno, avisos), fb_profile\ (sesion de Facebook),
echo  pdfs\, config.json y noticias.xlsx.
echo.

rem --- 1. cerrar la app si esta abierta (lanzador del .venv y su proceso hijo)
powershell -NoProfile -Command "$ps = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\"; $ps | Where-Object { $_.CommandLine -like '*main.py*' -and $_.ExecutablePath -like '%APP%*' } | ForEach-Object { $id = $_.ProcessId; $ps | Where-Object { $_.ParentProcessId -eq $id } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Stop-Process -Id $id -Force }" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='msedge.exe'\" | Where-Object { $_.CommandLine -like '*%APP%fb_profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

rem --- 2a. instalado con Git: git pull
if not exist ".git" goto zip
where git >nul 2>&1
if errorlevel 1 goto zip
echo [1/2] Descargando cambios con Git ...
rem si ACTUALIZAR.bat se bajo suelto (no versionado), se quita para que git lo traiga
git ls-files --error-unmatch ACTUALIZAR.bat >nul 2>&1
if errorlevel 1 del /q ACTUALIZAR.bat >nul 2>&1
rem config.json lo cambia la app (paginas, intervalo): se aparta, se actualiza y se repone
copy /y config.json "%TEMP%\altiplano_config.json" >nul
git checkout -- config.json >nul 2>&1
git pull --ff-only
set "FALLO=%errorlevel%"
copy /y "%TEMP%\altiplano_config.json" config.json >nul
if "%FALLO%"=="0" goto deps
echo  ERROR: git pull fallo. Si cambiaste archivos del programa a mano, avisa antes de forzar.
pause
exit /b 1

:zip
rem --- 2b. instalado desde el ZIP: se baja el ZIP y se copian solo los archivos del programa
echo [1/2] Descargando la version nueva (ZIP de GitHub) ...
set "TMPZ=%TEMP%\altiplano_actualizar"
if exist "%TMPZ%" rmdir /s /q "%TMPZ%"
mkdir "%TMPZ%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://github.com/Canazachyub/altiplano-al-dia/archive/refs/heads/main.zip' -OutFile '%TMPZ%\main.zip'; Expand-Archive '%TMPZ%\main.zip' '%TMPZ%' -Force"
if not exist "%TMPZ%\altiplano-al-dia-main\main.py" goto error_zip
rem robocopy copia el programa sin tocar los datos del usuario
robocopy "%TMPZ%\altiplano-al-dia-main" "%APP%." /E /NFL /NDL /NJH /NJS /NP /XD .venv python data fb_profile pdfs .git /XF .env config.json noticias*.xlsx >nul
if errorlevel 8 goto error_copia
if not exist "config.json" copy "%TMPZ%\altiplano-al-dia-main\config.json" "config.json" >nul
rmdir /s /q "%TMPZ%"

:deps
rem --- 3. dependencias nuevas (si las hay) y abrir la app
echo [2/2] Revisando dependencias ...
if not exist ".venv\Scripts\python.exe" goto instalar
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
copy /y requirements.txt ".venv\requirements.instalado" >nul
goto fin

:instalar
call "%APP%INSTALAR.bat"

:fin
echo.
echo  Listo: la app quedo actualizada.
echo.
if not defined NO_ABRIR start "" "%APP%Iniciar Monitor.bat"
if not defined NO_PAUSA timeout /t 4 >nul
exit /b 0

:error_zip
echo  ERROR: no se pudo descargar la actualizacion. Revisa la conexion a internet.
pause
exit /b 1

:error_copia
echo  ERROR: no se pudieron copiar los archivos nuevos.
pause
exit /b 1
