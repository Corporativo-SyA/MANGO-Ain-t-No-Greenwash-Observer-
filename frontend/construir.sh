#!/usr/bin/env sh
# Compila el frontend de MANGO en frontend/dist (el backend lo sirve en http://127.0.0.1:8000).
set -e
cd "$(dirname "$0")"
command -v npm >/dev/null 2>&1 || { echo "[MANGO] Falta Node.js 18 o superior: https://nodejs.org"; exit 1; }
npm ci --no-audit --no-fund
npm run build
echo "[MANGO] Listo. Ahora corre ./iniciar.sh (raíz del repositorio) y entra a http://127.0.0.1:8000"
