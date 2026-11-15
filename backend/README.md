# Backend local – MANGO v2

Servidor **FastAPI** que corre en la computadora del usuario y ejecuta el pipeline científico
**MANGO v2** (`app/pipeline/mango.py`). Todo lo que intercambia con el frontend (React) es **JSON**,
y todo lo que guarda son **archivos JSON**. No necesita base de datos ni servidor dedicado.

- La metodología científica completa, con parámetros, validación y referencias, está en
  [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md).
- El contrato JSON está en [`api_contract.json`](api_contract.json), versión `2.0.0`.

---

## 1. Arrancar (un clic)

| Sistema | Cómo |
|---|---|
| Windows | Doble clic en `iniciar.bat` (raíz del repositorio) |
| Mac / Linux | `./iniciar.sh` (raíz del repositorio) |

La primera vez se instalan `uv`, Python 3.12 y las librerías exactas de `uv.lock`; el Python del
sistema no se toca. Después se abre el navegador en:

- `http://127.0.0.1:8000/docs`: documentación interactiva para probar cada endpoint.
- `http://127.0.0.1:8000`: la app React, cuando exista `../frontend/dist` (sección 6).

### Configurar Global Mangrove Watch (recomendado)

1. Copia `.env.ejemplo` como `.env`.
2. Pon en `GMW_RUTA` la ruta al zip de GMW v4.1 descargado de JAXA/EORC. No hace falta descomprimirlo.

Con GMW, la máscara de manglar es la extensión oficial validada. Sin GMW, el pipeline usa un respaldo
espectral (MVI) y lo avisa en `advertencias`. `GET /api/salud` muestra `gmw_configurado`.

### A mano (dentro de `backend/`)

```bash
uv run --python 3.12 python -m app.main   # servidor
uv run pytest -q                          # 83 pruebas, sin internet
uv run ruff check .                       # linter
```

## 2. Endpoints

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/salud` | Saber si el backend está vivo, con `version_pipeline` y `gmw_configurado` |
| GET | `/api/contrato` | Devuelve `api_contract.json` |
| POST | `/api/consultas` | Lanza una consulta (bbox y meses). Responde **202** al instante |
| GET | `/api/consultas/{id}` | Progreso y, al terminar, el resultado completo |
| GET | `/api/consultas` | **Historial** de consultas guardadas |
| PATCH | `/api/consultas/{id}` | Renombrar una consulta |
| DELETE | `/api/consultas/{id}` | Borrarla del historial |
| GET | `/api/consultas/{id}/descargar` | Bajarla como archivo `.json` |
| GET | `/api/pilotos` | Casos piloto precalculados |
| GET | `/api/pilotos/{id}` | Resultado del piloto al instante |
| GET | `/api/diagnostico/conectores` | Prueba en vivo de NASA POWER, Planetary Computer y los mosaicos GMW |

## 3. Contrato (v2)

**Entrada** (`POST /api/consultas`):

```json
{
  "bbox": [-96.0950, 19.0450, -96.0750, 19.0650],
  "fecha_inicio": "2005-01",
  "fecha_fin": "2024-12",
  "nombre": "Mandinga sur"
}
```

- `bbox` = `[lon_min, lat_min, lon_max, lat_max]` en EPSG:4326.
- En lugar de `bbox` se puede mandar `poligono` (GeoJSON), pero solo uno de los dos. El polígono se
  analiza por su rectángulo envolvente y se avisa en `advertencias`.
- Las fechas son meses `AAAA-MM`, desde un solo mes; el área máxima es de **25 km²**. La línea base y la
  calibración usan el histórico completo de la zona, y el rango solo define qué se muestra.
- Si se manda exactamente la misma consulta, se devuelve la guardada (`200`, `reutilizada: true`).

**Respuesta.** Es un sobre `Consulta`: `estado` (`en_cola`, `en_proceso`, `completada` o `error`),
`progreso` (`porcentaje` y `etapa`), `ejecucion` (bbox, fechas y `area_ha` efectivos), `advertencias`
y `resultado`.

Contrato **2.1.0** (aditivo sobre 2.0): cada mes de `timeline_mensual[].terrestre` agrega
`indice_condicion`, `extension_provisional`, `confianza` y `escenas_descartadas`, e
`ingesta_satelital` agrega `descartadas_por_contaminacion_temporal`. Las consultas guardadas con la
2.0 siguen siendo válidas.

El `resultado` es la salida de MANGO y se distingue por `status`:

| `status` | `caso` | Qué mostrar |
|---|---|---|
| `COMPLETED` | `A` | Manglar conservado: línea de tiempo, eventos y secuelas |
| `COMPLETED` | `B` | Quiebre: `fecha_quiebre`, `naturaleza_quiebre` (ha perdidas, conversión o recuperación, destino, evento asociado) y la línea de tiempo partida por `regimen` |
| `REJECTED_CONTROLLED` | `C` | `motivo` (no hay manglar). **No** es un error HTTP |
| `INSUFFICIENT_DATA` | — | `motivo` y `sugerencia`. **Faltan datos**; no significa que no haya manglar |

Campos principales de un resultado `COMPLETED`:

- `timeline_mensual[]`: cada mes trae `clima` (NASA POWER) y `terrestre`:
  - índices y `regimen`;
  - **extensión** (`fraccion_manglar_en_pie`, `ha_manglar_estimada`): solo pérdida persistente;
  - **condición** (`indice_condicion`): estrés temporal del dosel;
  - `confianza` y `escenas_descartadas`.
- `episodios_evaluados[]`: eventos climáticos con `ejes` (4 ejes con severidad y probabilidad natural),
  `respuesta_al_evento` y `confundido_con`.
- `comparativa_recurrencia_top3`, `resumen_eventos`, `estadisticos_tendencia` y `extension_gmw_anual`.
- `umbrales_aplicados`, `referencias` y `version_pipeline`, para la auditoría.

Errores HTTP, siempre con la forma `{"error": {"codigo", "mensaje", "detalles"}}`:
`BBOX_INVALIDO`, `POLIGONO_INVALIDO`, `RANGO_FECHAS_INVALIDO`, `RANGO_INSUFICIENTE`,
`AREA_INSUFICIENTE` y `AREA_EXCEDIDA`.

Cuando una consulta termina en `estado: "error"`, el código es `FUENTE_EXTERNA_NO_DISPONIBLE`,
`RESULTADO_FUERA_DE_CONTRATO`, `ERROR_INTERNO` o `CONSULTA_INTERRUMPIDA`. Los `mensaje` ya están
redactados para mostrarse tal cual.

**Mocks para React** (`contrato/ejemplos/`), generados con el pipeline real sobre datos sintéticos:

- `consulta_caso_A.json`, `consulta_caso_B.json`, `consulta_caso_C.json`
- `consulta_datos_insuficientes.json`, `consulta_en_proceso.json`
- `entrada_consulta.json`, `error_area_excedida.json`, `error_rango_insuficiente.json`

La fuente de verdad son los modelos de `app/contrato.py`. Si cambian, regenera con
`uv run python scripts/exportar_contrato.py`.

## 4. Flujo para el frontend

```js
const API = "http://127.0.0.1:8000";

