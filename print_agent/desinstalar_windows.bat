@echo off
setlocal EnableDelayedExpansion
title MainchPrintAgent - Desinstalador
color 0C

echo ==========================================================
echo  MainchPrintAgent - Desinstalacion
echo ==========================================================
echo.

set "AGENT_DIR=%LOCALAPPDATA%\MainchPrintAgent"
set "VBS_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MainchPrintAgent.vbs"

REM --- 1. Detener el agente si esta corriendo ---
echo [1/3] Deteniendo el agente...
taskkill /F /IM pythonw.exe >nul 2>&1
if errorlevel 1 (
    echo       No habia procesos pythonw.exe activos.
) else (
    echo       Procesos pythonw.exe finalizados.
)

for /f "tokens=2 delims=," %%p in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2^>nul') do (
    wmic process where "ProcessId=%%~p" get CommandLine 2>nul | find /I "mainch_print_agent.py" >nul && (
        taskkill /F /PID %%~p >nul 2>&1
        echo       Agente detenido (PID %%~p).
    )
)
echo.

REM --- 2. Eliminar el autoarranque ---
echo [2/3] Eliminando el autoarranque...
if exist "%VBS_PATH%" (
    del /F /Q "%VBS_PATH%" >nul
    echo       Lanzador de Startup eliminado.
) else (
    echo       No habia lanzador de Startup.
)
echo.

REM --- 3. Eliminar los archivos del agente ---
echo [3/3] Eliminando archivos del agente...
if exist "%AGENT_DIR%" (
    rmdir /S /Q "%AGENT_DIR%" >nul
    echo       Carpeta %AGENT_DIR% eliminada.
) else (
    echo       No habia carpeta de instalacion.
)
echo.

echo ==========================================================
echo  DESINSTALACION COMPLETADA
echo ==========================================================
echo.
echo  El Puente Local ya no se ejecutara. El POS seguira
echo  funcionando con los motores WebUSB / Web Serial / Iframe.
echo.
pause
endlocal
