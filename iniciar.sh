#!/usr/bin/env bash
# Arranque de un clic en Mac/Linux: instala uv si falta, prepara Python 3.12 y abre la app.
set -e
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "Instalando uv por primera vez..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -f frontend/dist/index.html ] || echo "[Aviso] No existe frontend/dist: corre frontend/construir.sh para tener la interfaz."
echo "Iniciando MANGO en http://127.0.0.1:8000 (Ctrl+C para apagarlo) ..."
cd backend
uv run --python 3.12 python -m app.main
