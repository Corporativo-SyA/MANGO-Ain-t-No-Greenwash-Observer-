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

# Referencias

## Datos

- Bunting, P., Hilarides, L., Rosenqvist, A., Oakes, G., Castro Brandão Vaz dos Santos, P., Worthington, T., Rosenqvist, J., Zanaga, D., Van De Kerchove, R., Tadono, T., & Kobayashi, K. (2026). *Global Mangrove Watch: Timeseries of mangrove extent (Version 4.1.12)* [Data set]. Zenodo. https://doi.org/10.5281/zenodo.21346456
  - **Uso:** referencia externa de hectáreas y capa en el mapa. Explícito. Da la extensión de manglar de 1985 a 2025, con píxeles de 30 m.
  - **Su exactitud:** un F1 global de 0.9220–0.9330.
- Bunting, P., Rosenqvist, A., Hilarides, L., Lucas, R. M., Thomas, N., Tadono, T., Worthington, T. A., Spalding, M., Murray, N. J., & Rebelo, L.-M. (2022). Global mangrove extent change 1996–2020: Global Mangrove Watch version 3.0. *Remote Sensing*, *14*(15), 3657. https://doi.org/10.3390/rs14153657
  - **Uso:** dato de exactitud de GMW. Explícito. Estimaron una exactitud de 87.4% en extensión, pero menor en las clases de ganancia y pérdida (58.1% y 60.6%).
  - Esto sirve para decir en la presentación que GMW es referencia y no verdad absoluta.
- Earth Resources Observation and Science (EROS) Center. (2020). *Landsat 4-5 Thematic Mapper Level-2, Collection 2* [Data set]. U.S. Geological Survey. https://doi.org/10.5066/P9IAXOVV
  - Explícito. Formato oficial confirmado en USGS.
- Earth Resources Observation and Science (EROS) Center. (2020). *Landsat 7 Enhanced Thematic Mapper Plus Level-2, Collection 2* [Data set]. U.S. Geological Survey. https://doi.org/10.5066/P9C7I13B
  - Explícito. DOI confirmado; el título sigue el patrón de la anterior.
- Earth Resources Observation and Science (EROS) Center. (2020). *Landsat 8-9 Operational Land Imager / Thermal Infrared Sensor Level-2, Collection 2* [Data set]. U.S. Geological Survey. https://doi.org/10.5066/P9OGBGM6
  - Explícito. Mismo caso: DOI confirmado, título por patrón.
- Crawford, C. J., Roy, D. P., Arab, S., Barnes, C., Vermote, E., Hulley, G., Gerace, A., Choate, M., Engebretson, C., Micijevic, E., Schmidt, G., Anderson, C., Anderson, M., Bouchard, M., Cook, B., Dittmeier, R., Howard, D., Jenkerson, C., Kim, M., … Zahn, S. (2023). The 50-year Landsat collection 2 archive. *Science of Remote Sensing*, *8*, 100103. https://doi.org/10.1016/j.srs.2023.100103
  - **Uso:** justificar que los sensores son comparables (Colección 2 armonizada). Explícito.
  - Entre las mejoras de la Colección 2 están productos Nivel 2 globales y bandas de calidad consistentes.
  - **Formato APA 7:** con 21 o más autores van los primeros 19, “…” y el último.
- U.S. Geological Survey. (2024). *Landsat 8-9 Collection 2 (C2) Level 2 Science Product (L2SP) Guide* (LSDS-1619, Version 6.0). https://www.usgs.gov/media/files/landsat-8-collection-2-level-2-science-product-guide
  - Explícito. Versión 6.0, mayo de 2024.
- U.S. Geological Survey. (n.d.). *Landsat 4-7 Collection 2 Level 2 Science Product Guide* (LSDS-1618). https://www.usgs.gov/media/files/landsat-4-7-collection-2-level-2-science-product-guide
  - Explícito. Usen la versión que aparezca al descargarla.
