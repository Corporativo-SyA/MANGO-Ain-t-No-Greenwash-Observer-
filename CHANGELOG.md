# Cambios

## [Sin publicar]

### Corregido
- Pipeline 2.0.1: se enmascara la nube de confianza media y alta (QA_PIXEL bits 8-9); el bit 3 solo
  marca la de alta confianza y la media pasaba el filtro.
- Pipeline 2.0.2: una temperatura superficial implausible (< -10 °C o > 75 °C) invalida el píxel
  completo (prueba térmica de nube); antes solo se borraba la temperatura y el NDVI de la nube contaba.

### Agregado
- Pipeline 2.0.3: filtro multitemporal de escenas contaminadas (principio Tmask/CCDC). Una escena
  cuyo NDVI cae y regresa en la pasada siguiente, ambas veces más de 3σ de la zona, se descarta; la
  línea base y el núcleo se recalculan sin ella. Conteo en
  `ingesta_satelital.descartadas_por_contaminacion_temporal`.

### Cambiado
- Pipeline 2.0.4: el ruido de extensión de la línea base usa mediana + 3σ robusta en lugar del
  percentil 99 (con ~20 escenas el P99 es el máximo; en Mandinga una escena fijó el umbral en 25 %).
- Pipeline 2.0.5: un píxel solo cuenta como perdido si también lo está en su siguiente observación
  válida (confirmación por píxel; filtra parches de nube que la mediana de la escena no delata).
- **Contrato 2.1.0 / pipeline 2.1.0:** la línea de tiempo separa la EXTENSIÓN (`fraccion_manglar_en_pie`,
  `ha_manglar_estimada`: solo pérdida persistente 12-18 meses por píxel) de la CONDICIÓN del dosel
  (`indice_condicion`, z robusto vs. el mes calendario sano). Nuevo `extension_provisional`. Campos
  aditivos: las consultas guardadas siguen siendo válidas.
- Pipeline 2.1.1: cada mes trae `confianza` (ALTA ≥2 escenas que coinciden, MEDIA 1 escena, BAJA
  discrepan más que el ruido de la zona) y `escenas_descartadas`.
- Frontend: la gráfica se corta en los meses sin imagen (ya no une puntos a través de huecos), marca
  meses de baja confianza y tramos provisionales, y tiene selector Hectáreas / Condición / NDVI / MVI /
  MNDWI (MVI con su umbral publicado de 4.6).
- Frontend: el antes/después y el % de cada evento usan solo meses confiables (relleno de hasta 2
  meses, sin meses de baja confianza ni provisionales) y muestran la condición del dosel.
- `scripts/sesgo_sensores.py`: mide el sesgo de NDVI Landsat 7 vs 8/9 con pares a <= 8 días y decide
  con datos si aplicar la armonización de Roy et al. 2016 (corrige la afirmación de que era innecesaria).
- Mapa: capa **Manglares GMW** (mundo 2025 en celdas de 0.1°; México 1985–2025 en celdas de 0.01° con
  selector de año y modo "cambio desde 1985"). Clic en una celda de México propone un área de ~11 km².
  Datos en `frontend/public/capas/` generados con `backend/scripts/capas_gmw_a_json.py`.
- Gráfica: si la consulta no trae GMW (sin `GMW_RUTA`) y el área está en México, se dibuja la serie
  anual "GMW aprox." desde la cuadrícula de 1 km (ponderada por área; solo orientativa).
- `scripts/regresion_mango.py`: caso real `mandinga_zona_usuario` (2000-06 → 2026-06) con criterios de
  extensión vs. condición, sensibilidad frente a la baja de GMW y coherencia del caso. La salida de la
  v2.0 falla los 5 criterios (es el diagnóstico que motivó la v2.1).
- Escenarios sintéticos `neblina` (6 nubes completas sin bandera QA + 3 brumas) y `sequia` (4 meses,
  NDVI −0.25, recuperación en 6) con sus pruebas: Caso A, extensión intacta, condición < −2 en la sequía.
- `docs/METODOLOGIA.md` v2.1: secciones 3, 3.1 y 5.1, parámetros, validación, correcciones y referencias [26]–[27].
- Pipeline 2.1.3: **caché local** de escenas Landsat (bandas crudas por escena; un recuadro dentro de otro
  ya consultado se recorta sin descargar, porque la rejilla está alineada a 30 m en UTM) y de clima por
  celda de NASA POWER. En `<MANGLARES_DATOS>/cache_satelital`; `CACHE_SATELITAL=0` la desactiva.
  `ingesta_satelital.escenas_desde_cache` dice cuántas no se descargaron.
- **Pipeline 2.2.0: histórico completo de la zona.** Landsat se descarga de 1984 a ayer (antes: solo el
  rango consultado). La línea base es la primera ventana de 24 meses del registro con datos suficientes,
  así que el resultado ya no depende de la fecha de inicio elegida. Solo cuenta un quiebre hasta el fin
  del rango; si fue antes del rango se advierte. Tendencia, eventos y línea de tiempo siguen limitados al
  rango. Nuevos `ingesta_satelital.historia_landsat` y `escenas_en_rango`. El escenario sintético
  `pocos` sube a 98 % de pasadas nubladas: con todo el registro, el 93 % ya tenía datos suficientes.
