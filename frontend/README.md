# Frontend MANGO (React + Vite + Leaflet)

Este frontend consume el backend local de MANGO a través del contrato JSON v2.0.0
(`backend/api_contract.json`). No modifica el backend: solo lo consume.

## Uso rápido

1. Coloca esta carpeta `frontend/` en la raíz del repositorio, junto a `backend/`.
2. El zip ya incluye `dist/` compilado: abre `iniciar.bat` (o `./iniciar.sh`) y entra a
   http://127.0.0.1:8000. El backend sirve automáticamente `frontend/dist`.
3. Si cambias el código, recompila con doble clic en `construir.bat` (o `./construir.sh`).
   Requiere Node.js 18 o superior.

| Script | Para qué |
|---|---|
| `construir.bat` / `construir.sh` | `npm ci` y `npm run build`: genera `dist/` |
| `desarrollo.bat` / `desarrollo.sh` | Servidor de Vite en http://localhost:5173 con recarga en caliente; reenvía `/api` al backend (puerto 8000) |

Las llamadas usan rutas relativas (`/api/...`), así que no hace falta configurar CORS ni URLs.
Si el backend corre en otro puerto:

- En desarrollo, define `MANGO_BACKEND=http://127.0.0.1:PUERTO`.
- En producción, define `VITE_API_URL` y recompila.

## Imágenes

| Archivo | Uso |
|---|---|
| `src/assets/logoMango.png` | Logo del encabezado (132 px de alto) |
| `public/favicon.png` | Ícono de la pestaña |

Para cambiar una imagen, reemplaza el archivo conservando el nombre y recompila.

## Capa de manglares GMW

Los archivos `public/capas/gmw_mundo_2025.json` (~0.25 MB) y `public/capas/gmw_mexico.json` (~3.7 MB) se
generan desde las cuadrículas CSV de GMW:

```bash
cd backend
uv run python scripts/capas_gmw_a_json.py --mundo RUTA/manglares_2025_01.csv \
    --mexico RUTA/manglares_mexico_1985-2025_001.csv
```

Solo se descargan cuando el usuario activa **🌿 Manglares GMW** en el mapa. Atribución obligatoria:
©Global Mangrove Watch (CC BY 4.0); la capa la agrega sola al control de Leaflet.

## Estructura

```
src/
├── assets/logoMango.png
├── lib/
│   ├── api.js            cliente del backend (errores con forma {error:{codigo,mensaje}})
│   ├── reglas.js         validaciones espejo del backend (desde 1 mes, 25 km², AAAA-MM)
│   └── transformar.js    salida MANGO v2 → nodos de la línea del tiempo
├── components/
│   ├── FiltrosFechas.jsx     Paso 1: rango de meses
│   ├── MapaZona.jsx          Paso 2: área (rectángulo) o pilotos
│   ├── LineaTiempo.jsx       Paso 3: consulta, línea del tiempo y desglose
│   ├── Progreso.jsx          barra de progreso por etapas
│   ├── ResumenResultado.jsx  veredicto A/B/C/faltan datos e indicadores
│   ├── GraficaManglar.jsx    hectáreas de manglar mes a mes
│   └── Historial.jsx         consultas guardadas
├── App.jsx               orquestador (sondeo, historial, retomar consulta)
├── App.css               tokens de marca y estilos
└── index.css
```

## Cambios respecto al documento técnico original

| Documento original | Ahora | Motivo |
|---|---|---|
| `POST /api/manglares/eventos-extremos`, respuesta inmediata | `POST /api/consultas` (202) y luego `GET /api/consultas/{id}` cada 2 s | Un análisis real tarda de 10 a 16 min |
| Fechas `YYYY-MM-DD` | Meses `AAAA-MM`, desde un solo mes | El backend analiza mes a mes con el histórico completo de la zona |
| `coordenadas {lat_min, …}` | `bbox [lon_min, lat_min, lon_max, lat_max]` | Contrato v2 |
| Punto con `±0.045°` (≈ 100 km²) | Pilotos precalculados de `GET /api/pilotos`, que se abren al instante | Ese recuadro supera el máximo de 25 km² |
| `color: azul/rojo` | CONSERVADO, MODERADO, ALTO, CRÍTICO, QUIEBRE o SIN IMAGEN | Severidad calibrada del pipeline |
| `ubicaciones_manglar` en la respuesta | Pilotos cargados al iniciar la app | Nota 2 del documento original |

Se conservan el diseño, la paleta, las medidas, el encabezado, el fondo, los 3 pasos y los
nodos de 235 px.
