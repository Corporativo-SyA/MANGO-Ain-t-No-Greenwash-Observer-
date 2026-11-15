"""
MANGO v2 — MANGO Ain't No Greenwash Observer
=============================================

Motor científico de tendencias e impacto en manglares. Desacopla:
  1. Forzante climático (NASA POWER, diario, centroide del bbox).
  2. Respuesta del terreno (Landsat C2 L2 5/7/8/9 a 30 m, Planetary Computer STAC).
  3. Extensión de referencia del manglar (Global Mangrove Watch v4.1, 30 m, 1985-2025).

Principio de diseño v2: NINGÚN umbral es un número arbitrario. Cada umbral es
  (a) un estándar publicado (citado en `REFERENCIAS`), o
  (b) un estadístico de la propia zona: su clima 1991-2020, su manglar en la
      línea base o el ruido medido de sus propias series.
Todos los umbrales efectivamente usados se devuelven en `umbrales_aplicados`.

Contrato de salida: compatible con v1 (mismas claves); v2 solo AGREGA campos.
La versión original validada se conserva en mango_v1_referencia.py.

Uso:
    from app.pipeline.mango import run_mango_pipeline, PipelineConfig
    json_str = run_mango_pipeline((-96.0897, 19.0554, -96.0787, 19.0654),
                                  "2005-01-01", "2024-12-31")
CLI:
    python -m app.pipeline.mango --bbox -96.0897 19.0554 -96.0787 19.0654 \
        --start-date 2005-01-01 --end-date 2024-12-31 --gmw RUTA/gmw.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import threading
import time
import warnings
import zipfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import planetary_computer
import pyproj
import pystac_client
import rasterio
import requests
import ruptures as rpt
import shapely
from pystac_client.stac_api_io import StacApiIO
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from requests.adapters import HTTPAdapter
from scipy import stats
from shapely.geometry import Polygon, box, mapping
from urllib3.util.retry import Retry

PIPELINE_VERSION = "2.2.0"

# Callback de progreso: (porcentaje 0-100, texto para el usuario)
Progreso = Callable[[float, str], None]


def _sin_progreso(_p: float, _t: str) -> None:
    pass


class FuenteDatosError(RuntimeError):
    """Una fuente externa (NASA POWER, STAC, COG) no respondió tras los reintentos."""

    def __init__(self, fuente: str, mensaje: str):
        super().__init__(f"[{fuente}] {mensaje}")
        self.fuente = fuente


REFERENCIAS = {
    "baloloy2020": "Baloloy et al. (2020) ISPRS J. Photogramm. 166:95-117 — MVI=|NIR-G|/|SWIR1-G|, "
                   "umbral mínimo óptimo 4.6 en Landsat 8, máximo 20.",
    "neri2021": "Neri, Baloloy & Blanco (2021) ISPRS Arch. XLVI-4/W6 — el rango del MVI varía por "
                "sitio/clima/marea; Otsu no separa bien MVI.",
    "gmw41": "Global Mangrove Watch v4.1 (2026), JAXA/Aberystwyth — extensión anual 1985-2025, 30 m, "
             "F1 global 0.92-0.93. ©Global Mangrove Watch, CC BY 4.0.",
    "goldberg2020": "Goldberg et al. (2020) Global Change Biology 26:5844 — anomalía de NDVI de -0.2 "
                    "respecto a la referencia como pérdida permanente (basado en Lagomasino et al. 2019).",
    "yang2024": "Yang et al. (2024) Remote Sens. Environ. 315:114461 (USGS) — perturbado con rebote "
                "dentro de una temporada vs. en recuperación vs. en declive.",
    "fao_fra": "FAO FRA — superficie mínima de bosque 0.5 ha (unidad mínima cartografiable).",
    "xu2006": "Xu (2006) Int. J. Remote Sens. 27:3025 — MNDWI, umbral 0.",
    "otsu1979": "Otsu (1979) IEEE Trans. SMC 9:62 — umbral por máxima varianza entre clases.",
    "etccdi": "ETCCDI / Zhang et al. (2011) — percentiles sobre periodo base; R99p sobre días húmedos "
              "(>= 1 mm); percentil por día de calendario con ventana móvil.",
    "wmo_normals": "OMM — periodo normal climatológico estándar 1991-2020.",
    "davison_smith1990": "Davison & Smith (1990) JRSS-B 52:393 / Coles (2001) — runs declustering; "
                         "cúmulo termina tras 3 días consecutivos bajo el umbral.",
    "mckee1993": "McKee, Doesken & Kleist (1993) — SPI; sequía inicia con SPI<0 y alcanza <=-1.0; "
                 "categorías -1.0/-1.5/-2.0 (WMO-No. 1090, 2012).",
    "perkins2013": "Perkins & Alexander (2013) J. Climate 26:4500 — ola de calor: >=3 días consecutivos "
                   "con Tmax > P90 de calendario (ventana de 15 días).",
    "magana2003": "Magaña et al. (2003); Pérez et al. (2014) Front. Earth Sci. 2:19 — Nortes: 20-25 "
                  "surgencias frías en el Golfo de México de octubre a marzo.",
    "power_merra2": "NASA POWER — MERRA-2 1/2° x 5/8°, horario agregado a diario; WS10M_MAX tiende a "
                    "subestimarse en América (umbrales relativos, no absolutos).",
    "usgs_st": "USGS Landsat C2 ST: ST = DN*0.00341802 + 149.0 K; ST_QA = incertidumbre (0.01 K).",
    "killick2012": "Killick, Fearnhead & Eckley (2012) JASA 107:1590 — PELT; penalización BIC "
                   "lambda = 2 sigma^2 log n con sigma estimada por MAD de diferencias.",
    "mann_kendall_sen": "Mann (1945), Kendall (1975), Sen (1968) — tendencia sobre la serie mensual "
                        "desestacionalizada (pendiente por año).",
    "rousseeuw_croux1993": "Rousseeuw & Croux (1993) JASA 88:1273 — 1.4826*MAD como desviación robusta.",
    "usgs_c2_qa": "USGS Landsat Collection 2 Quality Assessment Bands — QA_PIXEL bit 3 solo marca nube de "
                  "ALTA confianza; bits 8-9 = confianza de nube (00 sin nivel, 01 baja, 10 media, 11 alta).",
    "zhu_woodcock2014": "Zhu & Woodcock (2014) Remote Sens. Environ. 144:152-171 — CCDC; filtro "
                        "multitemporal (Tmask) para nubes que la máscara de una sola escena omite.",
    "roy2016_c2": "Roy et al. (2016) Remote Sens. Environ. 185:57-70 — armonización ETM+->OLI NO aplicada "
                  "aún; el sesgo entre sensores se mide con scripts/sesgo_sensores.py antes de decidir.",
}


# ==============================================================================
# MÓDULO 0: CONFIGURACIÓN Y CONTRATOS INTERNOS
# ==============================================================================

@dataclass(frozen=True)
class PipelineConfig:
    # --- Fuentes -------------------------------------------------------------
    nasa_power_url: str = "https://power.larc.nasa.gov/api/temporal/daily/point"
    stac_api_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    stac_collection: str = "landsat-c2-l2"
    gmw_path: str | None = None            # zip o carpeta con mosaicos GMW v4.1 (opcional)
    # --- Red / rendimiento (ingeniería) ---------------------------------------
    http_retries: int = 4
    http_backoff_s: float = 2.0
    http_timeout_s: float = 120.0
    max_workers: int = 8
    max_scene_retries: int = 2
    # --- Landsat (ingeniería con justificación) ------------------------------
    pixel_size_m: float = 30.0             # resolución nativa Landsat
    max_stac_cloud_cover: float = 60.0     # PREFILTRO de escena (~185 km); la QA por píxel decide
    qa_cloud_conf_max: int = 1             # QA_PIXEL bits 8-9: se aceptan 0 (sin nivel) y 1 (baja)
    cache_dir: str | None = None           # caché local de escenas Landsat y clima (None = sin caché)
    landsat_historia_completa: bool = True  # línea base y calibración con TODO el registro de la zona
    landsat_history_start: str = "1984-03-01"  # inicio de Landsat 5 TM
    min_valid_polygon_coverage: float = 0.50
    # --- Climatología (OMM / ETCCDI) -----------------------------------------
    climatology_start: str = "1981-01-01"  # inicio de MERRA-2 en POWER
    base_period: tuple[int, int] = (1991, 2020)
    min_base_years: int = 10
    # --- Línea base y admisibilidad ------------------------------------------
    initial_reference_months: int = 24     # dos ciclos estacionales
    min_scenes_baseline: int = 6
    min_scenes_total: int = 12
    permanent_water_freq_thresh: float = 0.80   # intermareal: agua en >=80% de la línea base
    mndwi_fallback_thresh: float = 0.0          # Xu (2006) si el histograma no es bimodal
    otsu_min_separability: float = 0.50         # eta de Otsu mínimo para aceptar bimodalidad
    mvi_literature_min: float = 4.6             # Baloloy (2020), Landsat 8
    mvi_literature_max: float = 20.0            # Baloloy (2020)
    ndvi_vegetation_min: float = 0.30           # piso de vegetación densa (respaldo espectral)
    mvi_singularity_eps: float = 0.005          # estabilidad numérica
    min_mangrove_area_ha: float = 0.5           # FAO: unidad mínima
    robust_k: float = 3.0                       # desviaciones robustas para "fuera de lo natural"
    # --- Pérdida estructural --------------------------------------------------
    pixel_loss_ndvi_drop: float = 0.20          # Goldberg (2020) / Lagomasino (2019)
    loss_persistence_months: int = 12           # Yang (2024): no rebota en una temporada
    recovery_assessment_months: int = 24
    # --- Eventos --------------------------------------------------------------
    wet_day_mm: float = 1.0                     # ETCCDI
    rain_accum_days: int = 3
    rain_percentile: float = 99.0               # R99p
    wind_percentile: float = 98.0
    decluster_run_days: int = 3                 # Davison & Smith (1990)
    norte_region: tuple[float, float, float, float] = (-98.5, 14.5, -80.0, 31.0)  # Golfo + Caribe occ.
    norte_months: tuple[int, ...] = (10, 11, 12, 1, 2, 3)
    norte_sector_deg: tuple[float, float] = (315.0, 45.0)
    spi_scale_days: int = 90                    # SPI-3
    spi_drought_onset: float = -1.0             # McKee (1993)
    spi_severe: float = -1.5                    # WMO: severamente seco
    heat_percentile: float = 90.0               # Perkins & Alexander (2013)
    heat_window_days: int = 15
    heat_min_days: int = 3
    radiation_percentile: float = 90.0
    # --- Ventanas post-evento -------------------------------------------------
    obs_target_scenes: int = 2
    storm_window_max_days: int = 120
    radiative_window_max_days: int = 60
    loss_event_link_days: int = 90
    # --- Severidad (probabilidad empírica bajo variabilidad natural) ----------
    p_moderado: float = 0.05
    p_alto: float = 0.01
    p_critico: float = 0.001
    min_healthy_scenes_for_climatology: int = 3
    min_null_size: int = 20                     # tamaño mínimo de la distribución nula empírica
    disturbance_min_scenes: int = 3             # anomalía sostenida = perturbación, no ruido


# Configuración por defecto (inmutable: es seguro compartirla)
DEFAULT_CONFIG = PipelineConfig()


@dataclass(frozen=True)
class ObservationWindowPolicy:
    anchor: Literal["fecha_inicio", "fecha_fin", "fecha_pico"]
    start_offset_days: int
    max_days: int

    def resolve(self, start_date: date, end_date: date, peak_date: date) -> tuple[date, date]:
        base = {"fecha_inicio": start_date, "fecha_fin": end_date, "fecha_pico": peak_date}[self.anchor]
        inicio = base + timedelta(days=self.start_offset_days)
        return inicio, inicio + timedelta(days=self.max_days)


@dataclass(frozen=True)
class TriggerEvent:
    event_id: str
    tipo: str
    fecha_inicio: str
    fecha_fin: str
    fecha_pico: str
    magnitud_maxima: float
    unidad: str
    umbral: float
    window_policy: ObservationWindowPolicy


@dataclass(frozen=True)
class CanonicalGrid:
    polygon_wgs84: Polygon
    polygon_utm: Polygon
    centroid_lon: float
    centroid_lat: float
    crs_wkt: str
    epsg_code: int | None
    transform: rasterio.Affine
    width: int
    height: int
    polygon_interior_mask: np.ndarray
    total_polygon_pixels: int
    pixel_area_ha: float


@dataclass
class SceneObservation:
    scene_id: str
    date: date
    year_month: str
    platform: str
    valid_coverage: float
    arrays: dict[str, np.ndarray]
    st_uncertainty_k: float = np.nan
    mndwi_water_thresh: float = np.nan
    regime: str = "SIN_CLASIFICAR"
    ndvi: float = np.nan
    ndmi: float = np.nan
    mndwi: float = np.nan
    mvi: float = np.nan
    lst_c: float = np.nan
    ndvi_p25: float = np.nan
    mvi_p25: float = np.nan
    lost_fraction: float = np.nan
    ext_lost_fraction: float = np.nan  # pérdida de EXTENSIÓN (persistente 12-18 m); la de arriba es candidata
    ext_provisional: bool = False      # sin 18 meses de seguimiento: extensión con confirmación de 2 pasadas
    condicion: float = np.nan          # z robusto del NDVI del manglar en pie vs. su mes calendario sano
    contaminada: bool = False          # descartada por el filtro multitemporal (nube/bruma omitida por la QA)
    desde_cache: bool = False          # leída de la caché local (sin descarga)
    motivo_descarte: str = ""


# ==============================================================================
# MÓDULO 6a: REGLAS DE SEVERIDAD (probabilidad empírica bajo variabilidad natural de la zona)
# ==============================================================================
# Cada eje se juzga por la probabilidad de observar una anomalía igual o mayor en condiciones
# normales de ESTA zona (distribución nula empírica de pasadas consecutivas, ver módulo 4).
# p <= 5 % MODERADO, p <= 1 % ALTO, p <= 0.1 % CRITICO.

@dataclass(frozen=True)
class AxisSeverityRule:
    axis_key: str          # delta que se reporta (compatibilidad v1)
    metric: str            # métrica cuya anomalía decide
    label: str
    evaluate: Callable[[float, float, PipelineConfig], tuple[str, str]]   # (p, estadístico)


def _categoria(p: float, c: PipelineConfig) -> str:
    if p <= c.p_critico:
        return "CRITICO"
    if p <= c.p_alto:
        return "ALTO"
    if p <= c.p_moderado:
        return "MODERADO"
    return "NORMAL"


def _sev_descenso(textos: dict[str, str]) -> Callable[[float, float, PipelineConfig], tuple[str, str]]:
    def _f(p: float, stat: float, c: PipelineConfig) -> tuple[str, str]:
        cat = _categoria(p, c) if stat < 0 else "NORMAL"
        return cat, textos[cat]
    return _f


def _sev_agua(p: float, stat: float, c: PipelineConfig) -> tuple[str, str]:
    cat = _categoria(p, c)
    if cat == "NORMAL":
        return cat, "Dinámica hídrica superficial sin anegamiento ni relleno anómalo."
    if cat == "MODERADO":
        return cat, "Variación moderada en la lámina de agua superficial del terreno."
    if stat > 0:
        return cat, ("Anegamiento persistente severo sobre terreno previamente emergido." if cat == "CRITICO"
                     else "Inundación superficial prolongada o alteración hídrica significativa.")
    return cat, ("Desecación extrema o pérdida crítica de lámina hídrica del humedal." if cat == "CRITICO"
                 else "Desecación marcada de la lámina de agua superficial del humedal.")


def _sev_microclima(p: float, stat: float, c: PipelineConfig) -> tuple[str, str]:
    cat = _categoria(p, c)
    if stat > 0 and cat != "NORMAL":
        return cat, {"CRITICO": "Pérdida crítica de regulación térmica con sobrecalentamiento superficial.",
                     "ALTO": "Pérdida significativa de regulación térmica del terreno.",
                     "MODERADO": "Ligero calentamiento superficial por encima de la climatología sana."}[cat]
    if stat < 0 and cat != "NORMAL":
        return ("NORMAL", "Enfriamiento superficial asociado a humedad retenida o ventilación; "
                          "regulación térmica activa.")
    return "NORMAL", "Termorregulación microclimática conservada."


SEVERITY_RULES: dict[str, AxisSeverityRule] = {
    "delta_ndvi": AxisSeverityRule("delta_ndvi", "ndvi", "Cobertura y Hábitat", _sev_descenso({
        "CRITICO": "Pérdida severa o defoliación crítica del dosel arbóreo.",
        "ALTO": "Reducción marcada en el vigor y densidad de la cobertura de manglar.",
        "MODERADO": "Estrés foliar moderado respecto al comportamiento estacional sano.",
        "NORMAL": "Densidad y vigor del dosel conservados dentro de la variabilidad natural.",
    })),
    "delta_ndmi": AxisSeverityRule("delta_ndmi", "ndmi", "Suelo", _sev_descenso({
        "CRITICO": "Desecación severa de biomasa y exposición crítica del sustrato.",
        "ALTO": "Déficit hídrico pronunciado en vegetación y suelo superficial.",
        "MODERADO": "Disminución moderada de humedad retenida en el sustrato.",
        "NORMAL": "Contenido de humedad en biomasa y suelo superficial estable.",
    })),
    "delta_mndwi": AxisSeverityRule("delta_mndwi", "mndwi", "Agua", _sev_agua),
    # v2: el microclima se juzga con el acoplamiento LST - T2M (aísla el terreno del clima del día)
    "delta_lst_c": AxisSeverityRule("delta_lst_c", "lst_minus_t2m", "Microclima", _sev_microclima),
}


# ==============================================================================
# UTILIDADES DE RED (reintentos con espera creciente)
# ==============================================================================

def _http_session(config: PipelineConfig) -> requests.Session:
    retry = Retry(total=config.http_retries, backoff_factor=config.http_backoff_s,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST"}), raise_on_status=False)
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = f"mango-pipeline/{PIPELINE_VERSION}"
    return s


def _with_retries(fn: Callable[[], Any], fuente: str, config: PipelineConfig) -> Any:
    ultimo: Exception | None = None
    for intento in range(config.http_retries + 1):
        try:
            return fn()
        except FuenteDatosError:
            raise
        except Exception as e:  # red, JSON truncado, catálogo caído
            ultimo = e
            if intento < config.http_retries:
                time.sleep(config.http_backoff_s * (2 ** intento))
    raise FuenteDatosError(fuente, f"sin respuesta tras {config.http_retries + 1} intentos: {ultimo}")


# ==============================================================================
# MÓDULO 1A: CLIMA (NASA POWER, community=AG) — HISTÓRICO COMPLETO DESDE 1981
# ==============================================================================

POWER_PARAMS = "PRECTOTCORR,WS10M,WS10M_MAX,WD10M,T2M_MAX,ALLSKY_SFC_SW_DWN"


def _download_power(centroid_lon: float, centroid_lat: float, hist_start: str, end_date: str,
                    config: PipelineConfig) -> pd.DataFrame:
    """Descarga cruda (con reintentos). Devuelve el DataFrame diario sin limpiar."""
    params = {
        "parameters": POWER_PARAMS, "community": "AG",
        "longitude": round(centroid_lon, 5), "latitude": round(centroid_lat, 5),
        "start": hist_start.replace("-", ""), "end": end_date.replace("-", ""), "format": "JSON",
    }
    sesion = _http_session(config)

    def _get() -> dict:
        resp = sesion.get(config.nasa_power_url, params=params, timeout=config.http_timeout_s)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    payload = _with_retries(_get, "NASA POWER", config)
    try:
        param_dict = payload["properties"]["parameter"]
    except (KeyError, TypeError) as e:
        raise FuenteDatosError("NASA POWER", "respuesta sin 'properties.parameter'") from e
    raw = pd.DataFrame(param_dict)
    raw.index = pd.to_datetime(raw.index, format="%Y%m%d")
    return raw


POWER_CELDA_LAT, POWER_CELDA_LON = 0.5, 0.625   # rejilla MERRA-2 de NASA POWER


def _download_power_cacheado(centroid_lon: float, centroid_lat: float, hist_start: str, end_date: str,
                             config: PipelineConfig) -> pd.DataFrame:
    """
    Caché del clima por CELDA de NASA POWER (MERRA-2, 1/2° x 5/8°): POWER entrega el valor de la celda
    que contiene el punto, así que todos los recuadros dentro de la misma celda comparten la serie.
    Se reutiliza si la serie guardada cubre [hist_start, end_date]; si no, se descarga y se reemplaza.
    """
    if not config.cache_dir:
        return _download_power(centroid_lon, centroid_lat, hist_start, end_date, config)
    i = math.floor(centroid_lat / POWER_CELDA_LAT)
    j = math.floor(centroid_lon / POWER_CELDA_LON)
    ruta = Path(config.cache_dir) / "power" / f"celda_{i}_{j}.pkl"
    try:
        if ruta.exists():
            guardado = pd.read_pickle(ruta)
            if guardado.index.min() <= pd.Timestamp(hist_start) and guardado.index.max() >= pd.Timestamp(end_date):
                return guardado.loc[hist_start:end_date]
    except Exception:   # noqa: BLE001 — caché dañada: se ignora y se descarga
        pass
    raw = _download_power(centroid_lon, centroid_lat, hist_start, end_date, config)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(f".{os.getpid()}.tmp")
        raw.to_pickle(tmp)
        os.replace(tmp, ruta)
    except OSError:
        pass            # sin caché no pasa nada: solo se vuelve a descargar la próxima vez
    return raw


def fetch_nasa_power_series(
    centroid_lon: float,
    centroid_lat: float,
    start_date: str,
    end_date: str,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """
    Descarga SIEMPRE desde `climatology_start` (1981) hasta `end_date`: los umbrales de
    eventos se calculan con la climatología de la zona (periodo base OMM 1991-2020),
    pero `daily_df` y `monthly_df` solo cubren el rango consultado por el usuario.
    """
    hist_start = min(config.climatology_start, start_date)
    raw = _download_power_cacheado(centroid_lon, centroid_lat, hist_start, end_date, config)
    full = raw.where(raw > -900.0, np.nan)
    full = full.reindex(pd.date_range(start=hist_start, end=end_date, freq="D"))
    full.index.name = "date"

    df = full.loc[start_date:end_date].copy()
    monthly = (
        df.assign(year_month=df.index.strftime("%Y-%m"))
        .groupby("year_month", as_index=False)
        .agg(
            precip_mm_mes=("PRECTOTCORR", lambda s: s.sum(min_count=1)),
            viento_max_ms=("WS10M_MAX", "max"),
            viento_medio_ms=("WS10M", "mean"),
            temp_aire_max_c=("T2M_MAX", "max"),
            radiacion_media_mj=("ALLSKY_SFC_SW_DWN", "mean"),
        )
    )
    return {
        "daily_full_df": full,
        "daily_df": df,
        "monthly_df": monthly,
        "metadata": {
            "source": "NASA POWER Daily API (community=AG)",
            "centroid": [round(centroid_lon, 5), round(centroid_lat, 5)],
            "total_days": int(len(df)),
            "null_cells": int(df.isna().sum().sum()),
            "historico_desde": hist_start,
            "dias_historicos": int(len(full)),
        },
    }


# ==============================================================================
# MÓDULO 1B: REJILLA CANÓNICA UTM Y EXTRACCIÓN LANDSAT C2 L2 (30 M)
# ==============================================================================

def build_canonical_grid(
    bbox: tuple[float, float, float, float],
    pixel_size_m: float = 30.0,
) -> CanonicalGrid:
    west, south, east, north = bbox
    poly_wgs84 = box(west, south, east, north)
    centroid = poly_wgs84.centroid

    utm_zone = max(1, min(60, int((centroid.x + 180.0) // 6.0) + 1))
    target_crs = pyproj.CRS.from_dict({
        "proj": "utm", "zone": utm_zone, "datum": "WGS84", "units": "m", "south": centroid.y < 0.0,
    })
    transformer = pyproj.Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)

    def _reproject_coords(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    poly_utm = shapely.transform(poly_wgs84, _reproject_coords)
    minx, miny, maxx, maxy = poly_utm.bounds
    aligned_minx = math.floor(minx / pixel_size_m) * pixel_size_m
    aligned_maxy = math.ceil(maxy / pixel_size_m) * pixel_size_m
    width = max(1, int(math.ceil((maxx - aligned_minx) / pixel_size_m)))
    height = max(1, int(math.ceil((aligned_maxy - miny) / pixel_size_m)))
    transform = from_origin(aligned_minx, aligned_maxy, pixel_size_m, pixel_size_m)

    interior = geometry_mask([mapping(poly_utm)], out_shape=(height, width),
                             transform=transform, invert=True)
    return CanonicalGrid(
        polygon_wgs84=poly_wgs84, polygon_utm=poly_utm,
        centroid_lon=float(centroid.x), centroid_lat=float(centroid.y),
        crs_wkt=target_crs.to_wkt(), epsg_code=target_crs.to_epsg(),
        transform=transform, width=width, height=height,
        polygon_interior_mask=interior, total_polygon_pixels=int(interior.sum()),
        pixel_area_ha=(pixel_size_m * pixel_size_m) / 10_000.0,
    )


def _read_warped_band(href: str, grid: CanonicalGrid, resampling: Resampling, band: int = 1) -> np.ndarray:
    with rasterio.open(href) as src:
        with WarpedVRT(src, crs=grid.crs_wkt, transform=grid.transform,
                       width=grid.width, height=grid.height, resampling=resampling) as vrt:
            return vrt.read(band)


def otsu_threshold(values: np.ndarray, bins: int = 256) -> tuple[float, float]:
    """Umbral de Otsu (1979) y separabilidad eta = var_entre / var_total (0..1)."""
    v = values[np.isfinite(values)]
    if v.size < 50 or float(np.ptp(v)) < 1e-6:
        return float("nan"), 0.0
    hist, edges = np.histogram(v, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    w = hist.astype(float) / hist.sum()
    w0 = np.cumsum(w)
    w1 = 1.0 - w0
    mu = np.cumsum(w * centers)
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        var_b = (mu_t * w0 - mu) ** 2 / (w0 * w1)
    var_b[~np.isfinite(var_b)] = 0.0
    k = int(np.argmax(var_b))
    var_t = float(np.var(v))
    eta = float(var_b[k] / var_t) if var_t > 0 else 0.0
    return float(centers[k]), eta


def _norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    num, den = a - b, a + b
    ok = np.isfinite(num) & np.isfinite(den) & (np.abs(den) > 1e-6)
    return np.divide(num, den, out=np.full_like(den, np.nan), where=ok)


_BANDAS_CACHE = ("qa", "green", "red", "nir08", "swir16", "lwir")


def _descriptor_rejilla(grid: CanonicalGrid) -> tuple[int, int, int, int]:
    """Esquina superior izquierda (m, múltiplo de 30 en UTM) y tamaño de la rejilla canónica."""
    return (int(round(grid.transform.c)), int(round(grid.transform.f)), grid.width, grid.height)


def _cache_escena_buscar(item_id: str, grid: CanonicalGrid, config: PipelineConfig) -> dict | None:
    """
    Busca la escena ya descargada para una rejilla que CONTENGA la actual. La rejilla canónica está
    alineada a múltiplos absolutos de 30 m en UTM, así que un recuadro dentro de otro ya consultado cae
    exactamente en los mismos píxeles y basta recortar.
    """
    if not config.cache_dir or grid.epsg_code is None:
        return None
    x0, y0, w, h = _descriptor_rejilla(grid)
    paso = int(round(config.pixel_size_m))
    carpeta = Path(config.cache_dir) / "landsat" / str(grid.epsg_code)
    for ruta in carpeta.glob(f"{item_id}__*.npz"):
        try:
            cx0, cy0, cw, ch = (int(v) for v in ruta.stem.split("__", 1)[1].split("_"))
            dx, dy = x0 - cx0, cy0 - y0
            if (dx < 0 or dy < 0 or dx % paso or dy % paso
                    or dx // paso + w > cw or dy // paso + h > ch):
                continue
            c, r = dx // paso, dy // paso
            with np.load(ruta) as z:
                bandas = {k: z[k][r:r + h, c:c + w] for k in _BANDAS_CACHE}
                st_qa = z["st_qa"][r:r + h, c:c + w] if "st_qa" in z.files else None
            return {"bandas": bandas, "st_qa": st_qa}
        except Exception:   # noqa: BLE001 — archivo dañado o de otro formato: se ignora
            continue
    return None


def _cache_escena_guardar(item_id: str, grid: CanonicalGrid, bandas: dict[str, np.ndarray],
                          st_qa: np.ndarray | None, config: PipelineConfig) -> None:
    if not config.cache_dir or grid.epsg_code is None:
        return
    x0, y0, w, h = _descriptor_rejilla(grid)
    carpeta = Path(config.cache_dir) / "landsat" / str(grid.epsg_code)
    ruta = carpeta / f"{item_id}__{x0}_{y0}_{w}_{h}.npz"
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        datos = {k: bandas[k] for k in _BANDAS_CACHE}
        if st_qa is not None:
            datos["st_qa"] = st_qa
        tmp = carpeta / f"{ruta.stem}.{os.getpid()}.{threading.get_ident()}.tmp.npz"
        np.savez_compressed(tmp, **datos)
        os.replace(tmp, ruta)
    except OSError:
        pass            # disco lleno o bloqueado: la escena simplemente no queda en caché


def _leer_bandas(
    item: Any,
    grid: CanonicalGrid,
    config: PipelineConfig,
) -> tuple[Literal["OK", "DISCARDED", "FAILED", "CACHE"], dict[str, np.ndarray] | None, np.ndarray | None]:
    """Bandas crudas (DN) de la escena sobre la rejilla: desde la caché local o desde el COG remoto."""
    en_cache = _cache_escena_buscar(item.id, grid, config)
    if en_cache is not None:
        return "CACHE", en_cache["bandas"], en_cache["st_qa"]
    gdal_env = {
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MAX_RETRY": "3",
        "GDAL_HTTP_RETRY_DELAY": "1.0",
        "GDAL_HTTP_TIMEOUT": "30",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    bands: dict[str, np.ndarray] = {}
    st_qa = None
    for attempt in range(config.max_scene_retries + 1):
        try:
            assets = planetary_computer.sign(item).assets
            thermal_key = "lwir11" if "lwir11" in assets else ("lwir" if "lwir" in assets else None)
            required = ["qa_pixel", "green", "red", "nir08", "swir16"]
            if thermal_key is None or any(k not in assets for k in required):
                return "DISCARDED", None, None
            with rasterio.Env(**gdal_env):
                bands["qa"] = _read_warped_band(assets["qa_pixel"].href, grid, Resampling.nearest)
                # Los DN remuestreados se redondean a uint16 (error < 1 DN = 2.75e-5 de reflectancia): así
                # la escena leída de la caché es idéntica a la descargada.
                for k in ("green", "red", "nir08", "swir16"):
                    bands[k] = np.rint(_read_warped_band(assets[k].href, grid, Resampling.bilinear)
                                       ).clip(0, 65535).astype(np.uint16)
                bands["lwir"] = np.rint(_read_warped_band(assets[thermal_key].href, grid, Resampling.bilinear)
                                        ).clip(0, 65535).astype(np.uint16)
                if "qa" in assets:   # ST_QA: incertidumbre de la temperatura superficial (0.01 K)
                    st_qa = _read_warped_band(assets["qa"].href, grid, Resampling.nearest).astype(np.int16)
            break
        except RasterioIOError:
            if attempt < config.max_scene_retries:
                time.sleep(0.75 * (2 ** attempt))
                continue
            return "FAILED", None, None
    bands["qa"] = bands["qa"].astype(np.uint16)
    _cache_escena_guardar(item.id, grid, bands, st_qa, config)
    return "OK", bands, st_qa


def _process_single_stac_item(
    item: Any,
    grid: CanonicalGrid,
    config: PipelineConfig,
) -> tuple[Literal["OK", "DISCARDED", "FAILED"], SceneObservation | None]:
    estado, crudas, st_qa = _leer_bandas(item, grid, config)
    if crudas is None:
        return ("FAILED" if estado == "FAILED" else "DISCARDED"), None
    obs = _procesar_bandas(item, crudas, st_qa, grid, config)
    if obs is not None:
        obs.desde_cache = estado == "CACHE"
    return ("OK", obs) if obs is not None else ("DISCARDED", None)


def _procesar_bandas(
    item: Any,
    crudas: dict[str, np.ndarray],
    st_qa: np.ndarray | None,
    grid: CanonicalGrid,
    config: PipelineConfig,
) -> SceneObservation | None:
    """Máscara QA + prueba térmica + escalas USGS + índices. None si la escena no cubre el polígono."""
    bands = {k: (v if k == "qa" else v.astype(np.float32)) for k, v in crudas.items()}
    # QA_PIXEL: 0 relleno, 1 nube dilatada, 2 cirro, 3 nube (solo ALTA confianza), 4 sombra
    qa_bad = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
    qa_clear = (bands["qa"] & qa_bad) == 0
    # Bits 8-9: confianza de nube. La nube de confianza MEDIA no activa el bit 3 y pasaría el filtro.
    conf_nube = (bands["qa"].astype(np.uint16) >> 8) & 0b11
    qa_clear &= conf_nube <= config.qa_cloud_conf_max
    sensor_valid = ((bands["green"] > 0) & (bands["red"] > 0) & (bands["nir08"] > 0)
                    & (bands["swir16"] > 0) & (bands["lwir"] > 0))
    # Prueba térmica de nube: una superficie a < -10 °C o > 75 °C no es terreno visible (cima de nube fría
    # o artefacto). Invalida el PÍXEL completo, no solo la temperatura: antes se borraba la LST y su
    # NDVI (≈ 0, nube) seguía contando como manglar perdido. Misma lógica que Fmask (Zhu & Woodcock 2012).
    lst_raw = (bands["lwir"] * 0.00341802 + 149.0) - 273.15
    termico_ok = (lst_raw >= -10.0) & (lst_raw <= 75.0)
    valid = grid.polygon_interior_mask & qa_clear & sensor_valid & termico_ok
    if grid.total_polygon_pixels == 0:
        return None
    coverage = float(valid.sum()) / float(grid.total_polygon_pixels)
    if coverage < config.min_valid_polygon_coverage:
        return None

    def _sr(x: np.ndarray) -> np.ndarray:   # USGS C2 L2: SR = DN*0.0000275 - 0.2
        return np.where(valid, np.clip(x * 0.0000275 - 0.2, 0.0, 1.0), np.nan)

    green, red, nir, swir1 = (_sr(bands[k]) for k in ("green", "red", "nir08", "swir16"))
    lst_c = np.where(valid, lst_raw, np.nan)

    st_unc = float("nan")
    if st_qa is not None:
        u = np.where(valid & (st_qa > 0), st_qa * 0.01, np.nan)
        if np.isfinite(u).any():
            st_unc = float(np.nanmedian(u))

    arrays = scene_indices(green, red, nir, swir1, config)
    arrays["lst_c"] = lst_c.astype(np.float32)
    dt = item.datetime.date()
    return SceneObservation(
        scene_id=item.id, date=dt, year_month=dt.strftime("%Y-%m"),
        platform=str(item.properties.get("platform", "unknown")),
        valid_coverage=round(coverage, 4), arrays=arrays, st_uncertainty_k=st_unc,
    )


def scene_indices(green: np.ndarray, red: np.ndarray, nir: np.ndarray, swir1: np.ndarray,
                  config: PipelineConfig) -> dict[str, np.ndarray]:
    """Índices espectrales. MVI con la forma publicada |NIR-G|/|SWIR1-G| (Baloloy 2020)."""
    ndvi = _norm_diff(nir, red)
    ndmi = _norm_diff(nir, swir1)
    mndwi = _norm_diff(green, swir1)
    num, den = np.abs(nir - green), np.abs(swir1 - green)
    safe = np.isfinite(num) & np.isfinite(den) & (den > config.mvi_singularity_eps)
    mvi = np.divide(num, den, out=np.full_like(den, np.nan), where=safe)
    mvi = np.clip(mvi, 0.0, config.mvi_literature_max)
    return {k: v.astype(np.float32) for k, v in
            {"ndvi": ndvi, "ndmi": ndmi, "mndwi": mndwi, "mvi": mvi}.items()}


def _open_catalog(config: PipelineConfig) -> pystac_client.Client:
    io = StacApiIO(timeout=config.http_timeout_s, max_retries=Retry(
        total=config.http_retries, backoff_factor=config.http_backoff_s,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET", "POST"})))
    return _with_retries(lambda: pystac_client.Client.open(config.stac_api_url, stac_io=io),
                         "Planetary Computer STAC", config)


def fetch_landsat_scenes(
    grid: CanonicalGrid,
    start_date: str,
    end_date: str,
    config: PipelineConfig = DEFAULT_CONFIG,
    progreso: Progreso = _sin_progreso,
) -> dict[str, Any]:
    catalog = _open_catalog(config)

    def _search() -> list:
        return list(catalog.search(
            collections=[config.stac_collection], intersects=mapping(grid.polygon_wgs84),
            datetime=f"{start_date}/{end_date}",
            query={"eo:cloud_cover": {"lt": config.max_stac_cloud_cover}},
        ).items())

    items = _with_retries(_search, "Planetary Computer STAC", config)
    progreso(0.0, f"Encontradas {len(items)} escenas Landsat candidatas")

    valid_scenes: list[SceneObservation] = []
    discarded = failed = done = 0
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = [executor.submit(_process_single_stac_item, it, grid, config) for it in items]
        for fut in as_completed(futures):
            status, obs = fut.result()
            done += 1
            if status == "OK" and obs is not None:
                valid_scenes.append(obs)
            elif status == "FAILED":
                failed += 1
            else:
                discarded += 1
            progreso(done / max(1, len(items)),
                     f"Procesando escenas Landsat {done}/{len(items)} ({len(valid_scenes)} limpias)")

    valid_scenes.sort(key=lambda s: (s.date, -s.valid_coverage))
    deduped: list[SceneObservation] = []
    seen: set[date] = set()
    for s in valid_scenes:
        if s.date not in seen:
            deduped.append(s)
            seen.add(s.date)

    return {
        "scenes": deduped,
        "stats": {
            "stac_candidates": len(items),
            "valid_scenes_retained": len(deduped),
            "discarded_by_cloud_or_slc": discarded,
            "failed_remote_assets": failed,
            "grid_shape": [grid.height, grid.width],
            "epsg": grid.epsg_code,
            "max_stac_cloud_cover": config.max_stac_cloud_cover,
            "plataformas": sorted({s.platform for s in deduped}),
            "escenas_desde_cache": sum(1 for s in deduped if s.desde_cache),
        },
    }


# ==============================================================================
# MÓDULO 1C: EXTENSIÓN DE REFERENCIA — GLOBAL MANGROVE WATCH v4.1
# ==============================================================================

_GMW_NAME = re.compile(r"GMW_([NS])(\d{2})([EW])(\d{3})_.*\.tif{1,2}$", re.IGNORECASE)
GMW_FIRST_YEAR, GMW_LAST_YEAR = 1985, 2025


def _gmw_index(gmw_path: str) -> dict[tuple[int, int], str]:
    """{(lat_sup, lon_izq): ruta_rasterio}. Nombre GMW = esquina superior izquierda del mosaico 1°."""
    p = Path(gmw_path)
    if p.is_dir():
        nombres = [(str(f), f.name) for f in p.rglob("*.tif")]
    elif p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            nombres = [(f"/vsizip/{p.resolve().as_posix()}/{n}", Path(n).name) for n in z.namelist()]
    else:
        return {}
    idx = {}
    for ruta, nombre in nombres:
        m = _GMW_NAME.match(nombre)
        if not m:
            continue
        lat = int(m.group(2)) * (1 if m.group(1).upper() == "N" else -1)
        lon = int(m.group(4)) * (1 if m.group(3).upper() == "E" else -1)
        idx[(lat, lon)] = ruta
    return idx


_GMW_CACHE: dict[str, dict[tuple[int, int], str]] = {}


def load_gmw_extent(grid: CanonicalGrid, reference_year: int,
                    config: PipelineConfig) -> dict[str, Any]:
    """
    Máscara GMW del año de referencia en la rejilla canónica + serie anual (ha) en el polígono.
    Si no hay GMW configurado o no cubre la zona: {"disponible": False, ...}.
    """
    vacio = {"disponible": False, "mascara": None, "serie_anual": [], "mosaicos": 0}
    if not config.gmw_path or not Path(config.gmw_path).exists():
        return vacio
    if config.gmw_path not in _GMW_CACHE:
        _GMW_CACHE[config.gmw_path] = _gmw_index(config.gmw_path)
    idx = _GMW_CACHE[config.gmw_path]
    w, s, e, n = grid.polygon_wgs84.bounds
    rutas = [idx[(lat, lon)]
             for lat in range(math.floor(s) + 1, math.ceil(n) + 1)
             for lon in range(math.floor(w), math.ceil(e))
             if (lat, lon) in idx]
    anios = list(range(GMW_FIRST_YEAR, GMW_LAST_YEAR + 1))
    if not rutas:
        # GMW solo publica mosaicos donde hay manglar: sin mosaico = GMW no registra manglar aquí
        return {"disponible": True, "mascara": np.zeros((grid.height, grid.width), dtype=bool),
                "anio_referencia": min(max(reference_year, GMW_FIRST_YEAR), GMW_LAST_YEAR),
                "serie_anual": [{"anio": a, "ha": 0.0} for a in anios], "mosaicos": 0}

    acumulado = np.zeros((len(anios), grid.height, grid.width), dtype=bool)
    for ruta in rutas:
        with rasterio.open(ruta) as src:
            n_b = min(src.count, len(anios))
            with WarpedVRT(src, crs=grid.crs_wkt, transform=grid.transform, width=grid.width,
                           height=grid.height, resampling=Resampling.nearest) as vrt:
                datos = vrt.read(list(range(1, n_b + 1)))
        acumulado[:n_b] |= (datos == 1)
    acumulado &= grid.polygon_interior_mask[None, :, :]
    ref = min(max(reference_year, GMW_FIRST_YEAR), GMW_LAST_YEAR)
    serie = [{"anio": a, "ha": round(float(acumulado[i].sum()) * grid.pixel_area_ha, 2)}
             for i, a in enumerate(anios)]
    return {"disponible": True, "mascara": acumulado[anios.index(ref)], "anio_referencia": ref,
            "serie_anual": serie, "mosaicos": len(rutas)}


# ==============================================================================
# MÓDULO 2: ADMISIBILIDAD (A/B/C/DATOS INSUFICIENTES) Y QUIEBRE POR EXTENSIÓN
# ==============================================================================

def _robust_sigma(x: np.ndarray, axis: int | None = None) -> np.ndarray | float:
    """1.4826 * MAD: desviación robusta (consistente con la normal), ignora NaN."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(x, axis=axis, keepdims=axis is not None)
        mad = np.nanmedian(np.abs(x - med), axis=axis)
    return 1.4826 * mad


