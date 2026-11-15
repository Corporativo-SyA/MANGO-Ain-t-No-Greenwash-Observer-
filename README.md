# MVP – Detector de Tendencias e Impacto en Manglares

**MANGO** (*MANGO Ain't No Greenwash Observer*) corre **en la computadora del usuario**. El
usuario dibuja un rectángulo sobre el mapa y elige un rango de meses. La app muestra cómo cambió el
manglar de esa zona y cómo respondió el territorio a los eventos climáticos. Usa **NASA POWER**
(clima), **Landsat** (USGS/NASA, vía Microsoft Planetary Computer) y **Global Mangrove Watch v4.1**.

> Estado: **v0.2.0**. Pipeline científico MANGO v2 y frontend React integrados en una sola app local.
> La metodología está en [`backend/docs/METODOLOGIA.md`](backend/docs/METODOLOGIA.md).

## Usar la app (usuario nuevo)

1. Descarga el repositorio: **Code → Download ZIP** (o `git clone`) y descomprímelo.
2. Doble clic en **`iniciar.bat`** (Windows) o corre `./iniciar.sh` (Mac / Linux).
3. Se abre el navegador en **http://127.0.0.1:8000** con la app completa.

No hace falta instalar Python ni Node. La primera vez, [`uv`](https://docs.astral.sh/uv/) descarga
Python 3.12 y las librerías exactas del proyecto, sin tocar el Python del sistema; tarda un par de
minutos. Las siguientes veces abre en segundos. Para apagar la app, cierra la ventana de la consola.

**Recomendado:** descarga Global Mangrove Watch v4.1 (zip de JAXA/EORC), copia
`backend/.env.ejemplo` como `backend/.env` y pon en `GMW_RUTA` la ruta a ese zip.

## Estructura

```
.
├── backend/          Servidor local FastAPI y pipeline MANGO v2 (ver backend/README.md)
├── frontend/         App React + Vite + Leaflet (ver frontend/README.md)
│   └── dist/         Build ya compilado que sirve el backend (se versiona a propósito)
├── iniciar.bat       Arranque de un clic en Windows
├── iniciar.sh        Arranque de un clic en Mac / Linux
└── .github/workflows CI: linter, pruebas, contrato JSON y build del frontend
```

- `http://127.0.0.1:8000` → la app
- `http://127.0.0.1:8000/docs` → documentación interactiva de la API

## Contrato backend ↔ frontend

Todo el intercambio es **JSON**. El contrato (v2.0.0) vive en
[`backend/api_contract.json`](backend/api_contract.json). En
[`backend/contrato/ejemplos/`](backend/contrato/ejemplos/) hay respuestas de ejemplo para usarlas como
mocks en React: Casos A, B y C, datos insuficientes, consulta en proceso y errores.
La guía para el frontend está en [`backend/README.md`](backend/README.md).

**Opcional pero recomendado:** copia `backend/.env.ejemplo` como `backend/.env` y pon en `GMW_RUTA` la
ruta al zip de Global Mangrove Watch.

## Cómo colaborar

1. Crea una rama desde `main`: `git checkout -b feature/nombre-corto`.
2. Antes de subir, dentro de `backend/`:
   ```bash
   uv run ruff check .
   uv run pytest -q
   ```
3. Si cambias `backend/app/contrato.py`, regenera el contrato y súbelo en el mismo commit:
   ```bash
   uv run python scripts/exportar_contrato.py
   ```
   Si el cambio rompe al frontend, sube `VERSION_CONTRATO` y avisa al equipo.
4. Si cambias algo en `frontend/src`, recompila con `frontend/construir.bat` (requiere Node 18+)
   y sube `frontend/dist` en el mismo commit; así quien descargue el repo no necesita Node.
   Para desarrollar con recarga en caliente usa `frontend/desarrollo.bat` (http://localhost:5173).
5. Abre un Pull Request. La CI de GitHub corre el linter, las pruebas, verifica el contrato y
   compila el frontend.

Reglas del equipo: no subir `.ipynb`, `.env`, entornos virtuales, `node_modules` ni imágenes `.tif`.

Antes de un cambio en `backend/app/pipeline/mango.py`, corre la regresión con datos reales:
`uv run python scripts/regresion_mango.py`.

## Fuentes de datos

- NASA POWER (reanálisis MERRA-2): https://power.larc.nasa.gov
- Landsat Collection 2 Level-2 (USGS/NASA) vía Microsoft Planetary Computer
- Global Mangrove Watch v4.1 (JAXA/EORC, CC BY 4.0). Atribución: ©Global Mangrove Watch