- El rango mínimo de la consulta baja de 36 meses a **1 mes** (`MESES_MINIMOS=1`, sigue configurable):
  los 36 meses existían solo porque la línea base salía del propio rango.

## [0.2.0] - 2026-11-15

Integración del pipeline científico **MANGO v2**. Metodología en `backend/docs/METODOLOGIA.md`.

### Agregado
- `app/pipeline/mango.py` (v2.0.0): pipeline científico completo. Todos los umbrales son
  estándares publicados o se calculan con la propia zona, y se devuelven en `umbrales_aplicados`.
  - Máscara de manglar con GMW v4.1 y piso de NDVI aprendido de la zona; respaldo MVI 4.6 (Baloloy 2020).
  - Agua permanente con Otsu por escena.
  - Pérdida por extensión, a nivel de píxel, con PELT (penalización BIC), persistencia de 12 a 18
    meses, hectáreas perdidas y naturaleza del quiebre (conversión o recuperación, destino de la
    cobertura).
  - Eventos con el clima completo desde 1981 y el periodo base 1991–2020: R99p, viento P98 y Nortes
    (solo en el Golfo de México y el Caribe), SPI-3 y CTX90pct.
  - Severidad calibrada con una distribución nula empírica de la zona; `respuesta_al_evento` frente
    al propio régimen.
  - Reintentos con espera exponencial para NASA POWER y STAC.
  - Progreso continuo, escena por escena.
- Estado `INSUFFICIENT_DATA` con `motivo` y `sugerencia` (faltan datos ≠ sin manglar).
- Entrada por `bbox`; se sigue aceptando `poligono`.
- `backend/.env` (plantilla `.env.ejemplo`) con `GMW_RUTA`.
- Scripts `regresion_mango.py` (v1 contra v2 con datos reales) y `generar_pilotos.py` (4 pilotos reales).
- `app/pipeline/sintetico.py` y 33 pruebas científicas sin internet (66 pruebas en total).
- `docs/METODOLOGIA.md`, con estilo de artículo IEEE y 25 referencias.

- **Frontend React** (`frontend/`): flujo de 3 pasos (meses, mapa y línea del tiempo) consumiendo
  el contrato v2, con:
  - progreso por etapas y consultas que se retoman al recargar;
  - veredicto A/B/C/faltan datos e indicadores clave;
  - gráfica de hectáreas mes a mes con quiebre y eventos;
  - filtros y desglose Antes vs. Después en 4 ejes;
  - pilotos al instante, buscador de lugares, historial y descarga JSON.
- `frontend/dist` versionado: la app completa corre solo con `iniciar.bat`, sin Node.

### Cambiado
- **Contrato v2.0.0 (incompatible con v1):** el `resultado` ahora es la salida de MANGO,
  distinguida por `status`. El sobre `Consulta` agrega `ejecucion` y `advertencias`.
- Guardrails: área máxima de 25 km² y rango mínimo de 36 meses.
- La huella de caché incluye la versión del pipeline y el uso de GMW.
- La CI ya no compara los mocks byte a byte; las diferencias de punto flotante entre plataformas
  se toleran. Sí verifica `api_contract.json`.

### Eliminado
- Stub de datos simulados, `MODO_PIPELINE`, `STUB_RETRASO_S` y `?escenario_stub=`.
- `app/clientes/gmw.py`. GMW se lee directamente en el pipeline.

## [Anterior a 0.2.0]

### Agregado
- `scripts/generar_cuadricula_gmw.py`: convierte los mosaicos de Global Mangrove Watch v4.1
  en una cuadrícula CSV/GeoJSON de celdas con manglar (para colorear el mapa y el Caso C).
  Acepta series históricas (`--anios 1985-2025`), varias regiones (`--region` repetible)
  y nombre de salida (`--nombre`).

## [0.1.0] - 2026-11-15

Primera versión del backend (Fase 2.1, *shallow implementation*).

### Agregado
- Servidor local FastAPI con todos los intercambios en JSON y errores con forma única.
- Endpoints de consultas asíncronas (`POST`/`GET`), historial guardado, renombrar,
  borrar y descargar.
- Stubs de los Casos A, B y C con la forma final del contrato.
- `api_contract.json` v1.0.0 y mocks en `backend/contrato/ejemplos/`.
- Punto de enchufe `ejecutar_experimento()` para el pipeline científico (`MODO_PIPELINE=real`).
- Conectores crudos a NASA POWER y al STAC de Landsat en Planetary Computer
  (búsqueda + lectura por ventana de COG), con reintentos.
- Casos piloto de Veracruz servidos al instante.
- Diagnóstico en vivo de conectores.
- Arranque de un clic (`iniciar.bat` / `iniciar.sh`) con Python 3.12 vía `uv`.
- Integración continua: linter, pruebas y verificación del contrato.

### Pendiente
- Conector a Global Mangrove Watch v4.1 (definir forma de descarga).
- Pipeline científico real.
- Frontend React en `frontend/`.