def _stack(scenes: list[SceneObservation], key: str) -> np.ndarray:
    return np.stack([s.arrays[key] for s in scenes], axis=0)


def _nanquantile_stack(stack: np.ndarray, q: float) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanquantile(stack, q, axis=0)


def _masked_quantile(arr: np.ndarray, mask: np.ndarray, q: float = 0.5) -> float:
    vals = arr[mask]
    vals = vals[np.isfinite(vals)]
    return float(np.quantile(vals, q)) if vals.size else float("nan")


def _marcar_escenas_contaminadas(scenes: list[SceneObservation], core: np.ndarray,
                                 config: PipelineConfig) -> int:
    """
    Filtro multitemporal (principio de Tmask/CCDC, Zhu & Woodcock 2014): la máscara QA por escena omite
    nubes delgadas y neblina. Una escena cuyo NDVI CAE respecto a la pasada anterior y REGRESA en la
    siguiente, ambas veces más de k·σ, no es un cambio del terreno que Landsat pueda confirmar: se descarta.

    El cambio entre dos escenas es la mediana de las diferencias PÍXEL A PÍXEL sobre el núcleo visto en
    ambas (no la diferencia de medianas: tras una pérdida parcial la distribución es bimodal y su mediana
    salta sin que el terreno cambie). σ = 1.4826·MAD de esos cambios en toda la serie de la zona.
    Conservador: la primera y la última escena nunca se marcan, y no se juzga si algún vecino está a más
    de `storm_window_max_days`. Devuelve el número de escenas marcadas.
    """
    n_core = int(core.sum())
    if n_core == 0:
        return 0
    utiles = [s for s in scenes if (core & np.isfinite(s.arrays["ndvi"])).sum() >= 0.5 * n_core]
    if len(utiles) < 5:
        return 0

    def _cambio(a: SceneObservation, b: SceneObservation) -> float:
        na, nb = a.arrays["ndvi"], b.arrays["ndvi"]
        comun = core & np.isfinite(na) & np.isfinite(nb)
        if comun.sum() < 0.25 * n_core:
            return float("nan")
        return float(np.median(nb[comun] - na[comun]))

    cambios = np.array([_cambio(utiles[k - 1], utiles[k]) for k in range(1, len(utiles))])
    sig_d = float(_robust_sigma(cambios[np.isfinite(cambios)])) if np.isfinite(cambios).any() else 0.0
    if not sig_d > 1e-9:
        return 0
    limite = config.robust_k * sig_d
    hueco = timedelta(days=config.storm_window_max_days)
    marcadas = 0
    for k in range(1, len(utiles) - 1):
        a, b, c = utiles[k - 1], utiles[k], utiles[k + 1]
        if b.date - a.date > hueco or c.date - b.date > hueco:
            continue
        baja, sube = cambios[k - 1], cambios[k]
        if np.isfinite(baja) and np.isfinite(sube) and baja < -limite and sube > limite:
            b.contaminada, b.motivo_descarte = True, "caida_aislada_ndvi"
            marcadas += 1
    return marcadas


