@echo off
REM Arranque de un clic en Windows: instala uv si falta, prepara Python 3.12 y abre la app.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Instalando uv por primera vez...
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
if not exist "frontend\dist\index.html" echo [Aviso] No existe frontend\dist: corre frontend\construir.bat para tener la interfaz.
echo Iniciando MANGO en http://127.0.0.1:8000 (cierra esta ventana para apagarlo) ...
cd backend
uv run --python 3.12 python -m app.main
pause