- U.S. Geological Survey. (n.d.). *Landsat Collection 2 quality assessment bands*. https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands
  - **Uso:** máscara `QA_PIXEL`, bits 0–4 y 8–9. Explícito. Los bits 8–9 son la confianza de nube: 01 baja, 10 media, 11 alta.
  - **Matiz:** en Landsat 4‑7 el valor 2 de confianza está “reservado”, así que “confianza media” solo existe de verdad en Landsat 8‑9.
- Microsoft. (2022). *microsoft/PlanetaryComputer: October 2022* (Version 2022.10.28) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.7261897
  - **Uso:** catálogo STAC de donde descargamos Landsat. Explícito. Microsoft indica usar este DOI para citar el Planetary Computer en general.
  - Pueden añadir la colección usada: https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2
- NASA Langley Research Center. (n.d.). *POWER Project data* [Data set]. NASA Prediction Of Worldwide Energy Resources. Retrieved [fecha], from https://power.larc.nasa.gov/
  - Explícito. La guía oficial pide incluir la frase de referencia de POWER, el nombre del servicio, la versión y la fecha de acceso.
  - Agreguen en agradecimientos: “The data was obtained from National Aeronautics and Space Administration (NASA) Langley Research Center’s Prediction Of Worldwide Energy Resources (POWER) project funded through the NASA Earth Science Division.”
- Gelaro, R., McCarty, W., Suárez, M. J., Todling, R., Molod, A., Takacs, L., Randles, C. A., Darmenov, A., Bosilovich, M. G., Reichle, R., Wargan, K., Coy, L., Cullather, R., Draper, C., Akella, S., Buchard, V., Conaty, A., da Silva, A. M., Gu, W., … Zhao, B. (2017). The Modern-Era Retrospective Analysis for Research and Applications, version 2 (MERRA-2). *Journal of Climate*, *30*(14), 5419–5454. https://doi.org/10.1175/JCLI-D-16-0758.1
  - **Uso:** base meteorológica de POWER y origen de la celda de ~50 km. Implícito: no usamos MERRA-2 directamente, pero POWER reporta valores de MERRA-2 promediados en regiones de 0.5 × 0.625 grados.
  - **Ojo:** solo confirmé los primeros autores y el último. Los intermedios salen de mi memoria; verifíquenlos en el DOI.

## Índices espectrales

- Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974). Monitoring vegetation systems in the Great Plains with ERTS. In *Third Earth Resources Technology Satellite-1 Symposium* (NASA SP-351, Vol. 1, pp. 309–317). NASA.
  - **Uso:** NDVI. Explícito (origen del índice).
- Baloloy, A. B., Blanco, A. C., Sta. Ana, R. R. C., & Nadaoka, K. (2020). Development and application of a new mangrove vegetation index (MVI) for rapid and accurate mangrove mapping. *ISPRS Journal of Photogrammetry and Remote Sensing*, *166*, 95–117. https://doi.org/10.1016/j.isprsjprs.2020.06.001
  - **Uso:** MVI para la máscara de manglar. Explícito. También lo probaron en Landsat 8 con las bandas equivalentes.
  - **Matiz:** la fórmula original usa valores absolutos, $|\text{NIR}-\text{Green}|/|\text{SWIR}-\text{Green}|$. En el banco de preguntas la escribí sin ellos; revisen cuál implementa el código.
- Neri, M. P., Baloloy, A. B., & Blanco, A. C. (2021). Limitation assessment and workflow refinement of the Mangrove Vegetation Index (MVI)-based mapping methodology using Sentinel-2 imagery. *The International Archives of the Photogrammetry, Remote Sensing and Spatial Information Sciences*, *XLVI-4/W6-2021*, 235–242. https://doi.org/10.5194/isprs-archives-XLVI-4-W6-2021-235-2021
  - **Uso:** respaldar por qué no usamos MVI para el cambio mes a mes. Implícito.
  - Señalan que los valores de MVI varían con las condiciones de marea y que hay errores en zonas de acuicultura y cultivos irrigados.
  - El argumento del denominador cercano a cero es razonamiento nuestro, no de este artículo.