def _ventana_linea_base(scenes: list[SceneObservation], config: PipelineConfig) -> list[SceneObservation]:
    """
    Línea base = la PRIMERA ventana de `initial_reference_months` con al menos `min_scenes_baseline`
    escenas, empezando por la escena más antigua del registro. Con el histórico completo no depende de la
    fecha de inicio que elija el usuario; en los 80-90 el archivo Landsat es escaso y la ventana avanza
    hasta encontrar observaciones suficientes. Lista vacía si ninguna ventana alcanza el mínimo.
    """
    largo = timedelta(days=int(config.initial_reference_months * 30.4375))
    for i, s0 in enumerate(scenes):
        ventana = [s for s in scenes[i:] if s.date <= s0.date + largo]
        if len(ventana) >= config.min_scenes_baseline:
            return ventana
    return []


def _ruido_extension(lost_ref: np.ndarray, config: PipelineConfig) -> float:
    """
    Ruido de extensión de la propia zona: mediana + k·σ robusta (1.4826·MAD) de la fracción "perdida"
    en la línea base ya limpia. Con ~20 escenas de base, un percentil 99 es en la práctica el MÁXIMO y una
    sola escena mala lo fija (caso real Mandinga 2000-2002: umbral de 25 % del núcleo).
    """
    v = lost_ref[np.isfinite(lost_ref)]
    if not v.size:
        return 0.0
    return float(np.median(v) + config.robust_k * float(_robust_sigma(v)))


