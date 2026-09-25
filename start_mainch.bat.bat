@echo off

REM Cambia al directorio correcto donde está el proyecto
cd /d C:\mainchmenu-main\mainchmenu-main

REM Verifica si Python está instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python no está instalado. Por favor, instálalo antes de continuar.
    pause
    exit /b
)

REM Verifica si Pip está instalado
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Pip no está instalado. Instalando ahora...
    python -m ensurepip --upgrade
    python -m pip install --upgrade pip
)

REM Crea y activa el entorno virtual si no existe
if not exist env (
    echo Creando el entorno virtual...
    python -m venv env
)
call env\Scripts\activate.bat

REM Instala las dependencias si no están instaladas
pip show django >nul 2>&1
if %errorlevel% neq 0 (
    echo Instalando las dependencias necesarias...
    pip install -r requirements.txt
)

REM Inicia el servidor de Django
echo Iniciando el servidor Django...
python manage.py runserver

REM Abre el navegador con el servidor web
start http://127.0.0.1:8000

pause
