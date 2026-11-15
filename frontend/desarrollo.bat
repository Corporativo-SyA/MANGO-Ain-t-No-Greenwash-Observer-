@echo off
REM Modo desarrollo con recarga en caliente (http://localhost:5173). Requiere el backend encendido (iniciar.bat).
cd /d "%~dp0"
if not exist node_modules call npm ci --no-audit --no-fund
call npm run dev