def _fraccion_perdida_confirmada(scenes: list[SceneObservation], core: np.ndarray,
                                 umbral_px: np.ndarray) -> None:
    """
    Fija `lost_fraction` de cada escena. Confirmación por píxel (principio de CCDC/LandTrendr): un píxel
    candidato (NDVI <= umbral) cuenta como perdido solo si también lo es en SU siguiente observación
    válida; así un parche de nube que la mediana de la escena no delata no suma pérdida. Se recorre hacia
    atrás para conocer esa siguiente observación; la fecha sigue siendo la de la primera. La última
    observación de cada píxel no se puede confirmar y cuenta como candidata (provisional).
    Escenas que ven menos del 50 % del núcleo: NaN (no deciden).
    """
    n_core = int(core.sum())
    siguiente_vista = np.zeros_like(core)
    siguiente_cand = np.zeros_like(core)
    for s in reversed(scenes):
        nd = s.arrays["ndvi"]
        visto = core & np.isfinite(nd)
        cand = visto & (nd <= umbral_px)
        confirmado = cand & np.where(siguiente_vista, siguiente_cand, True)
        siguiente_cand = np.where(visto, cand, siguiente_cand)
        siguiente_vista |= visto
        if n_core == 0 or visto.sum() < 0.5 * n_core:
            s.lost_fraction = float("nan")
            continue
        s.lost_fraction = float(confirmado.sum()) / float(visto.sum())


def _extension_persistente(scenes: list[SceneObservation], core: np.ndarray, umbral_px: np.ndarray,
                           config: PipelineConfig) -> np.ndarray:
    """
    Pérdida de EXTENSIÓN por escena (`ext_lost_fraction`): un píxel candidato en t cuenta como superficie
    perdida solo si sigue siendo candidato en >= 50 % de sus observaciones limpias dentro de
    [t + 12 m, t + 18 m] — la misma ventana de persistencia que define el quiebre estructural
    (Yang et al. 2024), aplicada por píxel. Así el estrés del dosel que se recupera (sequía, inundación,
    defoliación) NO se reporta como hectáreas perdidas: va al índice de condición.
    Si el píxel no tiene observaciones en esa ventana (final del registro o hueco largo), se usa la
    confirmación de 2 pasadas y, al final del registro, la escena se marca `ext_provisional`.
    Devuelve, por escena, la mediana del NDVI del manglar aún en pie (núcleo visto y no perdido), que es
    la base del índice de condición.
    """
    n = len(scenes)
    n_core = int(core.sum())
    en_pie_ndvi = np.full(n, np.nan)
    if n == 0 or n_core == 0:
        return en_pie_ndvi
    vistos = np.zeros((n, *core.shape), dtype=bool)
    cands = np.zeros((n, *core.shape), dtype=bool)
    for i, s in enumerate(scenes):
        nd = s.arrays["ndvi"]
        vistos[i] = core & np.isfinite(nd)
        cands[i] = vistos[i] & (nd <= umbral_px)
    # confirmación de 2 pasadas (respaldo cuando no hay ventana tardía)
    confirmados = np.zeros_like(cands)
    sig_v, sig_c = np.zeros_like(core), np.zeros_like(core)
    for i in range(n - 1, -1, -1):
        confirmados[i] = cands[i] & np.where(sig_v, sig_c, True)
        sig_c = np.where(vistos[i], cands[i], sig_c)
        sig_v |= vistos[i]
    tipo = np.int16 if n < 32000 else np.int32
    acum_v = np.concatenate([np.zeros((1, *core.shape), tipo), np.cumsum(vistos, axis=0, dtype=tipo)])
    acum_c = np.concatenate([np.zeros((1, *core.shape), tipo), np.cumsum(cands, axis=0, dtype=tipo)])
    fechas = np.array([np.datetime64(s.date) for s in scenes])
    persist = np.timedelta64(int(config.loss_persistence_months * 30.4375), "D")
    margen = np.timedelta64(int(6 * 30.4375), "D")
    ultima = fechas[-1]
    for i, s in enumerate(scenes):
        if vistos[i].sum() < 0.5 * n_core:
            s.ext_lost_fraction = float("nan")
            continue
        j0 = int(np.searchsorted(fechas, fechas[i] + persist, side="left"))
        j1 = int(np.searchsorted(fechas, fechas[i] + persist + margen, side="right"))
        v = acum_v[j1] - acum_v[j0]
        c = acum_c[j1] - acum_c[j0]
        persistente = cands[i] & (v > 0) & (2 * c >= v)
        respaldo = confirmados[i] & (v == 0)
        perdido = persistente | respaldo
        s.ext_lost_fraction = float(perdido.sum()) / float(vistos[i].sum())
        s.ext_provisional = bool(fechas[i] + persist + margen > ultima)
        en_pie = vistos[i] & ~perdido
        if en_pie.any():
            en_pie_ndvi[i] = float(np.median(s.arrays["ndvi"][en_pie]))
    return en_pie_ndvi


def _indice_condicion(scenes: list[SceneObservation], en_pie_ndvi: np.ndarray) -> None:
    """
    Condición del dosel (`condicion`): mediana del NDVI del manglar AÚN en pie (sin la superficie perdida
    de forma persistente; la defoliación temporal SÍ cuenta aquí) estandarizada contra la climatología del
    régimen sano por mes calendario (±1 mes): z = (x - mediana_mes) / σ_robusta.
    Negativo = estrés (sequía, inundación, defoliación). Mismo estimador robusto que el resto (1.4826·MAD).
    """
    xs = np.asarray(en_pie_ndvi, dtype=float)
    sanos = np.array([s.regime == "CON_MANGLAR" for s in scenes]) & np.isfinite(xs)
    if sanos.sum() < 6:
        return
    meses = np.array([s.date.month for s in scenes])
    clim = {}
    for m in range(1, 13):
        vecinos = {(m - 2) % 12 + 1, m, m % 12 + 1}
        sel = sanos & np.isin(meses, list(vecinos))
        clim[m] = float(np.median(xs[sel])) if sel.sum() >= 3 else float(np.median(xs[sanos]))
    anom = np.array([xs[i] - clim[meses[i]] for i in range(len(scenes))])
    sig = float(_robust_sigma(anom[sanos]))
    if not sig > 1e-9:
        return
    for i, s in enumerate(scenes):
        if np.isfinite(anom[i]):
            s.condicion = float(anom[i] / sig)


def _months_between(a: date, b: date) -> float:
    return (b - a).days / 30.4375


def _insuficiente(motivo: str, sugerencia: str, **extra: Any) -> dict[str, Any]:
    return {"estado": "DATOS_INSUFICIENTES", "caso": None, "admisible": False,
            "motivo_codigo": "DATOS_INSUFICIENTES", "motivo": motivo, "sugerencia": sugerencia,
            "escenas_clasificadas": [], "timeline_mensual": [], **extra}


def _scene_water_threshold(s: SceneObservation, mask: np.ndarray, config: PipelineConfig) -> float:
    vals = s.arrays["mndwi"][mask]
    thr, eta = otsu_threshold(vals)
    if np.isfinite(thr) and eta >= config.otsu_min_separability:
        v = vals[np.isfinite(vals)]
        frac_agua = float((v > thr).mean()) if v.size else 0.0
        if 0.05 <= frac_agua <= 0.95:      # ambas clases presentes: histograma realmente bimodal
            return thr
    return config.mndwi_fallback_thresh


def _deseasonalized_trend(scenes: list[SceneObservation]) -> dict[str, float | None]:
    """Mann-Kendall y Sen sobre la serie MENSUAL de NDVI sin estacionalidad (pendiente por año)."""
    df = pd.DataFrame({"fecha": [pd.Timestamp(s.date) for s in scenes], "ndvi": [s.ndvi for s in scenes]})
    df = df.dropna()
    if df.empty:
        return {"tau": None, "p": None, "sen_anual": None}
    mensual = df.set_index("fecha")["ndvi"].resample("MS").median().dropna()
    if len(mensual) < 12:
        return {"tau": None, "p": None, "sen_anual": None}
    clim = mensual.groupby(mensual.index.month).transform("median")
    anom = mensual - clim
    t = (mensual.index.year + (mensual.index.dayofyear - 1) / 365.25).to_numpy(dtype=float)
    tau, p = stats.kendalltau(t, anom.to_numpy(), nan_policy="omit")
    sen = stats.theilslopes(anom.to_numpy(), t)
    return {"tau": float(tau), "p": float(p), "sen_anual": float(sen.slope)}