- McFeeters, S. K. (1996). The use of the Normalized Difference Water Index (NDWI) in the delineation of open water features. *International Journal of Remote Sensing*, *17*(7), 1425–1432. https://doi.org/10.1080/01431169608948714
  - **Uso:** NDWI (agua, con verde y NIR). Explícito.
- Xu, H. (2006). Modification of normalised difference water index (NDWI) to enhance open water features in remotely sensed imagery. *International Journal of Remote Sensing*, *27*(14), 3025–3033. https://doi.org/10.1080/01431160600589179
  - **Uso:** MNDWI (agua, con verde y SWIR). Explícito.
- Gao, B.-C. (1996). NDWI—A normalized difference water index for remote sensing of vegetation liquid water from space. *Remote Sensing of Environment*, *58*(3), 257–266. https://doi.org/10.1016/S0034-4257(96)00067-3
  - **Uso:** NDMI (humedad del dosel). Implícito. Gao usó los canales de 0.86 y 1.24 µm; nosotros usamos NIR y SWIR1 de Landsat (~1.6 µm). Es la adaptación habitual para Landsat, no la fórmula original.

## Nubes y calidad

- Zhu, Z., & Woodcock, C. E. (2012). Object-based cloud and cloud shadow detection in Landsat imagery. *Remote Sensing of Environment*, *118*, 83–94. https://doi.org/10.1016/j.rse.2011.10.028
  - **Uso:** prueba térmica. Implícito. Fmask usa reflectancia y temperatura de brillo, con una probabilidad de temperatura normalizada.
  - Nosotros no implementamos Fmask; solo aplicamos el mismo principio de que las nubes son más frías.
- Zhu, Z., & Woodcock, C. E. (2014). Automated cloud, cloud shadow, and snow detection in multitemporal Landsat data: An algorithm designed specifically for monitoring land cover change. *Remote Sensing of Environment*, *152*, 217–234. https://doi.org/10.1016/j.rse.2014.06.012
  - **Uso:** filtro de picos “estilo Tmask”. Implícito.
  - Tmask ajusta un modelo temporal por píxel y detecta nubes que quedaron comparando el modelo con cada observación.
  - Nuestro filtro es una versión simplificada (diferencias con las pasadas vecinas), no el algoritmo completo. Díganlo así.
- Zhu, Z., Woodcock, C. E., Holden, C., & Yang, Z. (2015). Generating synthetic Landsat images based on all available Landsat data: Predicting Landsat surface reflectance at any given time. *Remote Sensing of Environment*, *162*, 67–83. https://doi.org/10.1016/j.rse.2015.02.009
  - **Uso:** confirmar cambios con observaciones consecutivas. Implícito.
  - Ellos declaran quiebre si la diferencia supera dos veces el RMSE en seis observaciones consecutivas. Nosotros pedimos dos: mismo principio, umbral distinto.
- Storey, J. C., Scaramuzza, P., Schmidt, G. L., & Barsi, J. (2005). Landsat 7 scan line corrector-off gap-filled product development. In *Global priorities in land remote sensing*. ASPRS.
  - **Uso:** explicar las franjas sin datos de Landsat 7 desde 2003. Explícito.
  - El corrector falló el 31 de mayo de 2003 y faltan cerca del 22% de cada escena.

## Estadística

- Killick, R., Fearnhead, P., & Eckley, I. A. (2012). Optimal detection of changepoints with a linear computational cost. *Journal of the American Statistical Association*, *107*(500), 1590–1598. https://doi.org/10.1080/01621459.2012.737745
  - **Uso:** PELT. Explícito.
- Schwarz, G. (1978). Estimating the dimension of a model. *The Annals of Statistics*, *6*(2), 461–464. https://doi.org/10.1214/aos/1176344136
  - **Uso:** BIC como penalización para elegir cuántos quiebres aceptar. Explícito para el BIC. Combinarlo con PELT es decisión nuestra, aunque Killick et al. discuten penalizaciones lineales en el número de quiebres.