async function consultar(bbox, fechaInicio, fechaFin, nombre, onProgreso) {
  const r = await fetch(`${API}/api/consultas`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bbox, fecha_inicio: fechaInicio, fecha_fin: fechaFin, nombre }),
  });
  let c = await r.json();
  if (!r.ok) throw c.error;                       // mostrar c.error.mensaje

  while (c.estado === "en_cola" || c.estado === "en_proceso") {
    onProgreso?.(c.progreso.porcentaje, c.progreso.etapa);
    await new Promise((res) => setTimeout(res, 2000));
    c = await (await fetch(`${API}/api/consultas/${c.id_consulta}`)).json();
  }
  if (c.estado === "error") throw c.error;
  return c.resultado;                              // switch (resultado.status)
}
```

La **primera** consulta de una zona descarga su histórico completo (Landsat desde 1984 y clima desde 1981)
y es la más lenta. Las siguientes dentro de la misma zona, o de un recuadro contenido en ella, salen de la
caché local y solo recalculan el análisis. La barra avanza de forma continua: clima 0–6 %, GMW 6–9 %, Landsat 9–84 %
(escena por escena), análisis de zona 84–90 %, eventos 90–96 % y secuelas 96–99 %.
Para mostrar algo de inmediato, usa los pilotos.

## 5. Dónde se guardan las consultas

En `~/.manglares_mvp/`. Se puede cambiar con `MANGLARES_DATOS`.

```
consultas/<id>.json   cada consulta: parámetros, estado y resultado (historial)
cache/<huella>.json   apunta a la consulta ya hecha con la misma entrada
cache_satelital/      escenas Landsat (bandas crudas por escena) y clima por celda de NASA POWER
```

La caché satelital hace que una zona ya consultada, o cualquier recuadro **dentro** de ella, se analice
sin volver a descargar: solo se consulta el catálogo STAC (segundos) y se recalcula el análisis. Se puede
borrar en cualquier momento; solo cuesta volver a descargar.

La huella incluye el bbox redondeado a 4 decimales, las fechas, la versión del pipeline y si se usó GMW.
Si la app se cierra a mitad de una consulta, al reabrirla queda en error `CONSULTA_INTERRUMPIDA`.

## 6. Servir el frontend

Corre `npm run build` dentro de `frontend/`. El backend sirve `frontend/dist` en
`http://127.0.0.1:8000`; al ser el mismo origen, no hay problemas de CORS. Para usar otra carpeta,
define `CARPETA_FRONTEND`. En desarrollo con Vite (`localhost:5173`), el CORS ya está permitido.

