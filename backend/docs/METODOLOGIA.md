# MANGO v2 — Metodología

**MANGO** (*MANGO Ain't No Greenwash Observer*) es un motor de análisis de tendencias e impacto en
ecosistemas de manglar a partir de datos satelitales abiertos. Este documento describe la versión 2.0.0
del pipeline (`app/pipeline/mango.py`; esta edición documenta la **v2.1**) con el nivel de detalle necesario para un artículo técnico:
datos, métodos, parámetros con su justificación, validación y limitaciones.

> **Principio de diseño.** Ningún umbral es un número arbitrario. Cada umbral es (a) un estándar
> publicado, (b) un estadístico calculado con los datos de la propia zona (su clima, su manglar o el
> ruido de sus series), o (c) un límite de ingeniería declarado como tal. Todos los umbrales efectivos
> de cada consulta se devuelven en `umbrales_aplicados`, para que el resultado sea auditable y reproducible.

---

## 1. Planteamiento

MANGO trata cada consulta como un **experimento natural**:

- **Causa externa (forzante):** series diarias de clima en el centroide de la zona (NASA POWER).
- **Respuesta del terreno:** reflectancia y temperatura superficial a 30 m (Landsat 5/7/8/9).
- **Extensión de referencia:** mapa anual de manglar a 30 m (Global Mangrove Watch v4.1).

Cada consulta se clasifica en uno de cuatro resultados:

| Resultado | Significado |
|---|---|
| `COMPLETED` / **Caso A** | Manglar presente y sin pérdida estructural en el periodo |
| `COMPLETED` / **Caso B** | Pérdida estructural detectada (fecha de quiebre, extensión perdida y naturaleza) |
| `REJECTED_CONTROLLED` / **Caso C** | Se confirmó que la zona **no** contiene manglar |
| `INSUFFICIENT_DATA` | No hay observaciones suficientes para decidir; **no** equivale a "sin manglar" |

---

## 2. Datos

| Fuente | Variables | Resolución | Acceso |
|---|---|---|---|
| NASA POWER, comunidad AG (MERRA-2) [23] | `PRECTOTCORR`, `WS10M`, `WS10M_MAX`, `WD10M`, `T2M_MAX`, `ALLSKY_SFC_SW_DWN` | ½° × ⅝°, diaria, desde 1981 | API pública, sin llave |
| Landsat Collection 2 Level-2 (L5 TM, L7 ETM+, L8/L9 OLI-TIRS) | SR verde, rojo, NIR, SWIR1; ST (térmica); `QA_PIXEL`; `ST_QA` | 30 m, 8–16 días | STAC de Microsoft Planetary Computer, firma anónima |
| Global Mangrove Watch v4.1 [3] | Extensión de manglar binaria, 41 bandas (1985–2025) | 30 m (0.000269°), mosaicos de 1° | Zenodo / JAXA EORC; uso local (`GMW_RUTA`) |

**Histórico completo de la zona (v2.2).** Aunque el usuario consulte un periodo corto, se descargan
**todas las escenas Landsat de la zona desde 1984 (Landsat 5) hasta ayer**, y NASA POWER **desde 1981**.
La línea base, la máscara, el ruido y la calibración de severidad usan todo ese registro; la línea de
tiempo, los eventos y la tendencia se reportan **solo para el rango consultado**. Con la caché local
(sección 12) el histórico de una zona se descarga una sola vez.

**Clima.** Los umbrales de eventos se calculan sobre el periodo normal climatológico de la
OMM, **1991–2020** [11]. La línea de tiempo que ve el usuario solo cubre el rango que consultó.

**Unidades y sesgos conocidos.** `WS10M_MAX` es el máximo diario de la velocidad horaria promedio de una
celda MERRA-2 (no una ráfaga), y tiende a subestimarse en América [23]. Por eso todos los umbrales de
viento y temperatura son **relativos** (percentiles locales) y no absolutos.

---

## 3. Preprocesamiento

1. **Rejilla canónica.** El bbox se reproyecta a la zona UTM del centroide (EPSG:326xx norte / 327xx sur),
   alineada a 30 m. Todas las bandas se remuestrean con `WarpedVRT` (vecino más cercano para QA,
   bilineal para reflectancia y temperatura).
2. **Búsqueda STAC.** Se prefiltran las escenas con nubosidad de escena < 60 %. Este filtro es solo de
   eficiencia: la nubosidad de la escena (~185 km) no refleja la del polígono. La decisión real la toma
   la máscara por píxel.
3. **Máscara por píxel.** Con `QA_PIXEL` se descartan relleno (bit 0), nube dilatada (1), cirro (2),
   nube (3) y sombra (4). En la Colección 2 el bit 3 solo marca la nube de **alta** confianza [26], así
   que además se descartan los píxeles con **confianza de nube media o alta** (bits 8–9 ≥ 10). Los bits
   14–15 (cirro) no se usan: solo tienen nivel bajo o alto, y el alto ya lo cubre el bit 2. Una escena se
   conserva si al menos el 50 % del polígono queda válido (incluye el bandeado SLC-off de Landsat 7).
4. **Escalas oficiales USGS.** Reflectancia: SR = DN·2.75×10⁻⁵ − 0.2. Temperatura: ST = DN·0.00341802 + 149.0 K.
   La incertidumbre por píxel se obtiene de `ST_QA` (0.01 K/DN) [21].
   **Prueba térmica de nube:** una temperatura superficial fuera de [−10, 75] °C no es terreno visible
   (cima de nube fría o artefacto) e **invalida el píxel completo**, con la misma lógica de temperatura de
   brillo que usa Fmask. En la v2.0 solo se borraba la temperatura y el NDVI de la nube seguía contando.
5. **Índices.** NDVI, NDMI, MNDWI [7] y **MVI = |NIR − Verde| / |SWIR1 − Verde|**, en la forma publicada
   [1], recortado a [0, 20].
6. **Armonización entre sensores.** La transformación ETM+→OLI de Roy et al. [22] **todavía no se
   aplica**. L4–7 (LEDAPS) y L8–9 (LaSRC) tienen corrección atmosférica y respuesta espectral distintas, así
   que el sesgo se **mide** con `scripts/sesgo_sensores.py`: pares L7/L8 a ≤ 8 días, intervalo de
   confianza por *bootstrap*, comparado con el ruido del mismo sensor. Se aplicará solo si los datos lo
   piden (sección 10).

### 3.1 Filtro multitemporal de escenas contaminadas

La máscara por escena omite nubes delgadas y neblina; las evaluaciones de series de tiempo encuentran que
Fmask es el algoritmo con mayor omisión de nubes. Siguiendo el principio del filtro multitemporal de
CCDC (Tmask) [27]:

- El cambio entre dos escenas consecutivas es la **mediana de las diferencias píxel a píxel** del NDVI
  sobre el núcleo visto en ambas. No se usa la diferencia de medianas: tras una pérdida parcial la
  distribución es bimodal y su mediana salta sin que el terreno cambie.
- σ = 1.4826·MAD de esos cambios en toda la serie de la zona.
- Una escena se descarta si su NDVI **cae** más de 3σ respecto a la anterior y **regresa** más de 3σ en la
  siguiente. No se juzga si algún vecino está a más de 120 días; la primera y la última escena nunca se
  marcan.
- Si hubo descartes, la línea base y el núcleo se **recalculan** sin esas escenas, porque dependen uno
  del otro.
- Consecuencia asumida: un fenómeno que dura menos de una revisita (8–16 días) y no se repite en la
  escena siguiente no se puede confirmar con Landsat; se reporta como descartado, no como evento.

---

## 4. Máscara de manglar y admisibilidad

**Línea base.** La **primera ventana de 24 meses del registro completo** (dos ciclos estacionales) con al
menos 6 escenas limpias; en los 80–90 el archivo es escaso y la ventana avanza hasta encontrarlas. Así la
línea base es de la zona y **no depende de la fecha de inicio que elija el usuario** (hasta la v2.1 eran los
primeros 24 meses de la consulta, y dos consultas del mismo recuadro con inicios distintos podían dar
umbrales distintos). Se exigen al menos 12 escenas en todo el registro, la ventana de base, al menos una
escena en el rango consultado y el 50 % del polígono observado sin nubes. Si no se cumple, el resultado es
`INSUFFICIENT_DATA` con su motivo. La máscara GMW se toma del año en que empieza la línea base. Como la línea base ya no sale del rango
consultado, el rango mínimo es de **un mes** (hasta la v2.1 era de 36 meses).

**Agua permanente.** En cada escena, el umbral de MNDWI se obtiene con **Otsu** [8]. Se acepta si la
separabilidad η ≥ 0.5 y ambas clases cubren entre el 5 % y el 95 % del polígono; si no, se usa el umbral
0 de Xu [7]. Un píxel es agua permanente si es agua en ≥ 80 % de las escenas de la línea base (el
manglar es intermareal).

**Máscara de manglar (núcleo):**

1. **Con GMW (recomendado):** píxeles GMW del año inicial de la consulta, terrestres. A partir de ellos
   se aprende el NDVI típico del manglar **de esa zona** (mediana y desviación robusta 1.4826·MAD [25]),
   y se excluyen los píxeles por debajo de `mediana − 3σ` (errores de comisión o áreas ya perdidas antes
   de la consulta).
2. **Si GMW no registra manglar pero la firma espectral sí** (MVI ≥ 4.6 y NDVI ≥ 0.30, umbrales
   publicados para Landsat [1]): se acepta con advertencia de posible omisión de GMW.
3. **Sin GMW configurado:** respaldo espectral con los umbrales publicados, y advertencia explícita de
   menor precisión. El rango del MVI varía por sitio, clima y marea, y Otsu no separa bien el MVI [2].

**Caso C.** Si el núcleo resultante es menor a **0.5 ha** (área mínima de la definición de bosque de la
FAO [24]), se confirma que no hay manglar.

---

## 5. Detección de pérdida estructural (Caso B)

La pérdida se mide **por extensión**, a nivel de píxel, y no con la mediana del NDVI: una tala parcial
casi no mueve la mediana.

1. **Píxel perdido en una escena:** NDVI ≤ NDVI_base(píxel) − max(0.20, 3·σ_píxel). Aquí 0.20 es el
   umbral de pérdida permanente de Goldberg et al. [4] (basado en Lagomasino et al. [5]) y σ_píxel es la
   variabilidad estacional de ese píxel en la línea base.
2. **Confirmación por píxel:** un píxel candidato solo cuenta como perdido si **también lo es en su
   siguiente observación válida** (principio de confirmación de CCDC [27]). Así un parche de nube que la
   mediana de la escena no delata no suma pérdida. La fecha sigue siendo la de la primera observación.
   **Fracción perdida por escena:** píxeles perdidos confirmados ÷ píxeles del núcleo observados. Las
   escenas que ven menos del 50 % del núcleo no deciden.
3. **Umbral de extensión τ:** el mayor entre el **ruido de la zona** (mediana + 3σ robusta de la
   fracción perdida en la línea base ya limpia) y la fracción que equivale a 0.5 ha. Hasta la v2.0 el
   ruido era el percentil 99; con unas 20 escenas de base eso es en la práctica el máximo, y una sola
   escena mala fijaba τ (caso real de Mandinga: 25 % del núcleo, ~5.3 ha).
4. **Cambio de régimen:** PELT [18] (biblioteca `ruptures` [19]) sobre la serie de fracción perdida
   estandarizada con σ = MAD(Δ)/√2, con **penalización BIC** λ = 2·log n y segmento mínimo de unos
   6 meses de escenas. Cada corte se refina a la primera escena que ya está perdida y así se mantiene.
5. **Quiebres y rango:** solo cuenta un quiebre con fecha **hasta el fin del rango consultado**; para
   confirmarlo se usan también las escenas posteriores al rango. Si ocurrió **antes** del rango, el caso es B
   con una advertencia y la línea de tiempo muestra el régimen posterior.
   **Persistencia:** el quiebre se confirma si la mediana de pérdida **entre 12 y 18 meses después**
   sigue ≥ τ y equivale a ≥ 0.5 ha. Una perturbación que rebota dentro de una temporada de crecimiento
   **no** es pérdida estructural, siguiendo la distinción de Yang et al. [6].
6. **Naturaleza del quiebre:**
   - `CONVERSION_PERSISTENTE` o `PERTURBACION_CON_RECUPERACION`, según si a los 24 meses la pérdida volvió
     bajo τ; `INDETERMINADO_SEGUIMIENTO_CORTO` si no hay 24 meses de seguimiento.
   - **Destino** de los píxeles perdidos: `AGUA` (MNDWI > umbral), `SUELO_DESNUDO_O_CONSTRUIDO`
     (NDVI < 0.30) u `OTRA_VEGETACION`.
   - **Atribución a un evento climático:** solo si el evento ocurrió **entre la última observación
     intacta y la primera con pérdida** y pertenece al **decil más intenso** de su tipo. Con unos
     10 eventos al año, una ventana fija de 90 días asociaría por azar casi cualquier quiebre a algún
     evento (P ≈ 1 − e^(−10·0.25) ≈ 92 %).
7. **Régimen de cada escena:** `CON_MANGLAR` antes del intervalo de transición, `TRANSICION` entre la
   última escena intacta y el quiebre, y `SIN_MANGLAR` después.
8. **Tendencia:** Mann-Kendall y Sen [20] por escena (compatibilidad con la v1) y sobre la serie
   **mensual desestacionalizada** (pendiente por año). Se reportan pero **no deciden** el quiebre.

### 5.1 Extensión contra condición del dosel (línea de tiempo)

Hasta la v2.0, la línea de tiempo mostraba la pérdida por escena como "hectáreas en pie". Así una sequía,
una inundación o una defoliación con rebrote aparecían como hectáreas perdidas: en Mandinga, la sequía de
2011 y una inundación en julio de 2021. Desde la v2.1 se separan:

- **Extensión** (`fraccion_manglar_en_pie`, `ha_manglar_estimada`): un píxel candidato en *t* cuenta como
  superficie perdida solo si sigue siendo candidato en ≥ 50 % de sus observaciones limpias dentro de
  **[t+12 m, t+18 m]**. Es la misma ventana que define el quiebre estructural [6], aplicada por píxel.
  En los últimos 18 meses del registro no se puede confirmar: se usa la confirmación de 2 pasadas y el
  mes se marca `extension_provisional`.
- **Condición** (`indice_condicion`): mediana del NDVI del manglar aún en pie, estandarizada contra la
  climatología del régimen sano del mismo mes calendario (±1 mes): z = (x − mediana_mes) / σ_robusta.
  Valores < −2 indican estrés fuerte, análogo al corte "extremadamente seco" del SPI [14].
- **Confianza del mes** (`confianza`, de presentación): ALTA si hay ≥ 2 escenas que coinciden (diferencia
  ≤ τ), MEDIA con una sola escena y BAJA si discrepan más que τ. El único corte es τ, el ruido de la zona.

---

## 6. Eventos climáticos detonantes

Todos los umbrales se calculan con el periodo base 1991–2020 de la propia zona. Solo se reportan los
eventos que inician dentro del rango consultado.

| Tipo | Definición | Fuente |
|---|---|---|
| `LLUVIA_TORRENCIAL_CICLON` | Acumulado de 3 días > P99 de los acumulados húmedos (≥ 1 mm) del periodo base; *runs declustering* con pausa de 3 días; el inicio se fecha 2 días antes de la primera excedencia | ETCCDI R99p [9], [10]; [12], [13] |
| `NORTE_SEVERO` | `WS10M_MAX` > P98 local, en la región Golfo de México / Caribe occidental, entre octubre y marzo, con ≥ 50 % de los días del episodio con viento del sector 315°–45° | [17] |
| `VIENTO_SEVERO` | Mismo umbral P98 fuera de esas condiciones (generaliza los Nortes a cualquier latitud) | — |
| `SEQUIA_ESTACIONAL_SEVERA` | SPI-3 (90 días) con ajuste gamma por mes de calendario sobre el periodo base, incluyendo la probabilidad de cero; evento de McKee (desde SPI < 0 hasta el regreso a SPI ≥ 0) que alcanza **SPI ≤ −1.5** | [14], [15] |
| `OLA_CALOR_ALTA_RADIACION` | ≥ 3 días consecutivos con Tmax > P90 de calendario (ventana de 15 días) **y** radiación > P90 de calendario | CTX90pct [16] |

**Ventanas de observación posteriores.** Para tormentas y viento: desde el día siguiente al fin del
evento, con un tope de 120 días. Para sequía y calor: desde el pico, con un tope de 60 días. Se usan las
**dos primeras escenas limpias** de la ventana. Si otro evento cae dentro de la ventana, se reporta en
`confundido_con`, porque la atribución es ambigua.

---

## 7. Evaluación de secuelas en 4 ejes

| Eje | Métrica | Dirección dañina |
|---|---|---|
| Cobertura y Hábitat | NDVI | Descenso |
| Suelo | NDMI | Descenso |
| Agua | MNDWI | Ambas (anegamiento o desecación) |
| Microclima | Acoplamiento térmico LST − T2M | Aumento |

Para el microclima se usa el acoplamiento LST − T2M, y no la LST cruda, para aislar el terreno del clima
de ese día. Además, una anomalía menor que la incertidumbre de `ST_QA` se considera indetectable.

**Climatología del régimen sano:** mediana por mes de calendario con ventana de ±1 mes. Si hay pocas
escenas, se usa la temporada local (los meses húmedos se calculan de la precipitación 1991–2020 de la
zona, por lo que funciona en ambos hemisferios) y, en último caso, el año completo.

**Calibración empírica de la severidad.** Para cada eje se construye la **distribución nula** de lo que
dos pasadas consecutivas muestran en condiciones normales de esa zona:

1. El residuo de cada escena sana se calcula contra una climatología que **la deja fuera** (*leave-one-out*).
2. Se excluyen de la nula las **perturbaciones sostenidas**: ≥ 3 escenas seguidas fuera de 3σ con el
   mismo signo. El ruido no persiste entre pasadas; un daño sí.
3. El estadístico del evento es la anomalía **menos extrema** de las dos escenas posteriores (0 si sus
   signos difieren). Es decir, se exige un daño consistente.
4. **p** = probabilidad empírica de una anomalía igual o mayor. Si la anomalía está más allá de todo lo
   observado, se usa la cola normal con σ robusta.
5. Categorías: **MODERADO** p ≤ 5 %, **ALTO** p ≤ 1 %, **CRÍTICO** p ≤ 0.1 %.

Se reportan dos lecturas por evento:

- `ejes`: estado del terreno frente al **manglar sano**. Muestra cómo queda el territorio sin manglar.
- `respuesta_al_evento`: efecto del evento frente a **su propio régimen**. Aísla el evento de la pérdida
  permanente.

---

## 8. Parámetros

| Parámetro | Valor | Tipo | Justificación |
|---|---|---|---|
| Periodo base de clima | 1991–2020 | Estándar | Normal climatológica OMM [11] |
| Lluvia extrema | P99 de acumulados de 3 días (días húmedos ≥ 1 mm) | Estándar + zona | R99p ETCCDI [9] |
| Pausa entre episodios | 3 días | Estándar | *Runs declustering* [12], [13] |
| Viento severo | P98 local de `WS10M_MAX` | Zona | Umbral relativo por el sesgo de MERRA-2 [23] |
| Nortes | Golfo de México / Caribe occidental, octubre–marzo, sector 315°–45° | Estándar | 20–25 surgencias frías por temporada [17] |
| Sequía | SPI-3 ≤ −1.5 | Estándar | "Severamente seco", OMM [15] |
| Ola de calor | ≥ 3 días con Tmax > P90 de calendario (15 días) | Estándar | CTX90pct [16] |
| Línea base | 24 meses | Ingeniería | Dos ciclos estacionales |
| Escenas mínimas | 12 en total y 6 en la línea base | Ingeniería | Evitar decisiones con datos escasos |
| Filtro de nubes de escena | < 60 % | Ingeniería | Solo eficiencia; decide la QA por píxel |
| Cobertura válida por escena | ≥ 50 % | Ingeniería | Representatividad espacial |
| Confianza de nube QA | Se rechaza media y alta (bits 8–9 ≥ 10) | Estándar | USGS C2 QA [26] |
| Prueba térmica de nube | LST fuera de [−10, 75] °C invalida el píxel | Estándar | Lógica de Fmask |
| Filtro multitemporal | Caída y regreso > 3σ de los cambios píxel a píxel; vecinos ≤ 120 días | Zona + estándar | Tmask/CCDC [27] |
| Confirmación por píxel | 2 observaciones válidas consecutivas | Estándar | CCDC [27] |
| Agua permanente | Otsu (η ≥ 0.5), frecuencia ≥ 80 % | Estándar + zona | [7], [8] |
| MVI de respaldo | ≥ 4.6 (máximo 20); NDVI ≥ 0.30 | Estándar | [1] |
| Piso de NDVI del manglar | mediana − 3σ robusta | Zona | [25] |
| Área mínima | 0.5 ha | Estándar | FAO [24] |
| Pérdida por píxel | max(0.20, 3σ_píxel) | Estándar + zona | [4], [5] |
| Umbral de extensión τ | max(mediana + 3σ robusta de la línea base limpia, 0.5 ha) | Zona | Ruido propio de la zona [25] |
| Extensión persistente | Candidato en ≥ 50 % de las observaciones en [t+12, t+18] meses | Estándar | [6] |
| Condición del dosel | z robusto vs. el mes calendario sano (±1 mes) | Zona | [25] |
| Penalización PELT | 2·log n (BIC) | Estándar | [18] |
| Persistencia de pérdida | Pérdida entre 12 y 18 meses después | Estándar | [6] |
| Evaluación de recuperación | 24 meses | Ingeniería | Dos temporadas de crecimiento |
| Severidad | p ≤ 5 %, 1 %, 0.1 % | Estadístico | Calibración empírica (sección 7) |
| Perturbación sostenida | ≥ 3 escenas fuera de 3σ | Estadístico | Separa ruido de daño |
| Ventana de observación | 2 escenas; tope de 120 / 60 días | Ingeniería | Revisita de 8–16 días |

---

## 9. Validación

### 9.1 Escenarios sintéticos (pruebas automáticas, `tests/test_mango.py`)

Las descargas se sustituyen por datos sintéticos con reflectancias típicas de cada cobertura,
estacionalidad, ruido, nubes y bandeado. **Todo el análisis corre con el código de producción.**
El polígono mide unas 41.6 ha (462 píxeles) y el periodo es 2005–2024, con unas 340 escenas.

| Escenario | Verdad | v1 | v2 | Detalle v2 |
|---|---|---|---|---|
| A: manglar estable | A | A | **A** | 21.9 % de eventos con alguna alerta (esperado ≈ 18.5 %); 0 % críticos |
| B: relleno del 45 % (abril 2012) | B | B | **B** | Quiebre 2012-04-26 (primera pasada); **13.50 ha estimadas = 13.50 ha reales**; conversión a suelo desnudo |
| B_parcial: relleno del 18 % | B | **A (no detecta)** | **B** | **5.49 ha estimadas = 5.49 ha reales** |
| Huracán: 50 % defoliado y rebrote | A | A | **A** | El evento sale CRÍTICO en Cobertura (p < 0.001); no se confunde con pérdida |
| Cultivo verde sin manglar | C | **A (lo acepta)** | **C** | MVI ≈ 1.6 < 4.6 y sin manglar en GMW |
| Zona casi siempre nublada (98 % de pasadas nubladas) | Datos insuficientes | **A** | **INSUFFICIENT_DATA** | 8 escenas en todo el registro (mínimo 12). En la v2.1 eran 93 %, pero con el histórico completo sí había base |
| Ventanas del escenario B (v2.2) | — | — | **B igual** | 2010–2014 y 2015–2020 dan el mismo quiebre (2012-04-26) y el mismo τ que 2005–2024; 2005–2010 da A (el quiebre posterior no cuenta) |
| Hemisferio sur | A | A | **A** | Estaciones calculadas del clima local; sin Nortes |
| Neblina: 6 nubes completas sin bandera QA + 3 brumas (v2.1) | A | — | **A** | 9 escenas descartadas; extensión intacta; condición mínima z −3.25 (sin el filtro: −287) |
| Sequía: 4 meses con NDVI −0.25 y recuperación en 6 (v2.1) | A | — | **A** | Extensión intacta; condición z ≈ −85 en el pico; vuelve a su ruido normal al año siguiente |

Además, las pruebas verifican:
- La calibración del SPI en el periodo base: media ≈ 0, desviación ≈ 1 y P(SPI ≤ −1.5) ≈ 6.7 %.
- La tasa de excedencia de CTX90pct ≈ 10 %.
- El *declustering*, Otsu, los reintentos de red, la lectura de COG locales con `ST_QA` y la lectura de
  GMW desde el zip.
- La compatibilidad del JSON con la v1 y la ausencia de NaN (RFC 8259).

### 9.2 Escenarios reales (regresión v1 contra v2)

`scripts/regresion_mango.py` ejecuta ambas versiones sobre los 4 escenarios validados de la v1
(Mandinga Sur, Marina Punta Tiburón, Centro urbano de Veracruz y Cananéia-Iguape). Criterios de
aceptación:
- Mandinga Sur sigue siendo A.
- Punta Tiburón sigue siendo B, con el quiebre a ≤ 183 días del 2009-04-17.
- El Centro urbano sigue siendo C.
- Cananéia sigue siendo A.

Caso real agregado en la v2.1, `mandinga_zona_usuario` (2000-06 → 2026-06), solo con v2:
- Ningún mes de extensión < 1 − τ fuera de un quiebre confirmado.
- La sequía de 2011 (feb–may) aparece en la condición (z < −2) y **no** en la extensión.
- El mes 2023-12 (nube fría) no aparece como pérdida.
- Sensibilidad: τ·núcleo < 2.4 ha, la baja que GMW registra en ese recuadro entre 2010 y 2014. Es una
  comprobación de orden de magnitud: la exactitud de GMW en cambios es de 58–61 %.
- Si sale Caso B: quiebre entre 2011 y 2015 con 0.5 a 5 ha.

La salida de la v2.0 sobre esa zona falla los cinco criterios; ese fue el diagnóstico que motivó la v2.1.

*Resultados: pendientes de ejecutar con acceso a internet (sección 12).*

---

## 10. Correcciones de diseño durante la auditoría

Durante el desarrollo se detectaron y corrigieron, con evidencia, dos criterios del plan inicial:

1. **Severidad con cortes del SPI (|z| ≥ 1, 1.5, 2) sobre σ = 1.4826·MAD.** En el manglar sano sintético,
   el **46 %** de los eventos salía con alguna alerta y el 9 % como crítico. Las causas eran colas más
   pesadas que la normal (nubes parciales, bandeado) y la multiplicidad de 4 ejes. Se reemplazó por la
   calibración empírica de la sección 7: **21.9 %** con alguna alerta y **0 %** críticos.
2. **Atribución con una ventana fija de 90 días.** El relleno sintético (de origen humano) quedaba
   asociado por azar a un Norte. Se reemplazó por el intervalo entre observaciones más el filtro de
   intensidad (sección 5.6).

También se corrigieron dos afirmaciones de la auditoría inicial:
- La diferencia entre el MVI con signo y con valores absolutos es menor de lo que se planteó; el
  problema principal de la v1 eran sus umbrales (0.70 y 0.85) frente al 4.6 publicado.
- La v1 sí detecta pérdidas mayores al ~25 % del núcleo gracias a su percentil 25; lo que no detecta son
  las pérdidas parciales menores.

**Correcciones de la v2.1**, a partir de una consulta real en Mandinga (2000–2026, 411 escenas):
- Meses con "0 ha" por una sola escena con nube que la QA no marcó (2023-12: NDVI 0.03, MNDWI +0.13 y
  temperatura superficial < −10 °C). Corregido con la confianza QA, la prueba térmica, el filtro
  multitemporal y la confirmación por píxel (secciones 3, 3.1 y 5).
- Estrés contado como pérdida (la sequía de 2011–2012, la más severa en ~70 años en México, y una
  inundación en julio de 2021). Corregido separando extensión y condición (sección 5.1).
- Umbral de ruido fijado por una sola escena (P99 con 19 escenas). Corregido con el estimador robusto.
- **Autocorrección:** en la v2.0 se afirmó que la armonización ETM+→OLI era "innecesaria en Colección 2".
  Fue una afirmación demasiado fuerte; ahora el sesgo se mide antes de decidir (sección 3, punto 6).

---

## 11. Limitaciones

- **Resolución del clima:** NASA POWER promedia unos 50 km. Eventos muy locales (tormentas convectivas)
  pueden no verse, y el viento no representa ráfagas. Conviene validar con estaciones en tierra
  (NOAA CDO).
- **Nubes:** en temporada de lluvias o de Nortes puede no haber escenas limpias en la ventana posterior
  (`SIN_OBSERVACION_POSTERIOR`). El radar Sentinel-1 sería el complemento natural.
- **Marea:** la marea al momento del paso afecta el MNDWI del manglar intermareal. La frecuencia del
  80 % y la calibración empírica lo amortiguan, pero no lo eliminan.
- **GMW:** tiene omisión del 5.65–7.13 % y comisión del 7.10–9.13 % [3], y su serie temprana se rellena
  en algunas regiones.
- **Atribución:** la asociación con eventos es temporal, no causal. La atribución a actividad humana o a
  fenómenos naturales se apoya en la trayectoria posterior (conversión contra recuperación) y en el
  destino de la cobertura.
- **Rectángulo:** el análisis usa un bbox; los polígonos irregulares se analizan por su envolvente.
- **Fenómenos de menos de una revisita:** una inundación o un evento que solo se ve en una escena y no se
  repite en la siguiente se descarta junto con las nubes (sección 3.1).
- **Extensión reciente:** en los últimos 18 meses la extensión es provisional (confirmación de 2 pasadas).
- **Primera consulta de una zona:** descarga el histórico completo (del orden de 600–900 escenas); tarda
  más que las siguientes, que salen de la caché local.

---

## 12. Reproducibilidad

- `version_pipeline`, `umbrales_aplicados` y `referencias` vienen en cada resultado.
- Los IDs de eventos son deterministas (hash de tipo y fechas).
- Validación real:
  ```bash
  GMW_RUTA=RUTA/gmw.zip uv run python scripts/regresion_mango.py
  ```
- Pruebas:
  ```bash
  uv run pytest -q
  ```

---

## Referencias

[1] A. B. Baloloy, A. C. Blanco, R. R. C. Sta. Ana y K. Nadaoka, "Development and application of a new mangrove vegetation index (MVI) for rapid and accurate mangrove mapping," *ISPRS J. Photogramm. Remote Sens.*, vol. 166, pp. 95–117, 2020.

[2] M. P. Neri, A. B. Baloloy y A. C. Blanco, "Limitation assessment and workflow refinement of the mangrove vegetation index (MVI)-based mapping methodology using Sentinel-2 imagery," *Int. Arch. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. XLVI-4/W6-2021, pp. 235–242, 2021.

[3] Global Mangrove Watch, "Timeseries of Mangrove Extent (Version 4.1.12)," conjunto de datos, Zenodo / JAXA EORC, 2026. Aberystwyth University, solo Earth Observation, Wetlands International, The Nature Conservancy y JAXA. CC BY 4.0.

[4] L. Goldberg, D. Lagomasino, N. Thomas y T. Fatoyinbo, "Global declines in human-driven mangrove loss," *Glob. Change Biol.*, vol. 26, no. 10, pp. 5844–5855, 2020.

[5] D. Lagomasino *et al.*, "Measuring mangrove carbon loss and gain in deltas," *Environ. Res. Lett.*, vol. 14, no. 2, 025002, 2019.

[6] X. Yang, Z. Zhu, K. D. Kroeger, S. Qiu, S. Covington, J. R. Conrad y Z. Zhu, "Tracking mangrove condition changes using dense Landsat time series," *Remote Sens. Environ.*, vol. 315, 114461, 2024.

[7] H. Xu, "Modification of normalised difference water index (NDWI) to enhance open water features in remotely sensed imagery," *Int. J. Remote Sens.*, vol. 27, no. 14, pp. 3025–3033, 2006.

[8] N. Otsu, "A threshold selection method from gray-level histograms," *IEEE Trans. Syst., Man, Cybern.*, vol. 9, no. 1, pp. 62–66, 1979.

[9] X. Zhang *et al.*, "Indices for monitoring changes in extremes based on daily temperature and precipitation data," *WIREs Clim. Change*, vol. 2, no. 6, pp. 851–870, 2011.

[10] X. Zhang, G. Hegerl, F. W. Zwiers y J. Kenyon, "Avoiding inhomogeneity in percentile-based indices of temperature extremes," *J. Climate*, vol. 18, no. 11, pp. 1641–1651, 2005.

[11] World Meteorological Organization, *WMO Guidelines on the Calculation of Climate Normals*, WMO-No. 1203, 2017.

[12] A. C. Davison y R. L. Smith, "Models for exceedances over high thresholds," *J. R. Stat. Soc. B*, vol. 52, no. 3, pp. 393–442, 1990.

[13] S. Coles, *An Introduction to Statistical Modeling of Extreme Values*. Londres: Springer, 2001.

[14] T. B. McKee, N. J. Doesken y J. Kleist, "The relationship of drought frequency and duration to time scales," en *Proc. 8th Conf. Applied Climatology*, Anaheim, CA, 1993, pp. 179–184.

[15] World Meteorological Organization, *Standardized Precipitation Index User Guide* (M. Svoboda, M. Hayes y D. Wood), WMO-No. 1090, Ginebra, 2012.

[16] S. E. Perkins y L. V. Alexander, "On the measurement of heat waves," *J. Climate*, vol. 26, no. 13, pp. 4500–4517, 2013.

[17] E. P. Pérez, V. Magaña, E. Caetano y S. Kusunoki, "Cold surge activity over the Gulf of Mexico in a warmer climate," *Front. Earth Sci.*, vol. 2, art. 19, 2014.

[18] R. Killick, P. Fearnhead e I. A. Eckley, "Optimal detection of changepoints with a linear computational cost," *J. Amer. Statist. Assoc.*, vol. 107, no. 500, pp. 1590–1598, 2012.

[19] C. Truong, L. Oudre y N. Vayatis, "Selective review of offline change point detection methods," *Signal Process.*, vol. 167, 107299, 2020.

[20] H. B. Mann, "Nonparametric tests against trend," *Econometrica*, vol. 13, pp. 245–259, 1945; P. K. Sen, "Estimates of the regression coefficient based on Kendall's tau," *J. Amer. Statist. Assoc.*, vol. 63, no. 324, pp. 1379–1389, 1968.

[21] K. G. Laraby y J. R. Schott, "Uncertainty estimation method and Landsat 7 global validation for the Landsat surface temperature product," *Remote Sens. Environ.*, vol. 216, pp. 472–481, 2018.

[22] D. P. Roy *et al.*, "Characterization of Landsat-7 to Landsat-8 reflective wavelength and normalized difference vegetation index continuity," *Remote Sens. Environ.*, vol. 185, pp. 57–70, 2016.

[23] R. Gelaro *et al.*, "The Modern-Era Retrospective Analysis for Research and Applications, Version 2 (MERRA-2)," *J. Climate*, vol. 30, no. 14, pp. 5419–5454, 2017; NASA POWER, documentación de metodología meteorológica.

[24] FAO, *Global Forest Resources Assessment 2020: Terms and Definitions*, FRA 2020 Working Paper 188, Roma, 2018.

[25] P. J. Rousseeuw y C. Croux, "Alternatives to the median absolute deviation," *J. Amer. Statist. Assoc.*, vol. 88, no. 424, pp. 1273–1283, 1993.

[26] U.S. Geological Survey, "Landsat Collection 2 Quality Assessment Bands" (QA_PIXEL: bit 3 = nube de alta confianza; bits 8–9 = confianza de nube), USGS Landsat Missions, consultado en 2026.

[27] Z. Zhu y C. E. Woodcock, "Continuous change detection and classification of land cover using all available Landsat data," *Remote Sens. Environ.*, vol. 144, pp. 152–171, 2014.
