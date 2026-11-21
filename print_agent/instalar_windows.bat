@echo off
setlocal EnableDelayedExpansion
title MainchPrintAgent - Instalador
color 0A

echo ==========================================================
echo  MainchPrintAgent - Puente de impresion local (v1.0.0)
echo ==========================================================
echo.

REM --- 1. Verificar Python 3 ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro Python en este equipo.
    echo.
    echo Instala Python 3 desde https://www.python.org/downloads/windows/
    echo IMPORTANTE: marca la casilla "Add python.exe to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VERSION=%%v
echo [OK] Python detectado: !PY_VERSION!
echo.

REM --- 2. Instalar pywin32 (acceso al spooler de Windows) ---
echo [1/3] Instalando pywin32...
python -m pip install --upgrade pywin32
if errorlevel 1 (
    echo [ERROR] No se pudo instalar pywin32. Verifica tu conexion a internet.
    pause
    exit /b 1
)
echo.

REM --- 3. Copiar el agente a LOCALAPPDATA ---
set "AGENT_DIR=%LOCALAPPDATA%\MainchPrintAgent"
set "AGENT_PY=%AGENT_DIR%\mainch_print_agent.py"
echo [2/3] Copiando agente a %AGENT_DIR% ...
if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
copy /Y "%~dp0mainch_print_agent.py" "%AGENT_PY%" >nul
if errorlevel 1 (
    echo [ERROR] No se pudo copiar mainch_print_agent.py.
    pause
    exit /b 1
)
echo.

REM --- 4. Crear el lanzador silencioso en Startup ---
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_PATH=%STARTUP_DIR%\MainchPrintAgent.vbs"
echo [3/3] Registrando autoarranque en Windows...
if not exist "%STARTUP_DIR%" mkdir "%STARTUP_DIR%"

REM pythonw.exe vive junto a python.exe; se resuelve sin depender del PATH.
set "PYTHONW=pythonw.exe"
for /f "delims=" %%p in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYTHONW=%%p"

> "%VBS_PATH%" echo Set shell = CreateObject("WScript.Shell")
>> "%VBS_PATH%" echo shell.Run """%PYTHONW%"" ""%AGENT_PY%""", 0, False

if not exist "%VBS_PATH%" (
    echo [ERROR] No se pudo crear el lanzador de autoarranque.
    pause
    exit /b 1
)
echo.

REM --- 5. Arrancar el agente ahora mismo ---
start "" wscript.exe "%VBS_PATH%"
timeout /t 2 >nul

echo ==========================================================
echo  INSTALACION COMPLETADA
echo ==========================================================
echo.
echo  El agente queda escuchando en http://127.0.0.1:9100
echo  y se iniciara automaticamente con Windows.
echo.
echo  Pasos finales:
echo    1. Abre el POS en Chrome o Edge.
echo    2. Ve a Configuracion ^> Impresora.
echo    3. Pulsa "Detectar Impresoras" y elige tu impresora termica.
echo    4. Pulsa "Imprimir Ticket de Prueba".
echo.
echo  Si Windows Firewall pregunta, permite el acceso solo en redes privadas.
echo.
pause
endlocal
