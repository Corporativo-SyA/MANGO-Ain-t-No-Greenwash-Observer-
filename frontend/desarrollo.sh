#!/usr/bin/env sh
# Modo desarrollo con recarga en caliente (http://localhost:5173). Requiere el backend encendido (./iniciar.sh).
cd "$(dirname "$0")"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run dev
