@echo off
REM Compila el frontend de MANGO en frontend\dist (el backend lo sirve en http://127.0.0.1:8000).
cd /d "%~dp0"
where npm >nul 2>nul || (echo [MANGO] Falta Node.js 18 o superior: https://nodejs.org & pause & exit /b 1)
call npm ci --no-audit --no-fund || (pause & exit /b 1)
call npm run build || (pause & exit /b 1)
echo.
echo [MANGO] Listo. Ahora abre iniciar.bat (raiz del repositorio) y entra a http://127.0.0.1:8000
pause