def analyze_zone_and_regimes(
    grid: CanonicalGrid,
    scenes: list[SceneObservation],
    monthly_climate_df: pd.DataFrame,
    config: PipelineConfig = DEFAULT_CONFIG,
    gmw: dict[str, Any] | None = None,
    rango: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """
    `scenes` puede ser el histórico completo de la zona; `rango` = (inicio, fin) que pidió el usuario.
    La línea base, la máscara, el ruido y la calibración usan TODO el registro; el caso (quiebre hasta
    `fin`), la tendencia y la línea de tiempo se reportan para el rango.
    """
    gmw = gmw or {"disponible": False}
    r_ini = date.fromisoformat(rango[0]) if rango else date.min
    r_fin = date.fromisoformat(rango[1]) if rango else date.max
    advertencias: list[str] = []
    poly = grid.polygon_interior_mask
    px_ha = grid.pixel_area_ha

    # --- 1. ¿Hay datos suficientes? (falta de datos NO es "sin manglar") --------
    if len(scenes) < config.min_scenes_total:
        return _insuficiente(
            f"Solo {len(scenes)} escenas Landsat limpias en todo el registro de la zona "
            f"(mínimo {config.min_scenes_total}). No es posible afirmar si hay o no manglar.",
            "La zona es casi siempre nublada para Landsat: prueba un recuadro algo mayor o vecino.")

    ref = _ventana_linea_base(scenes, config)
    if len(ref) < config.min_scenes_baseline:
        return _insuficiente(
            f"Ninguna ventana de {config.initial_reference_months} meses del registro tiene "
            f"{config.min_scenes_baseline} escenas limpias para construir la línea base.",
            "La zona es muy nublada: prueba un recuadro un poco mayor o espera nuevas escenas.")
    if not any(r_ini <= s.date <= r_fin for s in scenes):
        return _insuficiente(
            "No hay ninguna escena Landsat limpia dentro del rango consultado.",
            "Amplía el rango de fechas; la zona sí tiene observaciones en otros años.")

    # --- 2. Agua permanente: umbral de Otsu POR ESCENA (respaldo Xu 2006 = 0) ----
    for s in scenes:
        s.mndwi_water_thresh = _scene_water_threshold(s, poly, config)
    mndwi_ref = _stack(ref, "mndwi")
    thr_ref = np.array([s.mndwi_water_thresh for s in ref])[:, None, None]
    obs = np.isfinite(mndwi_ref).sum(axis=0)
    hits = (mndwi_ref > thr_ref).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        water_freq = np.where(obs > 0, hits / obs, 0.0)
    permanent_water = poly & (water_freq >= config.permanent_water_freq_thresh)
    terrestrial = poly & ~permanent_water

    # --- 3. Línea base y máscara de manglar: GMW (extensión de referencia) + umbrales de la zona
    def _linea_base_y_nucleo(ref_: list[SceneObservation]) -> dict[str, Any]:
        """Línea base por píxel (mediana y σ robusta) y núcleo de manglar (GMW o firma espectral)."""
        base_ndvi = _nanquantile_stack(_stack(ref_, "ndvi"), 0.5)
        base_mvi = _nanquantile_stack(_stack(ref_, "mvi"), 0.5)
        sigma_ndvi_px = _robust_sigma(_stack(ref_, "ndvi"), axis=0)
        avisos: list[str] = []
        spectral = (terrestrial & np.isfinite(base_mvi) & np.isfinite(base_ndvi)
                    & (base_mvi >= config.mvi_literature_min) & (base_ndvi >= config.ndvi_vegetation_min))
        umbrales_zona: dict[str, Any] = {}
        core: np.ndarray
        if gmw.get("disponible") and gmw.get("mascara") is not None:
            cand = gmw["mascara"] & terrestrial
            if cand.sum() * px_ha >= config.min_mangrove_area_ha:
                vals = base_ndvi[cand & np.isfinite(base_ndvi)]
                med = float(np.median(vals)) if vals.size else float("nan")
                sig = float(_robust_sigma(vals)) if vals.size else float("nan")
                piso = med - config.robust_k * sig if np.isfinite(med) and np.isfinite(sig) else -1.0
                core = cand & np.isfinite(base_ndvi) & (base_ndvi >= piso)
                fuente = "GMW"
                umbrales_zona = {"ndvi_mediana_manglar_zona": round(med, 4),
                                 "ndvi_sigma_robusta_manglar_zona": round(sig, 4),
                                 "ndvi_piso_manglar_zona": round(piso, 4),
                                 "mvi_mediana_manglar_zona": round(_masked_quantile(base_mvi, cand), 4)}
            elif spectral.sum() * px_ha >= config.min_mangrove_area_ha:
                core, fuente = spectral, "ESPECTRAL"
                avisos.append("GMW no registra manglar aquí, pero la firma espectral sí (MVI >= "
                              f"{config.mvi_literature_min}). Posible omisión de GMW: verificar en campo.")
            else:
                core, fuente = np.zeros_like(poly), "GMW"
        else:
            core, fuente = spectral, "ESPECTRAL_LITERATURA"
            avisos.append("Global Mangrove Watch no está configurado: la máscara de manglar usa el "
                          "umbral publicado de MVI (Baloloy 2020), menos preciso en sitios con mareas "
                          "o estuarios turbios.")
        return {"base_ndvi": base_ndvi, "base_mvi": base_mvi, "sigma_ndvi_px": sigma_ndvi_px,
                "spectral": spectral, "core": core, "fuente": fuente, "umbrales_zona": umbrales_zona,
                "avisos": avisos}

    lb = _linea_base_y_nucleo(ref)
    observed_frac = float(np.isfinite(lb["base_ndvi"][poly]).mean()) if poly.any() else 0.0
    if observed_frac < 0.5:
        return _insuficiente(
            f"La línea base solo observó el {observed_frac:.0%} del polígono sin nubes.",
            "Amplía el rango de fechas para reunir más observaciones despejadas.")

    # --- 3b. Filtro multitemporal: escenas con nube/bruma que la QA dejó pasar ---------------
    # Se marcan con el núcleo de la primera pasada; si hubo descartes, la línea base y el núcleo se
    # recalculan solo con escenas limpias (el núcleo depende de la línea base y viceversa).
    n_contaminadas = 0
    if float(lb["core"].sum()) * px_ha >= config.min_mangrove_area_ha:
        n_contaminadas = _marcar_escenas_contaminadas(scenes, lb["core"], config)
    if n_contaminadas:
        ref_limpia = [s for s in ref if not s.contaminada]
        if len(ref_limpia) >= config.min_scenes_baseline:
            lb2 = _linea_base_y_nucleo(ref_limpia)
            if float(lb2["core"].sum()) * px_ha >= config.min_mangrove_area_ha:
                lb, ref = lb2, ref_limpia
        descartadas = [s for s in scenes if s.contaminada]
        scenes = [s for s in scenes if not s.contaminada]
    else:
        descartadas = []

    base_ndvi, base_mvi, sigma_ndvi_px = lb["base_ndvi"], lb["base_mvi"], lb["sigma_ndvi_px"]
    spectral, core, fuente, umbrales_zona = lb["spectral"], lb["core"], lb["fuente"], lb["umbrales_zona"]
    advertencias.extend(lb["avisos"])

    core_ha = float(core.sum()) * px_ha
    if core_ha < config.min_mangrove_area_ha:
        return {
            "estado": "SIN_MANGLAR", "caso": "CASO_C", "admisible": False,
            "motivo_codigo": "SIN_MANGLAR",
            "motivo_rechazo": (
                "El polígono no contiene manglar: "
                + ("Global Mangrove Watch no registra manglar en la zona y " if fuente == "GMW" else "")
                + f"la firma espectral (MVI >= {config.mvi_literature_min}, NDVI >= "
                  f"{config.ndvi_vegetation_min}) cubre {float(spectral.sum()) * px_ha:.2f} ha, "
                  f"menos que la unidad mínima de {config.min_mangrove_area_ha} ha."),
            "metricas_admisibilidad": {
                "terrestrial_fraction": round(
                    float(terrestrial.sum()) / max(1, grid.total_polygon_pixels), 4),
                "terrestrial_mvi_p50": _r(_masked_quantile(base_mvi, terrestrial)),
                "terrestrial_ndvi_p50": _r(_masked_quantile(base_ndvi, terrestrial)),
                "manglar_gmw_ha": round(float((gmw.get("mascara") if gmw.get("mascara") is not None
                                               else np.zeros_like(poly)).sum()) * px_ha, 2),
                "firma_espectral_ha": round(float(spectral.sum()) * px_ha, 2),
            },
            "fuente_mascara": fuente, "advertencias": advertencias,
            "escenas_clasificadas": [], "timeline_mensual": [], "n_contaminadas": n_contaminadas,
        }

    # --- 4. Resúmenes por escena sobre el núcleo de manglar (compatibles con v1) --
    for s in scenes:
        s.ndvi = _masked_quantile(s.arrays["ndvi"], core, 0.50)
        s.ndmi = _masked_quantile(s.arrays["ndmi"], core, 0.50)
        s.mndwi = _masked_quantile(s.arrays["mndwi"], core, 0.50)
        s.mvi = _masked_quantile(s.arrays["mvi"], core, 0.50)
        s.lst_c = _masked_quantile(s.arrays["lst_c"], core, 0.50)
        s.ndvi_p25 = _masked_quantile(s.arrays["ndvi"], core, 0.25)
        s.mvi_p25 = _masked_quantile(s.arrays["mvi"], core, 0.25)

    # --- 5. Extensión en pie por escena: pérdida a nivel de PÍXEL --------------
    drop_px = np.maximum(config.pixel_loss_ndvi_drop,
                         np.where(np.isfinite(sigma_ndvi_px), config.robust_k * sigma_ndvi_px, 0.0))
    _fraccion_perdida_confirmada(scenes, core, base_ndvi - drop_px)

    lost_ref = np.array([s.lost_fraction for s in ref if np.isfinite(s.lost_fraction)])
    ruido = _ruido_extension(lost_ref, config)
    mmu_frac = config.min_mangrove_area_ha / core_ha
    tau = float(min(0.9, max(ruido, mmu_frac)))

    # --- 6. Quiebre: PELT (penalización BIC) sobre la serie de EXTENSIÓN PERDIDA ---
    # Solo la extensión: la mediana de NDVI tiene estacionalidad y generaría cortes espurios.
    obs_idx = [i for i, s in enumerate(scenes) if np.isfinite(s.lost_fraction)]
    serie_lost = np.array([scenes[i].lost_fraction for i in obs_idx])
    fechas_obs = [scenes[i].date for i in obs_idx]
    n_obs = len(obs_idx)
    span_m = max(1.0, _months_between(scenes[0].date, scenes[-1].date))
    min_seg = max(3, int(round(n_obs * 6.0 / span_m)))
    caso, fecha_quiebre, break_pos = "CASO_A", None, None
    pen = None
    candidatos_no_confirmados = 0
    persist = timedelta(days=int(config.loss_persistence_months * 30.4375))
    margen = timedelta(days=int(6 * 30.4375))

    def _confirmacion(j: int) -> float | None:
        """Mediana de pérdida en [quiebre+12m, quiebre+18m]: ¿sigue perdido tras una temporada?"""
        f = fechas_obs[j]
        tardio = [serie_lost[k] for k in range(j, n_obs)
                  if f + persist <= fechas_obs[k] <= f + persist + margen]
        return float(np.median(tardio)) if tardio else None

    if n_obs >= 2 * min_seg:
        sig = float(_robust_sigma(np.diff(serie_lost))) / math.sqrt(2.0)
        sig = sig if sig > 1e-9 else float(np.std(serie_lost)) + 1e-9
        senal = ((serie_lost - np.median(serie_lost)) / sig).reshape(-1, 1)
        pen = 2.0 * math.log(n_obs)            # BIC: 2 sigma^2 log n, con sigma = 1 tras estandarizar
        bkps = [b for b in rpt.Pelt(model="l2", min_size=min_seg, jump=1).fit(senal).predict(pen=pen)
                if b < n_obs]
        mejor = 0.0
        for b in bkps:
            # refinamiento: primera escena (cerca del corte) que ya está perdida y así se mantiene
            j0, j1 = max(1, b - min_seg), min(n_obs - 1, b + min_seg)
            cands = [j for j in range(j0, j1 + 1) if serie_lost[j] >= tau
                     and float(np.median(serie_lost[j:j + min_seg])) >= tau]
            if not cands:
                continue
            j = cands[0]
            if fechas_obs[j] > r_fin:          # un quiebre posterior al rango consultado no cuenta
                continue
            pre_med = float(np.median(serie_lost[:j]))
            if pre_med >= tau:
                continue
            conf = _confirmacion(j)
            if conf is None:
                if fechas_obs[-1] < fechas_obs[j] + persist:
                    candidatos_no_confirmados += 1
                continue
            area_ok = conf * core_ha >= config.min_mangrove_area_ha
            if conf >= tau and area_ok and conf - pre_med > mejor:
                mejor, break_pos = conf - pre_med, j

    if candidatos_no_confirmados and break_pos is None:
        advertencias.append("Se observa una posible pérdida reciente que aún no cumple 12 meses de "
                            "persistencia; no se clasifica como quiebre todavía.")

    naturaleza = None
    if break_pos is not None:
        caso = "CASO_B"
        b_idx = obs_idx[break_pos]
        fecha_quiebre = scenes[b_idx].date.isoformat()
        t0 = break_pos - 1
        while t0 > 0 and serie_lost[t0] >= tau:
            t0 -= 1
        trans_ini, trans_fin = obs_idx[max(0, t0)] + 1, b_idx
        trans_ini = min(trans_ini, trans_fin)
        for i, s in enumerate(scenes):
            s.regime = ("CON_MANGLAR" if i < trans_ini else "TRANSICION" if i <= trans_fin
                        else "SIN_MANGLAR")
        naturaleza = _classify_break(scenes, b_idx, core, base_ndvi, drop_px, tau, core_ha, config)
        naturaleza["ultima_observacion_intacta"] = scenes[obs_idx[max(0, t0)]].date.isoformat()
        naturaleza["primera_observacion_con_perdida"] = fecha_quiebre
        if scenes[b_idx].date < r_ini:
            advertencias.append(f"El quiebre estructural ocurrió el {fecha_quiebre}, ANTES del rango consultado: "
                                "la línea de tiempo muestra el régimen posterior a la pérdida.")
    else:
        for s in scenes:
            s.regime = "CON_MANGLAR"

    # --- 7. Tendencias del RANGO consultado (v1 por escena + v2 mensual desestacionalizada) ----
    en_rango = [s for s in scenes if r_ini <= s.date <= r_fin]
    ndvi_arr = np.array([s.ndvi for s in en_rango], dtype=float)
    t_idx = np.arange(len(en_rango), dtype=float)
    if np.isfinite(ndvi_arr).sum() >= 3:
        mk_tau, mk_p = stats.kendalltau(t_idx, ndvi_arr, nan_policy="omit")
    else:
        mk_tau, mk_p = float("nan"), float("nan")
    ok = np.isfinite(ndvi_arr)
    sen_escena = float(stats.theilslopes(ndvi_arr[ok], t_idx[ok]).slope) if ok.sum() >= 2 else float("nan")
    tend = _deseasonalized_trend(en_rango)

    # --- 8. Línea de tiempo mensual: EXTENSIÓN (persistente) y CONDICIÓN del dosel por separado -----
    en_pie_ndvi = _extension_persistente(scenes, core, base_ndvi - drop_px, config)
    _indice_condicion(scenes, en_pie_ndvi)
    scene_df = pd.DataFrame([{
        "year_month": s.year_month, "regimen": s.regime, "ndvi": s.ndvi, "ndmi": s.ndmi,
        "mndwi": s.mndwi, "mvi": s.mvi, "lst_c": s.lst_c, "perdida": s.ext_lost_fraction,
        "condicion": s.condicion, "provisional": s.ext_provisional,
    } for s in scenes])
    monthly_sat = scene_df.groupby("year_month", as_index=False).agg(
        escenas_validas=("ndvi", "count"), regimen=("regimen", "last"),
        ndvi=("ndvi", "median"), ndmi=("ndmi", "median"), mndwi=("mndwi", "median"),
        mvi=("mvi", "median"), lst_c=("lst_c", "median"), perdida=("perdida", "median"),
        condicion=("condicion", "median"), provisional=("provisional", "any"),
        n_ext=("perdida", "count"), perdida_min=("perdida", "min"), perdida_max=("perdida", "max"))
    descartes_mes = Counter(s.year_month for s in descartadas)
    merged = pd.merge(monthly_climate_df, monthly_sat, on="year_month", how="left")
    merged["escenas_validas"] = merged["escenas_validas"].fillna(0).astype(int)

    timeline: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        en_pie = None if not np.isfinite(row.perdida) else max(0.0, 1.0 - float(row.perdida))
        # Confianza del mes (presentación, no umbral científico): el único corte es tau, el ruido de la zona.
        if en_pie is None:
            confianza = None
        elif row.n_ext >= 2:
            confianza = "ALTA" if float(row.perdida_max - row.perdida_min) <= tau else "BAJA"
        else:
            confianza = "MEDIA"
        timeline.append({
            "year_month": row.year_month,
            "clima": {
                "precip_mm_mes": _r(row.precip_mm_mes, 2), "viento_max_ms": _r(row.viento_max_ms, 2),
                "viento_medio_ms": _r(row.viento_medio_ms, 2),
                "temp_aire_max_c": _r(row.temp_aire_max_c, 2),
                "radiacion_media_mj": _r(row.radiacion_media_mj, 2),
            },
            "terrestre": None if row.escenas_validas == 0 else {
                "escenas_validas": int(row.escenas_validas), "regimen": str(row.regimen),
                "ndvi": _r(row.ndvi), "ndmi": _r(row.ndmi), "mndwi": _r(row.mndwi),
                "mvi": _r(row.mvi), "lst_c": _r(row.lst_c, 2),
                "fraccion_manglar_en_pie": _r(en_pie),
                "ha_manglar_estimada": None if en_pie is None else round(en_pie * core_ha, 2),
                "indice_condicion": _r(row.condicion, 2),
                "extension_provisional": bool(row.provisional) if en_pie is not None else False,
                "confianza": confianza,
                "escenas_descartadas": int(descartes_mes.get(row.year_month, 0)),
            },
        })

    return {
        "estado": "ADMISIBLE", "caso": caso, "admisible": True, "fecha_quiebre": fecha_quiebre,
        "naturaleza_quiebre": naturaleza, "fuente_mascara": fuente, "advertencias": advertencias,
        "estadisticos_tendencia": {
            "mann_kendall_tau": _r(mk_tau), "mann_kendall_p": _r(mk_p, 6),
            "sen_slope_por_escena": _r(sen_escena, 6),
            "mann_kendall_tau_mensual_desestacionalizado": _r(tend["tau"]),
            "mann_kendall_p_mensual_desestacionalizado": _r(tend["p"], 6),
            "sen_slope_por_anio": _r(tend["sen_anual"], 6),
        },
        "mascaras_espaciales": {
            "total_polygon_pixels": grid.total_polygon_pixels,
            "permanent_water_pixels": int(permanent_water.sum()),
            "terrestrial_pixels": int(terrestrial.sum()),
            "mangrove_core_pixels": int(core.sum()),
            "mangrove_core_share_of_land": round(float(core.sum()) / float(max(1, terrestrial.sum())), 4),
            "mangrove_core_ha": round(core_ha, 2),
            "fuente_mascara": fuente,
        },
        "umbrales_zona": {
            **umbrales_zona,
            "mndwi_agua_umbral_mediana_escenas": _r(float(np.median([s.mndwi_water_thresh for s in scenes]))),
            "perdida_pixel_ndvi_minima": config.pixel_loss_ndvi_drop,
            "perdida_pixel_sigma_robusta_mediana": _r(_masked_quantile(sigma_ndvi_px, core)),
            "perdida_extension_umbral_fraccion": round(tau, 4),
            "perdida_extension_ruido_linea_base": round(ruido, 4),
            "perdida_extension_ruido_estimador": "mediana + 3·σ robusta (1.4826·MAD) de la línea base limpia",
            "perdida_extension_unidad_minima_fraccion": round(mmu_frac, 4),
            "pelt_penalizacion_bic": _r(pen, 3),
            "pelt_segmento_minimo_escenas": min_seg,
        },
        "core_mask": core, "base_ndvi": base_ndvi, "tau": tau, "core_ha": core_ha,
        "escenas_clasificadas": scenes,
        "n_contaminadas": n_contaminadas,
        "descartadas_por_mes": dict(Counter(s.year_month for s in descartadas)),
        "timeline_mensual": timeline,
    }


def _classify_break(scenes: list[SceneObservation], b_idx: int, core: np.ndarray,
                    base_ndvi: np.ndarray, drop_px: np.ndarray, tau: float, core_ha: float,
                    config: PipelineConfig) -> dict[str, Any]:
    """¿Conversión persistente (relleno, acuacultura, construcción) o perturbación que se recupera?"""
    fecha_b = scenes[b_idx].date
    post = [s for s in scenes[b_idx:]
            if _months_between(fecha_b, s.date) <= config.loss_persistence_months * 2]
    tardias = [s for s in scenes[b_idx:]
               if _months_between(fecha_b, s.date) >= config.recovery_assessment_months - 12]
    seguimiento = _months_between(fecha_b, scenes[-1].date)

    # Píxeles perdidos de forma persistente en el primer año(s) tras el quiebre
    st = np.stack([s.arrays["ndvi"] for s in post], axis=0)
    with np.errstate(invalid="ignore"):
        perdido = (st <= (base_ndvi - drop_px)[None]) & core[None]
    visto = np.isfinite(st) & core[None]
    frac = np.divide(perdido.sum(0), visto.sum(0), out=np.zeros(core.shape), where=visto.sum(0) > 0)
    px_perdidos = core & (frac >= 0.5)
    destino = "INDETERMINADO"
    if px_perdidos.any():
        mndwi_post = _nanquantile_stack(np.stack([s.arrays["mndwi"] for s in post]), 0.5)
        ndvi_post = _nanquantile_stack(st, 0.5)
        thr_agua = float(np.median([s.mndwi_water_thresh for s in post]))
        agua = float(np.nanmedian(mndwi_post[px_perdidos]))
        veg = float(np.nanmedian(ndvi_post[px_perdidos]))
        if agua > thr_agua:
            destino = "AGUA"
        elif veg < config.ndvi_vegetation_min:
            destino = "SUELO_DESNUDO_O_CONSTRUIDO"
        else:
            destino = "OTRA_VEGETACION"

    if seguimiento < config.recovery_assessment_months:
        tipo = "INDETERMINADO_SEGUIMIENTO_CORTO"
    else:
        tard = [s.lost_fraction for s in tardias if np.isfinite(s.lost_fraction)]
        recuperado = bool(tard) and float(np.median(tard)) < tau
        tipo = "PERTURBACION_CON_RECUPERACION" if recuperado else "CONVERSION_PERSISTENTE"

    return {
        "tipo": tipo,
        "destino_cobertura": destino,
        "ha_perdidas_estimadas": round(float(px_perdidos.sum()) * core_ha / max(1, int(core.sum())), 2),
        "meses_de_seguimiento": round(seguimiento, 1),
        "evento_climatico_asociado": None,   # se completa en el orquestador
    }


def _r(x: Any, nd: int = 4) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


# ==============================================================================
# MÓDULO 3: EVENTOS DETONANTES CON CLIMATOLOGÍA LOCAL (1991-2020)
# ==============================================================================

STORM_TYPES = ("LLUVIA_TORRENCIAL_CICLON", "NORTE_SEVERO", "VIENTO_SEVERO")


def _base_period(df: pd.DataFrame, config: PipelineConfig) -> tuple[pd.DataFrame, str]:
    """Periodo normal OMM 1991-2020; si no hay al menos `min_base_years`, todo el histórico."""
    a0, a1 = config.base_period
    base = df.loc[f"{a0}-01-01":f"{a1}-12-31"]
    if base.index.year.nunique() >= config.min_base_years:
        return base, f"{a0}-{a1}"
    return df, f"{df.index.min().year}-{df.index.max().year} (histórico completo)"


def _calendar_percentile(series: pd.Series, base_index: pd.DatetimeIndex, q: float,
                         window_days: int) -> pd.Series:
    """Percentil por día de calendario con ventana móvil centrada (ETCCDI / Perkins 2013)."""
    half = window_days // 2
    base = series.loc[base_index].dropna()
    doy_base = base.index.dayofyear.to_numpy()
    vals = base.to_numpy()
    thr = np.full(367, np.nan)
    for d in range(1, 367):
        dist = np.abs(doy_base - d)
        dist = np.minimum(dist, 366 - dist)
        sel = vals[dist <= half]
        if sel.size >= 30:
            thr[d] = np.percentile(sel, q)
    return pd.Series(thr[series.index.dayofyear.to_numpy()], index=series.index)


def _runs(mask: pd.Series, run_gap_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Runs declustering: excedencias separadas por < run_gap días forman un mismo episodio."""
    activos = mask.index[mask.fillna(False).to_numpy()]
    if len(activos) == 0:
        return []
    spans = [[activos[0], activos[0]]]
    for dt in activos[1:]:
        if (dt - spans[-1][1]).days <= run_gap_days:
            spans[-1][1] = dt
        else:
            spans.append([dt, dt])
    return [(a, b) for a, b in spans]


def _consecutive_runs(mask: pd.Series, min_len: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    m = mask.fillna(False).to_numpy()
    out, i = [], 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out.append((mask.index[i], mask.index[j]))
            i = j + 1
        else:
            i += 1
    return out


def compute_spi(precip: pd.Series, base_index: pd.DatetimeIndex, scale_days: int) -> pd.Series:
    """
    SPI (McKee 1993; WMO-No. 1090) sobre acumulados móviles de `scale_days` días.
    Ajuste gamma por mes de calendario en el periodo base, con probabilidad de cero.
    """
    acc = precip.rolling(scale_days, min_periods=int(scale_days * 0.85)).sum()
    spi = pd.Series(np.nan, index=acc.index)
    base_acc = acc.loc[acc.index.intersection(base_index)].dropna()
    for m in range(1, 13):
        b = base_acc[base_acc.index.month == m].to_numpy()
        if b.size < 60:
            continue
        q0 = float(np.mean(b <= 0))
        pos = b[b > 0]
        if pos.size < 30:
            continue
        a, _, escala = stats.gamma.fit(pos, floc=0)
        sel = (acc.index.month == m) & acc.notna().to_numpy()
        x = acc[sel].to_numpy()
        cdf = q0 + (1.0 - q0) * stats.gamma.cdf(np.maximum(x, 0), a, loc=0, scale=escala)
        cdf = np.clip(cdf, 1e-6, 1 - 1e-6)
        spi[sel] = stats.norm.ppf(cdf)
    return spi


def _event_id(tipo: str, inicio: str, fin: str) -> str:
    return "EVT-" + hashlib.sha1(f"{tipo}|{inicio}|{fin}".encode()).hexdigest()[:8].upper()


def _in_region(lon: float, lat: float, region: tuple[float, float, float, float]) -> bool:
    return region[0] <= lon <= region[2] and region[1] <= lat <= region[3]


def _sector(wd: pd.Series, sector: tuple[float, float]) -> pd.Series:
    a, b = sector
    return (wd >= a) | (wd <= b) if a > b else (wd >= a) & (wd <= b)


def detect_trigger_events(
    daily_df: pd.DataFrame,
    query_start: str | None = None,
    query_end: str | None = None,
    centroid: tuple[float, float] | None = None,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> tuple[list[TriggerEvent], dict[str, Any]]:
    """
    `daily_df` debe ser el histórico COMPLETO (desde 1981). Los umbrales salen del periodo base;
    solo se reportan eventos que inician dentro de [query_start, query_end].
    """
    df = daily_df.copy()
    q0 = pd.Timestamp(query_start) if query_start else df.index.min()
    q1 = pd.Timestamp(query_end) if query_end else df.index.max()
    base, etiqueta_base = _base_period(df, config)
    bidx = base.index
    umbrales: dict[str, Any] = {"periodo_base": etiqueta_base}
    eventos: list[TriggerEvent] = []

    storm = ObservationWindowPolicy("fecha_fin", 1, config.storm_window_max_days)
    radiative = ObservationWindowPolicy("fecha_pico", 0, config.radiative_window_max_days)

    def _en_rango(a: pd.Timestamp) -> bool:
        return q0 <= a <= q1

    # --- Lluvia torrencial: acumulado de 3 días > P99 de días húmedos (R99p) --------
    r3 = df["PRECTOTCORR"].rolling(config.rain_accum_days, min_periods=config.rain_accum_days).sum()
    r3_base = r3.loc[bidx]
    wet = r3_base[r3_base >= config.wet_day_mm].dropna()
    if wet.size >= 100:
        thr_r = float(np.percentile(wet, config.rain_percentile))
        umbrales["lluvia_3d_mm"] = round(thr_r, 2)
        for a, b in _runs(r3 > thr_r, config.decluster_run_days):
            ini = a - timedelta(days=config.rain_accum_days - 1)   # el acumulado se fecha al final
            if not _en_rango(ini):
                continue
            seg = r3.loc[a:b]
            eventos.append(TriggerEvent(
                _event_id("LLUVIA_TORRENCIAL_CICLON", ini.date().isoformat(), b.date().isoformat()),
                "LLUVIA_TORRENCIAL_CICLON", ini.date().isoformat(), b.date().isoformat(),
                seg.idxmax().date().isoformat(), round(float(seg.max()), 2), "mm/72h",
                round(thr_r, 2), storm))

    # --- Viento severo / Norte: racha diaria > P98 local ----------------------------
    ws = df["WS10M_MAX"]
    ws_base = ws.loc[bidx].dropna()
    if ws_base.size >= 365:
        thr_w = float(np.percentile(ws_base, config.wind_percentile))
        umbrales["viento_max_ms"] = round(thr_w, 2)
        golfo = centroid is not None and _in_region(centroid[0], centroid[1], config.norte_region)
        umbrales["region_nortes"] = golfo
        del_norte = _sector(df["WD10M"], config.norte_sector_deg)
        for a, b in _runs(ws > thr_w, config.decluster_run_days):
            if not _en_rango(a):
                continue
            dias = (ws.loc[a:b] > thr_w)
            dias_idx = dias.index[dias.to_numpy()]
            norte = (golfo and a.month in config.norte_months
                     and float(del_norte.loc[dias_idx].mean()) >= 0.5)
            tipo = "NORTE_SEVERO" if norte else "VIENTO_SEVERO"
            seg = ws.loc[a:b]
            eventos.append(TriggerEvent(
                _event_id(tipo, a.date().isoformat(), b.date().isoformat()), tipo,
                a.date().isoformat(), b.date().isoformat(), seg.idxmax().date().isoformat(),
                round(float(seg.max()), 2), "m/s", round(thr_w, 2), storm))

    # --- Sequía: SPI-3 (90 días), evento de McKee que alcanza la categoría severa ----
    spi = compute_spi(df["PRECTOTCORR"], bidx, config.spi_scale_days)
    if spi.notna().sum() > 365:
        umbrales["spi_severo"] = config.spi_severe
        neg = spi < 0
        for a, b in _consecutive_runs(neg, 1):
            seg = spi.loc[a:b]
            if float(seg.min()) > config.spi_severe:
                continue                      # no alcanzó sequía severa
            pico = seg.idxmin()
            if not _en_rango(pico):
                continue
            eventos.append(TriggerEvent(
                _event_id("SEQUIA_ESTACIONAL_SEVERA", a.date().isoformat(), b.date().isoformat()),
                "SEQUIA_ESTACIONAL_SEVERA", a.date().isoformat(), b.date().isoformat(),
                pico.date().isoformat(), round(float(seg.min()), 2), "SPI-3",
                config.spi_severe, radiative))

    # --- Ola de calor con alta radiación: CTX90pct (>=3 días), radiación > P90 calendario
    tx, rad = df["T2M_MAX"], df["ALLSKY_SFC_SW_DWN"]
    thr_tx = _calendar_percentile(tx, bidx, config.heat_percentile, config.heat_window_days)
    thr_rad = _calendar_percentile(rad, bidx, config.radiation_percentile, config.heat_window_days)
    if thr_tx.notna().any():
        umbrales["calor_tx_p90_rango_c"] = [round(float(thr_tx.min()), 2), round(float(thr_tx.max()), 2)]
        caliente = (tx > thr_tx) & (rad > thr_rad)
        for a, b in _consecutive_runs(caliente, config.heat_min_days):
            if not _en_rango(a):
                continue
            seg = tx.loc[a:b]
            exceso = (tx - thr_tx).loc[a:b]
            eventos.append(TriggerEvent(
                _event_id("OLA_CALOR_ALTA_RADIACION", a.date().isoformat(), b.date().isoformat()),
                "OLA_CALOR_ALTA_RADIACION", a.date().isoformat(), b.date().isoformat(),
                exceso.idxmax().date().isoformat(), round(float(seg.max()), 2), "°C",
                round(float(thr_tx.loc[exceso.idxmax()]), 2), radiative))

    eventos.sort(key=lambda e: (e.fecha_inicio, e.tipo))
    return eventos, umbrales


# ==============================================================================
# MÓDULO 4: SECUELAS POST-EVENTO CONTRA LA CLIMATOLOGÍA SANA DE LA ZONA
# ==============================================================================

def wet_months_from_climate(daily_full: pd.DataFrame, config: PipelineConfig) -> set[int]:
    """Meses húmedos de ESTA zona: precipitación media mensual > media de las 12 medias."""
    base, _ = _base_period(daily_full, config)
    mensual = base["PRECTOTCORR"].resample("MS").sum(min_count=20)
    clim = mensual.groupby(mensual.index.month).mean()
    return {int(m) for m, v in clim.items() if v > clim.mean()}


class RegimeClimatology:
    """
    Climatología de un régimen (CON_MANGLAR o SIN_MANGLAR) por mes de calendario (ventana ±1 mes),
    con respaldo en la temporada local (húmeda/seca de ESTA zona) y luego anual.

    Distribución nula empírica (calibración de severidad):
      - residuo de cada escena contra su climatología, dejando FUERA esa escena (sin auto-sesgo);
      - nula de 1 pasada  = residuos individuales;
      - nula de 2 pasadas = "menos extremo" de pares de escenas consecutivas (mismo signo, si no 0),
        que es exactamente el estadístico que se calcula después de un evento.
    """
    METRICS = ("ndvi", "ndmi", "mndwi", "lst_c", "lst_minus_t2m")

    def __init__(self, rows: pd.DataFrame, wet_months: set[int], config: PipelineConfig) -> None:
        self.h = rows.sort_values("date").reset_index(drop=True)
        self.wet = wet_months
        self.c = config
        self.null1: dict[str, np.ndarray] = {}
        self.null2: dict[str, np.ndarray] = {}
        self.sigma: dict[str, float] = {}
        if len(self.h) < config.min_healthy_scenes_for_climatology:
            return
        resid = {m: np.full(len(self.h), np.nan) for m in self.METRICS}
        for i, row in self.h.iterrows():
            b = self.baseline(int(row["month"]), excluir=i)
            for m in self.METRICS:
                if np.isfinite(row[m]) and np.isfinite(b[m]):
                    resid[m][i] = row[m] - b[m]
        self.excluidas: dict[str, int] = {}
        for m in self.METRICS:
            r = resid[m].copy()
            sig0 = float(_robust_sigma(r[np.isfinite(r)])) if np.isfinite(r).sum() >= 3 else float("nan")
            # Perturbaciones sostenidas (>= N escenas seguidas fuera de k sigmas, mismo signo) NO son
            # variabilidad natural: el ruido no persiste entre pasadas; un daño sí. Se excluyen de la nula.
            if np.isfinite(sig0) and sig0 > 0:
                fuera = np.where(np.isfinite(r), np.sign(r) * (np.abs(r) > config.robust_k * sig0), 0)
                excl = np.zeros(len(r), bool)
                i = 0
                while i < len(r):
                    if fuera[i] != 0:
                        j = i
                        while j + 1 < len(r) and fuera[j + 1] == fuera[i]:
                            j += 1
                        if j - i + 1 >= config.disturbance_min_scenes:
                            excl[i:j + 1] = True
                        i = j + 1
                    else:
                        i += 1
                r[excl] = np.nan
                self.excluidas[m] = int(excl.sum())
            self.null1[m] = r[np.isfinite(r)]
            pares = [least_extreme([r[i], r[i + 1]]) for i in range(len(r) - 1)
                     if np.isfinite(r[i]) and np.isfinite(r[i + 1])]
            self.null2[m] = np.array(pares, dtype=float)
            self.sigma[m] = (float(_robust_sigma(self.null1[m]))
                             if self.null1[m].size >= config.min_healthy_scenes_for_climatology
                             else float("nan"))

    @property
    def disponible(self) -> bool:
        return bool(self.null1) and any(v.size >= self.c.min_null_size for v in self.null1.values())

    def _subset(self, month: int, excluir: int | None) -> pd.DataFrame:
        h = self.h.drop(index=excluir) if excluir is not None else self.h
        vecinos = {((month - 2) % 12) + 1, month, (month % 12) + 1}
        sub = h[h["month"].isin(vecinos)]
        if len(sub) >= self.c.min_healthy_scenes_for_climatology:
            return sub
        temporada = self.wet if month in self.wet else set(range(1, 13)) - self.wet
        sub = h[h["month"].isin(temporada)]
        return sub if len(sub) >= self.c.min_healthy_scenes_for_climatology else h

    def baseline(self, month: int, excluir: int | None = None) -> dict[str, float]:
        if self.h.empty:
            return {m: float("nan") for m in self.METRICS}
        sub = self._subset(month, excluir)
        return {m: float(sub[m].median(skipna=True)) for m in self.METRICS}

    def p_value(self, metric: str, stat: float, k: int) -> float | None:
        """Probabilidad (una cola, en la dirección de `stat`) de una anomalía igual o mayor."""
        null = self.null2.get(metric) if k >= 2 else None
        if null is None or null.size < self.c.min_null_size:
            null = self.null1.get(metric)
        if null is None or null.size < self.c.min_null_size or not np.isfinite(stat):
            return None
        if stat == 0:
            return 1.0
        n_ext = int((null >= stat).sum()) if stat > 0 else int((null <= stat).sum())
        p = (n_ext + 1) / (null.size + 1)
        if n_ext == 0:   # más allá de todo lo observado: cola paramétrica con sigma robusta
            sig = float(_robust_sigma(null))
            if sig > 0:
                p = min(p, float(stats.norm.sf(abs(stat) / sig)))
        return float(p)


def least_extreme(valores: list[float]) -> float:
    """Anomalía consistente entre pasadas: la menos extrema si todas tienen el mismo signo; si no, 0."""
    v = [x for x in valores if np.isfinite(x)]
    if not v:
        return float("nan")
    if all(x > 0 for x in v):
        return min(v)
    if all(x < 0 for x in v):
        return max(v)
    return 0.0


def _evaluar_contra(clim: RegimeClimatology, objetivo: pd.DataFrame, st_floor: float,
                    config: PipelineConfig) -> dict[str, Any] | None:
    if not clim.disponible:
        return None
    k = len(objetivo)
    estad, pval, z = {}, {}, {}
    for regla in SEVERITY_RULES.values():
        m = regla.metric
        res = []
        for _, row in objetivo.iterrows():
            b = clim.baseline(int(row["month"]))
            if np.isfinite(row[m]) and np.isfinite(b[m]):
                res.append(row[m] - b[m])
        s = least_extreme(res) if res else float("nan")
        if m == "lst_minus_t2m" and np.isfinite(s) and np.isfinite(st_floor) and abs(s) < st_floor:
            s = 0.0                                     # bajo la incertidumbre del sensor térmico
        estad[m] = s
        pval[m] = clim.p_value(m, s, k) if np.isfinite(s) else None
        sig = clim.sigma.get(m, float("nan"))
        z[m] = round(s / sig, 3) if np.isfinite(s) and np.isfinite(sig) and sig > 0 else None
    return {"estadistico": estad, "p": pval, "z": z}


def evaluate_post_event_impacts(
    events: list[TriggerEvent],
    scenes: list[SceneObservation],
    daily_climate_df: pd.DataFrame,
    fecha_quiebre: str | None,
    config: PipelineConfig = DEFAULT_CONFIG,
    wet_months: set[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not scenes:
        return [], {}
    filas = []
    for s in scenes:
        ts = pd.Timestamp(s.date)
        t2m = float(daily_climate_df.loc[ts, "T2M_MAX"]) if ts in daily_climate_df.index else np.nan
        rad = float(daily_climate_df.loc[ts, "ALLSKY_SFC_SW_DWN"]) if ts in daily_climate_df.index else np.nan
        filas.append({
            "scene_id": s.scene_id, "date": s.date, "month": s.date.month, "regimen": s.regime,
            "ndvi": s.ndvi, "ndmi": s.ndmi, "mndwi": s.mndwi, "lst_c": s.lst_c,
            "t2m_max": t2m, "sw_rad": rad, "st_unc": s.st_uncertainty_k,
            "lst_minus_t2m": s.lst_c - t2m if np.isfinite(s.lst_c) and np.isfinite(t2m) else np.nan,
        })
    sdf = pd.DataFrame(filas).sort_values("date").reset_index(drop=True)
    wet = wet_months or set()
    sano = RegimeClimatology(sdf[sdf["regimen"] == "CON_MANGLAR"], wet, config)
    degradado = RegimeClimatology(sdf[sdf["regimen"] == "SIN_MANGLAR"], wet, config)
    quiebre = date.fromisoformat(fecha_quiebre) if fecha_quiebre else None
    st_floor = float(np.nanmedian(sdf["st_unc"])) if sdf["st_unc"].notna().any() else float("nan")

    ventanas = {ev.event_id: (date.fromisoformat(ev.fecha_inicio), date.fromisoformat(ev.fecha_fin))
                for ev in events}
    out: list[dict[str, Any]] = []
    for ev in events:
        ini, fin = ventanas[ev.event_id]
        w0, w_max = ev.window_policy.resolve(ini, fin, date.fromisoformat(ev.fecha_pico))
        regimen = "CON_MANGLAR" if quiebre is None or fin < quiebre else "SIN_MANGLAR"
        objetivo = sdf[(sdf["date"] >= w0) & (sdf["date"] <= w_max)].head(config.obs_target_scenes)
        w1 = objetivo["date"].max() if not objetivo.empty else w_max
        reg = {
            "event_id": ev.event_id, "tipo": ev.tipo, "fecha_inicio": ev.fecha_inicio,
            "fecha_fin": ev.fecha_fin, "fecha_pico": ev.fecha_pico,
            "magnitud_maxima": ev.magnitud_maxima, "unidad": ev.unidad, "umbral_local": ev.umbral,
            "regimen_evento": regimen, "ventana_busqueda": [w0.isoformat(), w_max.isoformat()],
            "confundido_con": sorted(o for o, (a, b) in ventanas.items()
                                     if o != ev.event_id and a <= w1 and b >= w0),
        }
        if objetivo.empty:
            out.append({**reg, "estado_observacion": "SIN_OBSERVACION_POSTERIOR",
                        "escenas_post_evento": 0, "deltas": None, "anomalias_z": None,
                        "probabilidad_natural": None, "_eval": None, "_eval_regimen": None})
            continue
        mes_ref = int(objetivo["month"].mode().iloc[0])
        b = sano.baseline(mes_ref)
        obs = {m: float(objetivo[m].median(skipna=True)) for m in RegimeClimatology.METRICS}

        def _d(m: str, obs: dict[str, float] = obs, b: dict[str, float] = b) -> float | None:
            return round(obs[m] - b[m], 4) if np.isfinite(obs[m]) and np.isfinite(b[m]) else None

        ev_sano = _evaluar_contra(sano, objetivo, st_floor, config)
        ev_reg = (ev_sano if regimen == "CON_MANGLAR"
                  else _evaluar_contra(degradado, objetivo, st_floor, config))
        rad = float(objetivo["sw_rad"].median(skipna=True))
        out.append({
            **reg, "estado_observacion": "OBSERVADO", "escenas_post_evento": int(len(objetivo)),
            "radiacion_media_sobrevuelo_mj": round(rad, 2) if np.isfinite(rad) else None,
            "deltas": {"delta_ndvi": _d("ndvi"), "delta_ndmi": _d("ndmi"), "delta_mndwi": _d("mndwi"),
                       "delta_lst_c": _d("lst_c"), "delta_acoplamiento_termico_c": _d("lst_minus_t2m")},
            "anomalias_z": None if ev_sano is None else {
                "z_ndvi": ev_sano["z"]["ndvi"], "z_ndmi": ev_sano["z"]["ndmi"],
                "z_mndwi": ev_sano["z"]["mndwi"], "z_acoplamiento_termico": ev_sano["z"]["lst_minus_t2m"]},
            "probabilidad_natural": None if ev_sano is None else {
                k: (None if v is None else round(v, 5)) for k, v in ev_sano["p"].items() if k != "lst_c"},
            "_eval": ev_sano, "_eval_regimen": ev_reg,
        })

    info = {
        "metodo": "probabilidad empírica (pasadas consecutivas, dejar-uno-fuera) bajo el régimen sano",
        "escenas_regimen_sano": int(len(sano.h)), "escenas_regimen_degradado": int(len(degradado.h)),
        "sigma_natural": {k: _r(v) for k, v in sano.sigma.items()},
        "tamano_nula_2_pasadas": {k: int(v.size) for k, v in sano.null2.items()},
        "escenas_excluidas_por_perturbacion": sano.excluidas,
        "incertidumbre_lst_mediana_k": _r(st_floor, 3), "meses_humedos": sorted(wet),
    }
    return out, info


# ==============================================================================
# MÓDULO 5: RECURRENCIA HISTÓRICA (TOP-3)
# ==============================================================================

def group_historical_recurrence(
    evaluated_events: list[dict[str, Any]],
    config: PipelineConfig = DEFAULT_CONFIG,
) -> list[dict[str, Any]]:
    """Separación mínima = largo de la ventana de observación del tipo (si se traslapan, no son
    independientes). Se conserva el de mayor magnitud de cada cúmulo."""
    observed = [e for e in evaluated_events if e["estado_observacion"] == "OBSERVADO"]
    if not observed:
        return []
    sep = {t: config.storm_window_max_days for t in STORM_TYPES}
    por_tipo: dict[str, list[dict[str, Any]]] = {}
    for ev in sorted(observed, key=lambda x: x["fecha_inicio"]):
        bucket = por_tipo.setdefault(ev["tipo"], [])
        min_sep = sep.get(ev["tipo"], config.radiative_window_max_days)
        if not bucket:
            bucket.append(ev)
            continue
        prev_end = date.fromisoformat(bucket[-1]["fecha_fin"])
        if (date.fromisoformat(ev["fecha_inicio"]) - prev_end).days >= min_sep:
            bucket.append(ev)
        elif abs(ev["magnitud_maxima"]) > abs(bucket[-1]["magnitud_maxima"]):
            bucket[-1] = ev
    grupos = []
    for tipo, items in por_tipo.items():
        reg = {i["regimen_evento"] for i in items}
        grupos.append({"tipo_amenaza": tipo, "total_episodios_observados": len(items),
                       "compara_antes_y_despues_quiebre": {"CON_MANGLAR", "SIN_MANGLAR"} <= reg,
                       "ocurrencias": items})
    grupos.sort(key=lambda g: (g["compara_antes_y_despues_quiebre"], g["total_episodios_observados"]),
                reverse=True)
    return grupos[:3]


# ==============================================================================
# MÓDULO 6b: TRADUCCIÓN SEMÁNTICA Y SERIALIZACIÓN RFC 8259
# ==============================================================================

def _ejes(ev: dict[str, Any] | None, config: PipelineConfig) -> tuple[dict[str, Any], list[str]]:
    ejes: dict[str, dict[str, Any]] = {}
    sevs: list[str] = []
    for regla in SEVERITY_RULES.values():
        p = None if ev is None else ev["p"].get(regla.metric)
        s = None if ev is None else ev["estadistico"].get(regla.metric)
        if p is None or s is None or not math.isfinite(float(s)):
            ejes[regla.label] = {
                "severidad": "NO_DISPONIBLE",
                "descripcion": "Dato espectral o térmico no disponible para esta observación."}
            continue
        sev, desc = regla.evaluate(float(p), float(s), config)
        sevs.append(sev)
        ejes[regla.label] = {"severidad": sev, "descripcion": desc}
    return ejes, sevs


def translate_episode_semantics(episode: dict[str, Any],
                                config: PipelineConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    ev_sano = episode.get("_eval")
    ev_reg = episode.get("_eval_regimen")
    limpio = {k: v for k, v in episode.items() if not k.startswith("_")}
    if episode["estado_observacion"] != "OBSERVADO" or ev_sano is None:
        return {**limpio,
                "diagnostico_global": ("Sin escenas ópticas limpias en la ventana posterior debido a "
                                       "nubosidad persistente."),
                "ejes": None, "respuesta_al_evento": None}
    ejes, sevs = _ejes(ev_sano, config)
    if episode["regimen_evento"] == "CON_MANGLAR" and sevs and all(s == "NORMAL" for s in sevs):
        diag = "Sin daños estructurales: el manglar amortiguó el impacto del evento."
    elif "CRITICO" in sevs:
        diag = "Impacto ecológico crítico detectado tras el evento extremo."
    elif "ALTO" in sevs:
        diag = "Alteración biofísica alta en uno o más ejes del ecosistema."
    elif "MODERADO" in sevs:
        diag = "Respuesta moderada con señales tempranas de estrés ambiental."
    else:
        diag = "Variabilidad posterior dentro de rangos esperados."
    respuesta = None
    if ev_reg is not None:
        ejes_r, _ = _ejes(ev_reg, config)
        respuesta = {"referencia": episode["regimen_evento"], "ejes": ejes_r}
    return {**limpio, "diagnostico_global": diag, "ejes": ejes, "respuesta_al_evento": respuesta}


def _sanitize_rfc8259(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _sanitize_rfc8259(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_rfc8259(v) for v in obj]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (date, datetime, pd.Timestamp)):
        return obj.isoformat()
    return obj


# ==============================================================================
# ORQUESTADOR PRINCIPAL
# ==============================================================================

# Reparto de la barra de progreso por etapa (suma 100)
_ETAPAS = {"clima": (0, 6), "gmw": (6, 9), "landsat": (9, 84), "zona": (84, 90),
           "eventos": (90, 96), "secuelas": (96, 99), "listo": (99, 100)}


def _sub(progreso: Progreso, etapa: str) -> Progreso:
    a, b = _ETAPAS[etapa]
    return lambda f, texto: progreso(a + (b - a) * max(0.0, min(1.0, f)), texto)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(_sanitize_rfc8259(payload), ensure_ascii=False, indent=2, allow_nan=False)


def _link_break_to_event(naturaleza: dict[str, Any] | None, eventos: list[TriggerEvent]) -> None:
    """
    Atribución temporal conservadora: un evento solo se asocia a la pérdida si (1) ocurrió ENTRE la
    última observación intacta y la primera con pérdida, y (2) está en el decil más intenso de su
    tipo (relativo a su umbral local). Con ~10 eventos/año, una ventana fija (p. ej. 90 días)
    asociaría casi cualquier quiebre a algún evento por azar.
    """
    if not naturaleza or not naturaleza.get("ultima_observacion_intacta"):
        return
    a = date.fromisoformat(naturaleza["ultima_observacion_intacta"])
    b = date.fromisoformat(naturaleza["primera_observacion_con_perdida"])
    intensidad = {e.event_id: abs(e.magnitud_maxima) / max(abs(e.umbral), 1e-9) for e in eventos}
    p90: dict[str, float] = {}
    for tipo in {e.tipo for e in eventos}:
        vals = [intensidad[e.event_id] for e in eventos if e.tipo == tipo]
        p90[tipo] = float(np.percentile(vals, 90)) if vals else float("inf")
    candidatos = [e for e in eventos
                  if date.fromisoformat(e.fecha_inicio) <= b and date.fromisoformat(e.fecha_fin) >= a
                  and intensidad[e.event_id] >= p90[e.tipo]]
    naturaleza["criterio_atribucion"] = ("evento del decil más intenso de su tipo entre la última "
                                         "observación intacta y la primera con pérdida")
    if candidatos:
        e = max(candidatos, key=lambda x: intensidad[x.event_id])
        naturaleza["evento_climatico_asociado"] = {"event_id": e.event_id, "tipo": e.tipo,
                                                   "fecha_inicio": e.fecha_inicio, "fecha_fin": e.fecha_fin}


def run_mango_pipeline(
    bbox: tuple[float, float, float, float],
    start_date: str = "2005-01-01",
    end_date: str = "2024-12-31",
    config: PipelineConfig = DEFAULT_CONFIG,
    progreso: Progreso | None = None,
) -> str:
    """
    Devuelve un JSON (RFC 8259) con `status`:
      COMPLETED            -> CASO_A / CASO_B
      REJECTED_CONTROLLED  -> CASO_C (se confirmó que NO hay manglar)
      INSUFFICIENT_DATA    -> faltan observaciones para decidir (no es Caso C)
    `progreso(porcentaje, texto)` es opcional; sin él el comportamiento es idéntico.
    """
    progreso = progreso or _sin_progreso
    comun = {"version_pipeline": PIPELINE_VERSION, "referencias": REFERENCIAS}

    grid = build_canonical_grid(bbox, pixel_size_m=config.pixel_size_m)
    _sub(progreso, "clima")(0.0, "Descargando clima histórico de la zona (NASA POWER, desde 1981)")
    climate = fetch_nasa_power_series(grid.centroid_lon, grid.centroid_lat, start_date, end_date, config)
    _sub(progreso, "clima")(1.0, "Clima histórico descargado")

    _sub(progreso, "gmw")(0.0, "Leyendo la extensión de manglar de Global Mangrove Watch")
    gmw = load_gmw_extent(grid, int(start_date[:4]), config)
    _sub(progreso, "gmw")(1.0, "Extensión de referencia lista")

    if config.landsat_historia_completa:
        # Histórico completo de la zona (Landsat 5 → ayer): línea base, ruido y calibración no dependen del
        # rango que elija el usuario; con la caché local solo se descarga una vez por zona.
        ls_ini = min(config.landsat_history_start, start_date)
        ls_fin = max((date.today() - timedelta(days=1)).isoformat(), end_date)
        _sub(progreso, "landsat")(0.0, f"Buscando el histórico Landsat de la zona ({ls_ini[:4]}-{ls_fin[:4]})")
    else:
        ls_ini, ls_fin = start_date, end_date
    landsat = fetch_landsat_scenes(grid, ls_ini, ls_fin, config, _sub(progreso, "landsat"))
    ref_base = _ventana_linea_base(landsat["scenes"], config)
    if ref_base and gmw.get("disponible") and ref_base[0].date.year != int(start_date[:4]):
        # la máscara GMW se toma del año en que empieza la línea base (no del inicio del rango)
        gmw = load_gmw_extent(grid, max(1985, ref_base[0].date.year), config)

    _sub(progreso, "zona")(0.0, "Analizando la zona y buscando pérdidas de manglar")
    zona = analyze_zone_and_regimes(grid, landsat["scenes"], climate["monthly_df"], config, gmw,
                                    rango=(start_date, end_date))
    landsat["stats"]["descartadas_por_contaminacion_temporal"] = int(zona.get("n_contaminadas", 0))
    landsat["stats"]["historia_landsat"] = [ls_ini, ls_fin]
    landsat["stats"]["escenas_en_rango"] = sum(1 for s in landsat["scenes"]
                                               if start_date <= s.date.isoformat() <= end_date)
    gmw_anual = gmw.get("serie_anual", [])

    if zona["estado"] == "DATOS_INSUFICIENTES":
        progreso(100.0, "Listo")
        return _dump({
            **comun, "status": "INSUFFICIENT_DATA", "caso": None,
            "motivo_codigo": "DATOS_INSUFICIENTES", "motivo": zona["motivo"],
            "sugerencia": zona["sugerencia"], "extension_gmw_anual": gmw_anual,
            "ingesta_satelital": landsat["stats"], "ingesta_climatica": climate["metadata"],
        })
    if zona["estado"] == "SIN_MANGLAR":
        progreso(100.0, "Listo")
        return _dump({
            **comun, "status": "REJECTED_CONTROLLED", "caso": "CASO_C",
            "motivo_codigo": "SIN_MANGLAR", "motivo_rechazo": zona["motivo_rechazo"],
            "metricas_admisibilidad": zona["metricas_admisibilidad"],
            "fuente_mascara": zona["fuente_mascara"], "advertencias": zona["advertencias"],
            "extension_gmw_anual": gmw_anual,
            "ingesta_satelital": landsat["stats"], "ingesta_climatica": climate["metadata"],
        })

    _sub(progreso, "eventos")(0.0, "Detectando eventos climáticos extremos con la climatología local")
    eventos, umbrales_eventos = detect_trigger_events(
        climate["daily_full_df"], start_date, end_date, (grid.centroid_lon, grid.centroid_lat), config)
    _link_break_to_event(zona["naturaleza_quiebre"], eventos)

    _sub(progreso, "secuelas")(0.0, "Midiendo la respuesta del territorio después de cada evento")
    wet = wet_months_from_climate(climate["daily_full_df"], config)
    evaluados, info_secuelas = evaluate_post_event_impacts(
        eventos, zona["escenas_clasificadas"], climate["daily_df"], zona["fecha_quiebre"], config, wet)
    top3 = group_historical_recurrence(evaluados, config)
    traducidos = [translate_episode_semantics(e, config) for e in evaluados]
    top3_t = [{**g, "ocurrencias": [translate_episode_semantics(o, config) for o in g["ocurrencias"]]}
              for g in top3]

    payload = {
        **comun,
        "status": "COMPLETED",
        "caso": zona["caso"],
        "fecha_quiebre": zona["fecha_quiebre"],
        "naturaleza_quiebre": zona["naturaleza_quiebre"],
        "fuente_mascara": zona["fuente_mascara"],
        "advertencias": zona["advertencias"],
        "estadisticos_tendencia": zona["estadisticos_tendencia"],
        "mascaras_espaciales": zona["mascaras_espaciales"],
        "extension_gmw_anual": gmw_anual,
        "umbrales_aplicados": {
            "zona": zona["umbrales_zona"], "eventos": umbrales_eventos, "secuelas": info_secuelas,
            "severidad_probabilidad": {"moderado": config.p_moderado, "alto": config.p_alto,
                                       "critico": config.p_critico},
        },
        "ingesta_climatica": climate["metadata"],
        "ingesta_satelital": landsat["stats"],
        "resumen_eventos": {
            "total_detonantes_detectados": len(eventos),
            "total_con_observacion_posterior": sum(1 for e in evaluados
                                                   if e["estado_observacion"] == "OBSERVADO"),
        },
        "comparativa_recurrencia_top3": top3_t,
        "episodios_evaluados": traducidos,
        "timeline_mensual": zona["timeline_mensual"],
    }
    progreso(100.0, "Listo")
    return _dump(payload)


def _cli() -> None:
    p = argparse.ArgumentParser(description="Ejecuta el pipeline MANGO v2 sobre un bbox.")
    p.add_argument("--bbox", type=float, nargs=4, required=True,
                   metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    p.add_argument("--start-date", default="2005-01-01")
    p.add_argument("--end-date", default="2024-12-31")
    p.add_argument("--gmw", default=None, help="zip o carpeta con mosaicos GMW v4.1")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    cfg = PipelineConfig(gmw_path=a.gmw)
    res = run_mango_pipeline(tuple(a.bbox), a.start_date, a.end_date, cfg,
                             lambda pct, t: print(f"[{pct:5.1f}%] {t}", flush=True))
    if a.out:
        Path(a.out).write_text(res, encoding="utf-8")
    else:
        print(res)


if __name__ == "__main__":
    _cli()