## 7. Pilotos y validación real

Estos scripts **requieren internet**:

```bash
# Casos piloto: mandinga_sur, tiburon, veracruz_centro, cananeia
uv run python scripts/generar_pilotos.py            # todos (de 40 a 60 min)
uv run python scripts/generar_pilotos.py tiburon    # solo uno

# Regresión v1 contra v2 en los 4 escenarios validados (de 1 a 1.5 h); sale con código 1 si falla
uv run python scripts/regresion_mango.py
```

- Los pilotos se guardan en `datos_iniciales/pilotos/`. Súbelos al repositorio para que la app los
  sirva al instante.
- La regresión escribe sus resultados en `salidas_regresion/`, que no se sube.

## 8. Capa de manglares para el mapa (GMW)

`scripts/generar_cuadricula_gmw.py` convierte los mosaicos de GMW v4.1 en una cuadrícula CSV/GeoJSON
que contiene solo las celdas con manglar, para colorear el mapa del mundo.

```bash
# Manglar actual de todo el mundo (ligero)
uv run python scripts/generar_cuadricula_gmw.py RUTA/gmw.zip --celda 0.1 \
    --nombre manglares_mundo_2025_01 --procesos 4

# Histórico 1985-2025 solo de México
uv run python scripts/generar_cuadricula_gmw.py RUTA/gmw.zip --celda 0.01 \
    --anios 1985-2025 --nombre manglares_mexico_1985-2025_001 --procesos 4 \
    --region -118.5 22 -105 32.8 --region -110 14.5 -92.24 25.85 \
    --region -92.24 17.8 -89.15 21.8 --region -89.15 18.45 -86.5 21.8
```

Atribución obligatoria donde se muestre: **©Global Mangrove Watch** (CC BY 4.0).

## 9. Variables de configuración (`.env` o entorno)

| Variable | Por defecto | Qué hace |
|---|---|---|
| `GMW_RUTA` | *(vacío)* | Zip o carpeta de GMW v4.1 (máscara oficial de manglar) |
| `PIPELINE_HILOS` | `8` | Descargas Landsat en paralelo |
| `AREA_MAX_KM2` | `25` | Área máxima del bbox |
| `AREA_MIN_HA` | `1` | Área mínima del bbox |
| `MESES_MINIMOS` | `1` | Rango mínimo de la consulta (guardarraíl opcional) |
| `MES_MINIMO` | `1985-01` | Primer mes permitido |
| `MAX_TRABAJOS` | `1` | Consultas simultáneas |
| `MANGLARES_DATOS` | `~/.manglares_mvp` | Carpeta del historial y de la caché satelital |
| `CACHE_SATELITAL` | `1` | Caché local de escenas Landsat y clima (`0` la desactiva) |
| `PUERTO` | `8000` | Puerto local |
| `ABRIR_NAVEGADOR` | `1` | Abrir el navegador al arrancar |
| `CARPETA_FRONTEND` | `../frontend/dist` | Build de React que se sirve en `/` |

## Estructura

```
app/
  main.py               servidor, errores en JSON, frontend estático
  config.py             configuración (.env)
  contrato.py           modelos del contrato JSON v2 (fuente de verdad)
  rutas/                consultas.py, pilotos.py, sistema.py
  servicios/            validacion.py, almacen.py (JSON), trabajos.py (segundo plano)
  clientes/             nasa_power.py, landsat_stac.py (usados por el diagnóstico)
  pipeline/
    mango.py            pipeline científico MANGO v2
    puente.py           adaptador backend ↔ pipeline (fechas, errores, progreso)
    sintetico.py        escenarios sintéticos para pruebas y mocks
    mango_v1_referencia.py   pipeline original, intacto, para la regresión
docs/METODOLOGIA.md     metodología para el artículo
contrato/ejemplos/      mocks JSON para el frontend
datos_iniciales/pilotos/  pilotos precalculados (generar con internet)
scripts/                exportar_contrato, generar_pilotos, regresion_mango, generar_cuadricula_gmw
tests/                  83 pruebas, sin internet
```