- Leys, C., Ley, C., Klein, O., Bernard, P., & Licata, L. (2013). Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median. *Journal of Experimental Social Psychology*, *49*(4), 764–766. https://doi.org/10.1016/j.jesp.2013.03.013
  - **Uso:** umbral $\tau = \text{mediana} + 3 \cdot \text{MAD}$. Explícito. Recomiendan 2.5 o 3.0 como corte.
- Rousseeuw, P. J., & Croux, C. (1993). Alternatives to the median absolute deviation. *Journal of the American Statistical Association*, *88*(424), 1273–1283. https://doi.org/10.1080/01621459.1993.10476408
  - **Uso:** la equivalencia MAD ↔ desviación estándar, con la constante 1.4826. Respalda la frase “3 MAD ≈ 2 desviaciones estándar”. Explícito.

## Clima y eventos

- World Meteorological Organization. (2017). *WMO guidelines on the calculation of climate normals* (WMO-No. 1203). https://library.wmo.int/records/item/55797-wmo-guidelines-on-the-calculation-of-climate-normals
  - **Uso:** climatología 1991–2020. Explícito. La OMM recomienda usar el periodo de 30 años más reciente, 1991–2020.
- McKee, T. B., Doesken, N. J., & Kleist, J. (1993). The relationship of drought frequency and duration to time scales. In *Proceedings of the 8th Conference on Applied Climatology* (pp. 179–184). American Meteorological Society.
  - **Uso:** SPI. Explícito (origen del índice).
- World Meteorological Organization. (2012). *Standardized Precipitation Index user guide* (M. Svoboda, M. Hayes, & D. Wood; WMO-No. 1090). WMO.
  - **Uso:** SPI-3 ≤ −1.5 como sequía. Explícito. La tabla de la OMM clasifica −1.5 a −1.99 como “severely dry”.
- Zhang, X., Alexander, L., Hegerl, G. C., Jones, P., Klein Tank, A., Peterson, T. C., Trewin, B., & Zwiers, F. W. (2011). Indices for monitoring changes in extremes based on daily temperature and precipitation data. *WIREs Climate Change*, *2*(6), 851–870. https://doi.org/10.1002/wcc.147
  - **Uso:** lluvia extrema R99p y la idea de usar percentiles locales. Implícito para R99p: en ETCCDI, R99p es el total anual de lluvia de los días sobre el percentil 99. Nosotros marcamos como evento cada día sobre ese percentil: mismo umbral, otra métrica.
  - Explícito para el uso de percentiles: los índices de extremos ahora suelen usar umbrales relativos, porque pocos umbrales físicos sirven en todo el mundo.
- Perkins, S. E., & Alexander, L. V. (2013). On the measurement of heat waves. *Journal of Climate*, *26*(13), 4500–4517. https://doi.org/10.1175/JCLI-D-12-00383.1
  - **Uso:** ola de calor CTX90. Explícito. Definen ola de calor como tres o más días seguidos sobre el percentil 90 de temperatura máxima, entre otras definiciones.

## Contexto (impacto)

- Donato, D. C., Kauffman, J. B., Murdiyarso, D., Kurnianto, S., Stidham, M., & Kanninen, M. (2011). Mangroves among the most carbon-rich forests in the tropics. *Nature Geoscience*, *4*(5), 293–297. https://doi.org/10.1038/ngeo1123
  - **Uso:** por qué importan los manglares. Explícito para el carbono.
  - En promedio guardan 1,023 Mg de carbono por hectárea, y su tala genera hasta ~10% de las emisiones globales por deforestación, aunque ocupan solo 0.7% del área de bosque tropical.

## Software

- Harris, C. R., Millman, K. J., van der Walt, S. J., Gommers, R., Virtanen, P., Cournapeau, D., Wieser, E., Taylor, J., Berg, S., Smith, N. J., Kern, R., Picus, M., Hoyer, S., van Kerkwijk, M. H., Brett, M., Haldane, A., del Río, J. F., Wiebe, M., Peterson, P., … Oliphant, T. E. (2020). Array programming with NumPy. *Nature*, *585*(7825), 357–362. https://doi.org/10.1038/s41586-020-2649-2
