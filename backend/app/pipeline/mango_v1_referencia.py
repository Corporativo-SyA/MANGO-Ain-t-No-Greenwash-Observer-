from __future__ import annotations

import argparse
import json
import math
import time
import uuid
import warnings
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
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from scipy import stats
from shapely.geometry import Polygon, box, mapping

# ==============================================================================
# MÓDULO 0: CONTRATOS DE DATOS, CONFIGURACIÓN Y REGLAS DECLARATIVAS
# ==============================================================================

@dataclass(frozen=True)
class PipelineConfig:
    nasa_power_url: str = "https://power.larc.nasa.gov/api/temporal/daily/point"
    stac_api_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    stac_collection: str = "landsat-c2-l2"
    pixel_size_m: float = 30.0
    max_stac_cloud_cover: float = 25.0
    min_valid_polygon_coverage: float = 0.50
    max_workers: int = 8
    max_scene_retries: int = 2
    # Parámetros biofísicos y de admisibilidad (Guía Definitiva Sec. 1 y 4)
    initial_reference_months: int = 24
    permanent_water_mndwi_thresh: float = 0.20
    permanent_water_freq_thresh: float = 0.80
    min_terrestrial_fraction: float = 0.05
    min_admissible_mvi: float = 0.70
    min_admissible_ndvi: float = 0.30
    core_mangrove_mvi: float = 0.85
    core_mangrove_ndvi: float = 0.30
    mvi_singularity_eps: float = 0.005
    mvi_clip_bounds: tuple[float, float] = (-20.0, 20.0)
    # Parámetros de recurrencia y severidad
    min_episode_separation_days: int = 60
    lst_loss_threshold_c: float = 2.0


@dataclass(frozen=True)
class ObservationWindowPolicy:
    anchor: Literal["fecha_inicio", "fecha_fin"]
    start_offset_days: int
    end_offset_days: int

    def resolve(self, start_date: date, end_date: date) -> tuple[date, date]:
        base = start_date if self.anchor == "fecha_inicio" else end_date
        return (
            base + timedelta(days=self.start_offset_days),
            base + timedelta(days=self.end_offset_days),
        )


@dataclass(frozen=True)
class TriggerEvent:
    event_id: str
    tipo: str
    fecha_inicio: str
    fecha_fin: str
    magnitud_maxima: float
    unidad: str
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


@dataclass
class SceneObservation:
    scene_id: str
    date: date
    year_month: str
    platform: str
    valid_coverage: float
    arrays: dict[str, np.ndarray]
    regime: str = "SIN_CLASIFICAR"
    ndvi: float = np.nan
    ndmi: float = np.nan
    mndwi: float = np.nan
    mvi: float = np.nan
    lst_c: float = np.nan
    ndvi_p25: float = np.nan
    mvi_p25: float = np.nan


@dataclass(frozen=True)
class AxisSeverityRule:
    axis_key: str
    label: str
    evaluate: Callable[[float], tuple[str, str]]


SEVERITY_RULES: dict[str, AxisSeverityRule] = {
    "delta_ndvi": AxisSeverityRule(
        axis_key="delta_ndvi",
        label="Cobertura y Hábitat",
        evaluate=lambda d: (
            ("CRITICO", "Pérdida severa o defoliación crítica del dosel arbóreo.")
            if d <= -0.18 else
            ("ALTO", "Reducción marcada en el vigor y densidad de la cobertura de manglar.")
            if d <= -0.10 else
            ("MODERADO", "Estrés foliar moderado respecto al comportamiento estacional sano.")
            if d <= -0.05 else
            ("NORMAL", "Densidad y vigor del dosel conservados dentro de la variabilidad natural.")
        ),
    ),
    "delta_ndmi": AxisSeverityRule(
        axis_key="delta_ndmi",
        label="Suelo",
        evaluate=lambda d: (
            ("CRITICO", "Desecación severa de biomasa y exposición crítica del sustrato.")
            if d <= -0.16 else
            ("ALTO", "Déficit hídrico pronunciado en vegetación y suelo superficial.")
            if d <= -0.09 else
            ("MODERADO", "Disminución moderada de humedad retenida en el sustrato.")
            if d <= -0.04 else
            ("NORMAL", "Contenido de humedad en biomasa y suelo superficial estable.")
        ),
    ),
    "delta_mndwi": AxisSeverityRule(
        axis_key="delta_mndwi",
        label="Agua",
        evaluate=lambda d: (
            ("CRITICO", "Anegamiento persistente severo sobre terreno previamente emergido.")
            if d >= 0.22 else
            ("ALTO", "Inundación superficial prolongada o alteración hídrica significativa.")
            if d >= 0.12 else
            ("CRITICO", "Desecación extrema o pérdida crítica de lámina hídrica del humedal.")
            if d <= -0.20 else
            ("MODERADO", "Variación moderada en la lámina de agua superficial del terreno.")
            if abs(d) >= 0.06 else
            ("NORMAL", "Dinámica hídrica superficial sin anegamiento ni relleno anómalo.")
        ),
    ),
    "delta_lst_c": AxisSeverityRule(
        axis_key="delta_lst_c",
        label="Microclima",
        evaluate=lambda d: (
            ("CRITICO", "Pérdida crítica de regulación térmica con sobrecalentamiento superficial.")
            if d >= 3.5 else
            ("ALTO", "Pérdida significativa de regulación térmica del terreno.")
            if d >= 2.0 else
            ("MODERADO", "Ligero calentamiento superficial por encima de la climatología sana.")
            if d >= 1.2 else
            ("NORMAL", "Enfriamiento superficial asociado a humedad retenida o ventilación; regulación térmica activa.")
            if d <= -2.0 else
            ("NORMAL", "Termorregulación microclimática conservada.")
        ),
    ),
}


# ==============================================================================
# MÓDULO 1A: INGESTA CLIMÁTICA Y RADIATIVA (NASA POWER — COMMUNITY=AG)
# ==============================================================================

def fetch_nasa_power_series(
    centroid_lon: float,
    centroid_lat: float,
    start_date: str,
    end_date: str,
    config: PipelineConfig = PipelineConfig(),
) -> dict[str, Any]:
    params = {
        "parameters": "PRECTOTCORR,WS10M,WS10M_MAX,WD10M,T2M_MAX,ALLSKY_SFC_SW_DWN",
        "community": "AG",
        "longitude": round(centroid_lon, 5),
        "latitude": round(centroid_lat, 5),
        "start": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "format": "JSON",
    }
    resp = requests.get(config.nasa_power_url, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    param_dict = payload["properties"]["parameter"]
    df = pd.DataFrame(param_dict)
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df.index.name = "date"

    df = df.where(df > -900.0, np.nan)
    full_idx = pd.date_range(start=start_date, end=end_date, freq="D")
    df = df.reindex(full_idx)
    df.index.name = "date"

    monthly = (
        df.assign(year_month=df.index.strftime("%Y-%m"))
        .groupby("year_month", as_index=False)
        .agg(
            precip_mm_mes=("PRECTOTCORR", "sum"),
            viento_max_ms=("WS10M_MAX", "max"),
            viento_medio_ms=("WS10M", "mean"),
            temp_aire_max_c=("T2M_MAX", "max"),
            radiacion_media_mj=("ALLSKY_SFC_SW_DWN", "mean"),
        )
    )

    return {
        "daily_df": df,
        "monthly_df": monthly,
        "metadata": {
            "source": "NASA POWER Daily API (community=AG)",
            "centroid": [round(centroid_lon, 5), round(centroid_lat, 5)],
            "total_days": int(len(df)),
            "null_cells": int(df.isna().sum().sum()),
        },
    }


# ==============================================================================
# MÓDULO 1B: REJILLA CANÓNICA GLOBAL Y EXTRACCIÓN LANDSAT C2 L2 (30 M)
# ==============================================================================

def build_canonical_grid(
    bbox: tuple[float, float, float, float],
    pixel_size_m: float = 30.0,
) -> CanonicalGrid:
    west, south, east, north = bbox
    poly_wgs84 = box(west, south, east, north)
    centroid = poly_wgs84.centroid

    utm_zone = max(1, min(60, int((centroid.x + 180.0) // 6.0) + 1))
    is_south = centroid.y < 0.0
    target_crs = pyproj.CRS.from_dict({
        "proj": "utm",
        "zone": utm_zone,
        "datum": "WGS84",
        "units": "m",
        "south": is_south,
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

    polygon_interior_mask = geometry_mask(
        [mapping(poly_utm)],
        out_shape=(height, width),
        transform=transform,
        invert=True,
    )
    total_poly_pixels = int(polygon_interior_mask.sum())

    return CanonicalGrid(
        polygon_wgs84=poly_wgs84,
        polygon_utm=poly_utm,
        centroid_lon=float(centroid.x),
        centroid_lat=float(centroid.y),
        crs_wkt=target_crs.to_wkt(),
        epsg_code=target_crs.to_epsg(),
        transform=transform,
        width=width,
        height=height,
        polygon_interior_mask=polygon_interior_mask,
        total_polygon_pixels=total_poly_pixels,
    )


def _read_warped_band(
    href: str,
    grid: CanonicalGrid,
    resampling: Resampling,
) -> np.ndarray:
    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=grid.crs_wkt,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=resampling,
        ) as vrt:
            return vrt.read(1)


def _process_single_stac_item(
    item: Any,
    grid: CanonicalGrid,
    config: PipelineConfig,
) -> tuple[Literal["OK", "DISCARDED", "FAILED"], SceneObservation | None]:
    gdal_env = {
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MAX_RETRY": "3",
        "GDAL_HTTP_RETRY_DELAY": "1.0",
        "GDAL_HTTP_TIMEOUT": "30",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }

    qa = green_dn = red_dn = nir_dn = swir1_dn = lwir_dn = None

    for attempt in range(config.max_scene_retries + 1):
        try:
            signed_item = planetary_computer.sign(item)
            assets = signed_item.assets

            thermal_key = "lwir11" if "lwir11" in assets else ("lwir" if "lwir" in assets else None)
            required_keys = ["qa_pixel", "green", "red", "nir08", "swir16"]
            if thermal_key is None or any(k not in assets for k in required_keys):
                return ("DISCARDED", None)

            with rasterio.Env(**gdal_env):
                qa = _read_warped_band(assets["qa_pixel"].href, grid, Resampling.nearest)
                green_dn = _read_warped_band(assets["green"].href, grid, Resampling.bilinear).astype(np.float32)
                red_dn = _read_warped_band(assets["red"].href, grid, Resampling.bilinear).astype(np.float32)
                nir_dn = _read_warped_band(assets["nir08"].href, grid, Resampling.bilinear).astype(np.float32)
                swir1_dn = _read_warped_band(assets["swir16"].href, grid, Resampling.bilinear).astype(np.float32)
                lwir_dn = _read_warped_band(assets[thermal_key].href, grid, Resampling.bilinear).astype(np.float32)
            break
        except RasterioIOError:
            if attempt < config.max_scene_retries:
                time.sleep(0.75 * (attempt + 1))
                continue
            return ("FAILED", None)

    qa_bad_bits = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
    qa_clear = (qa & qa_bad_bits) == 0

    sensor_valid = (
        (green_dn > 0)
        & (red_dn > 0)
        & (nir_dn > 0)
        & (swir1_dn > 0)
        & (lwir_dn > 0)
    )
    valid_mask = grid.polygon_interior_mask & qa_clear & sensor_valid

    if grid.total_polygon_pixels == 0:
        return ("DISCARDED", None)
    valid_coverage = float(valid_mask.sum()) / float(grid.total_polygon_pixels)
    if valid_coverage < config.min_valid_polygon_coverage:
        return ("DISCARDED", None)

    green = np.where(valid_mask, np.clip(green_dn * 0.0000275 - 0.2, 0.0, 1.0), np.nan)
    red = np.where(valid_mask, np.clip(red_dn * 0.0000275 - 0.2, 0.0, 1.0), np.nan)
    nir = np.where(valid_mask, np.clip(nir_dn * 0.0000275 - 0.2, 0.0, 1.0), np.nan)
    swir1 = np.where(valid_mask, np.clip(swir1_dn * 0.0000275 - 0.2, 0.0, 1.0), np.nan)

    lst_c = np.where(valid_mask, (lwir_dn * 0.00341802 + 149.0) - 273.15, np.nan)
    lst_c = np.where((lst_c >= -10.0) & (lst_c <= 75.0), lst_c, np.nan)

    def _norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        num = a - b
        denom = a + b
        valid = np.isfinite(num) & np.isfinite(denom) & (np.abs(denom) > 1e-6)
        return np.divide(num, denom, out=np.full_like(denom, np.nan), where=valid)

    ndvi = _norm_diff(nir, red)
    ndmi = _norm_diff(nir, swir1)
    mndwi = _norm_diff(green, swir1)

    mvi_num = nir - green
    mvi_denom = swir1 - green
    mvi_safe = np.isfinite(mvi_num) & np.isfinite(mvi_denom) & (np.abs(mvi_denom) > config.mvi_singularity_eps)
    mvi_raw = np.divide(mvi_num, mvi_denom, out=np.full_like(mvi_denom, np.nan), where=mvi_safe)
    mvi = np.clip(mvi_raw, config.mvi_clip_bounds[0], config.mvi_clip_bounds[1])

    scene_dt = item.datetime.date()
    platform = str(item.properties.get("platform", "unknown"))

    return (
        "OK",
        SceneObservation(
            scene_id=item.id,
            date=scene_dt,
            year_month=scene_dt.strftime("%Y-%m"),
            platform=platform,
            valid_coverage=round(valid_coverage, 4),
            arrays={
                "ndvi": ndvi.astype(np.float32),
                "ndmi": ndmi.astype(np.float32),
                "mndwi": mndwi.astype(np.float32),
                "mvi": mvi.astype(np.float32),
                "lst_c": lst_c.astype(np.float32),
            },
        ),
    )


def fetch_landsat_scenes(
    grid: CanonicalGrid,
    start_date: str,
    end_date: str,
    config: PipelineConfig = PipelineConfig(),
) -> dict[str, Any]:
    catalog = pystac_client.Client.open(config.stac_api_url)
    search = catalog.search(
        collections=[config.stac_collection],
        intersects=mapping(grid.polygon_wgs84),
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": config.max_stac_cloud_cover}},
    )
    items = list(search.items())

    valid_scenes: list[SceneObservation] = []
    discarded_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = [
            executor.submit(_process_single_stac_item, item, grid, config)
            for item in items
        ]
        for fut in as_completed(futures):
            status, obs = fut.result()
            if status == "OK" and obs is not None:
                valid_scenes.append(obs)
            elif status == "FAILED":
                failed_count += 1
            else:
                discarded_count += 1

    valid_scenes.sort(key=lambda s: (s.date, -s.valid_coverage))
    deduped: list[SceneObservation] = []
    seen_dates: set[date] = set()
    for s in valid_scenes:
        if s.date not in seen_dates:
            deduped.append(s)
            seen_dates.add(s.date)

    return {
        "scenes": deduped,
        "stats": {
            "stac_candidates": len(items),
            "valid_scenes_retained": len(deduped),
            "discarded_by_cloud_or_slc": discarded_count,
            "failed_remote_assets": failed_count,
            "grid_shape": [grid.height, grid.width],
            "epsg": grid.epsg_code,
        },
    }


# ==============================================================================
# MÓDULO 2: VALIDACIÓN DE ZONA (CASO C), AISLAMIENTO Y QUIEBRE (CASO A / B)
# ==============================================================================

def _stack_quantile(scenes: list[SceneObservation], key: str, q: float = 0.5) -> np.ndarray:
    stack = np.stack([s.arrays[key] for s in scenes], axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanquantile(stack, q, axis=0)


def _masked_quantile(arr: np.ndarray, mask: np.ndarray, q: float = 0.5) -> float:
    vals = arr[mask]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return float("nan")
    return float(np.quantile(vals, q))


def analyze_zone_and_regimes(
    grid: CanonicalGrid,
    scenes: list[SceneObservation],
    monthly_climate_df: pd.DataFrame,
    config: PipelineConfig = PipelineConfig(),
) -> dict[str, Any]:
    if len(scenes) < 4:
        return {
            "caso": "CASO_C",
            "admisible": False,
            "motivo_rechazo": "Cobertura satelital limpia insuficiente (< 4 escenas válidas) en el rango consultado.",
            "escenas_clasificadas": [],
            "timeline_mensual": [],
        }

    first_date = scenes[0].date
    ref_cutoff = first_date + timedelta(days=int(config.initial_reference_months * 30.4375))
    ref_scenes = [s for s in scenes if s.date <= ref_cutoff]
    if len(ref_scenes) < 4:
        ref_scenes = scenes[: min(8, len(scenes))]

    mndwi_stack = np.stack([s.arrays["mndwi"] for s in ref_scenes], axis=0)
    valid_water_obs = np.isfinite(mndwi_stack).sum(axis=0)
    water_hits = (mndwi_stack > config.permanent_water_mndwi_thresh).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        water_freq = np.where(valid_water_obs > 0, water_hits / valid_water_obs, 0.0)

    permanent_water_mask = grid.polygon_interior_mask & (
        water_freq > config.permanent_water_freq_thresh
    )
    terrestrial_mask = grid.polygon_interior_mask & (~permanent_water_mask)
    terrestrial_fraction = (
        float(terrestrial_mask.sum()) / float(max(1, grid.total_polygon_pixels))
    )

    base_mvi_map = _stack_quantile(ref_scenes, "mvi", 0.5)
    base_ndvi_map = _stack_quantile(ref_scenes, "ndvi", 0.5)

    terr_mvi_p50 = _masked_quantile(base_mvi_map, terrestrial_mask, 0.50)
    terr_mvi_p75 = _masked_quantile(base_mvi_map, terrestrial_mask, 0.75)
    terr_ndvi_p50 = _masked_quantile(base_ndvi_map, terrestrial_mask, 0.50)

    has_land = terrestrial_fraction >= config.min_terrestrial_fraction
    has_mangrove_sig = (
        np.isfinite(terr_mvi_p50)
        and np.isfinite(terr_ndvi_p50)
        and (
            (terr_mvi_p50 >= config.min_admissible_mvi and terr_ndvi_p50 >= config.min_admissible_ndvi)
            or (terr_mvi_p75 >= config.core_mangrove_mvi and terr_ndvi_p50 >= config.min_admissible_ndvi)
        )
    )

    if not (has_land and has_mangrove_sig):
        return {
            "caso": "CASO_C",
            "admisible": False,
            "motivo_rechazo": (
                "El polígono consultado no presenta firma biofísica significativa de ecosistema de manglar "
                f"(fracción terrestre: {terrestrial_fraction:.1%}, MVI terrestre: {terr_mvi_p50:.2f}, "
                f"NDVI terrestre: {terr_ndvi_p50:.2f})."
            ),
            "metricas_admisibilidad": {
                "terrestrial_fraction": round(terrestrial_fraction, 4),
                "terrestrial_mvi_p50": None if not np.isfinite(terr_mvi_p50) else round(terr_mvi_p50, 4),
                "terrestrial_ndvi_p50": None if not np.isfinite(terr_ndvi_p50) else round(terr_ndvi_p50, 4),
            },
            "escenas_clasificadas": [],
            "timeline_mensual": [],
        }

    mangrove_core_mask = (
        terrestrial_mask
        & np.isfinite(base_mvi_map)
        & np.isfinite(base_ndvi_map)
        & (base_mvi_map >= config.core_mangrove_mvi)
        & (base_ndvi_map >= config.core_mangrove_ndvi)
    )
    if int(mangrove_core_mask.sum()) < 10:
        mangrove_core_mask = terrestrial_mask

    for s in scenes:
        s.ndvi = _masked_quantile(s.arrays["ndvi"], mangrove_core_mask, 0.50)
        s.ndmi = _masked_quantile(s.arrays["ndmi"], mangrove_core_mask, 0.50)
        s.mndwi = _masked_quantile(s.arrays["mndwi"], mangrove_core_mask, 0.50)
        s.mvi = _masked_quantile(s.arrays["mvi"], mangrove_core_mask, 0.50)
        s.lst_c = _masked_quantile(s.arrays["lst_c"], mangrove_core_mask, 0.50)
        s.ndvi_p25 = _masked_quantile(s.arrays["ndvi"], mangrove_core_mask, 0.25)
        s.mvi_p25 = _masked_quantile(s.arrays["mvi"], mangrove_core_mask, 0.25)

    ndvi_p50_arr = np.array([s.ndvi for s in scenes], dtype=float)
    ndvi_p25_arr = np.array([s.ndvi_p25 for s in scenes], dtype=float)
    mvi_p50_arr = np.array([s.mvi for s in scenes], dtype=float)
    t_idx = np.arange(len(scenes), dtype=float)

    mk_tau, mk_p = stats.kendalltau(t_idx, ndvi_p50_arr, nan_policy="omit")
    sen_res = stats.theilslopes(ndvi_p50_arr, t_idx)
    sen_slope = float(sen_res.slope)

    n_obs = len(scenes)
    min_seg = max(4, min(8, n_obs // 5))
    caso = "CASO_A"
    fecha_quiebre: str | None = None
    break_index: int | None = None

    if n_obs >= 2 * min_seg:
        sig = np.column_stack([
            (ndvi_p50_arr - np.nanmean(ndvi_p50_arr)) / (np.nanstd(ndvi_p50_arr) + 1e-6),
            (ndvi_p25_arr - np.nanmean(ndvi_p25_arr)) / (np.nanstd(ndvi_p25_arr) + 1e-6),
            (mvi_p50_arr - np.nanmean(mvi_p50_arr)) / (np.nanstd(mvi_p50_arr) + 1e-6),
        ])
        algo = rpt.Pelt(model="rbf", min_size=min_seg, jump=1).fit(sig)
        bkps = [b for b in algo.predict(pen=4.5) if b < n_obs]

        best_drop_score = 0.0
        for b in bkps:
            pre_ndvi_50 = float(np.nanmedian(ndvi_p50_arr[:b]))
            post_ndvi_50 = float(np.nanmedian(ndvi_p50_arr[b:]))
            pre_ndvi_25 = float(np.nanmedian(ndvi_p25_arr[:b]))
            post_ndvi_25 = float(np.nanmedian(ndvi_p25_arr[b:]))
            pre_mvi_50 = float(np.nanmedian(mvi_p50_arr[:b]))
            post_mvi_50 = float(np.nanmedian(mvi_p50_arr[b:]))

            drop_ndvi_50 = post_ndvi_50 - pre_ndvi_50
            drop_ndvi_25 = post_ndvi_25 - pre_ndvi_25
            drop_mvi_50 = post_mvi_50 - pre_mvi_50

            is_structural_loss = (
                (drop_ndvi_50 <= -0.10 or drop_ndvi_25 <= -0.14 or drop_mvi_50 <= -0.35)
                and (mk_p < 0.05 and sen_slope < 0.0)
            )
            if is_structural_loss:
                score = abs(drop_ndvi_50) + 0.7 * abs(drop_ndvi_25) + 0.25 * abs(drop_mvi_50)
                if score > best_drop_score:
                    best_drop_score = score
                    break_index = b

    if break_index is not None:
        caso = "CASO_B"
        fecha_quiebre = scenes[break_index].date.isoformat()
        trans_start_idx = max(0, break_index - 1)
        trans_end_idx = break_index
        for i, s in enumerate(scenes):
            if i < trans_start_idx:
                s.regime = "CON_MANGLAR"
            elif trans_start_idx <= i <= trans_end_idx:
                s.regime = "TRANSICION"
            else:
                s.regime = "SIN_MANGLAR"
    else:
        for s in scenes:
            s.regime = "CON_MANGLAR"

    scene_df = pd.DataFrame([
        {
            "year_month": s.year_month,
            "regimen": s.regime,
            "ndvi": s.ndvi,
            "ndmi": s.ndmi,
            "mndwi": s.mndwi,
            "mvi": s.mvi,
            "lst_c": s.lst_c,
        }
        for s in scenes
    ])

    monthly_sat = (
        scene_df.groupby("year_month", as_index=False)
        .agg(
            escenas_validas=("ndvi", "count"),
            regimen=("regimen", "last"),
            ndvi=("ndvi", "median"),
            ndmi=("ndmi", "median"),
            mndwi=("mndwi", "median"),
            mvi=("mvi", "median"),
            lst_c=("lst_c", "median"),
        )
    )

    merged_timeline = pd.merge(monthly_climate_df, monthly_sat, on="year_month", how="left")
    merged_timeline["escenas_validas"] = merged_timeline["escenas_validas"].fillna(0).astype(int)

    timeline_records: list[dict[str, Any]] = []
    for row in merged_timeline.itertuples(index=False):
        has_obs = row.escenas_validas > 0
        timeline_records.append({
            "year_month": row.year_month,
            "clima": {
                "precip_mm_mes": round(float(row.precip_mm_mes), 2) if pd.notna(row.precip_mm_mes) else None,
                "viento_max_ms": round(float(row.viento_max_ms), 2) if pd.notna(row.viento_max_ms) else None,
                "viento_medio_ms": round(float(row.viento_medio_ms), 2) if pd.notna(row.viento_medio_ms) else None,
                "temp_aire_max_c": round(float(row.temp_aire_max_c), 2) if pd.notna(row.temp_aire_max_c) else None,
                "radiacion_media_mj": round(float(row.radiacion_media_mj), 2) if pd.notna(row.radiacion_media_mj) else None,
            },
            "terrestre": None if not has_obs else {
                "escenas_validas": int(row.escenas_validas),
                "regimen": str(row.regimen),
                "ndvi": round(float(row.ndvi), 4),
                "ndmi": round(float(row.ndmi), 4),
                "mndwi": round(float(row.mndwi), 4),
                "mvi": round(float(row.mvi), 4),
                "lst_c": round(float(row.lst_c), 2),
            },
        })

    return {
        "caso": caso,
        "admisible": True,
        "fecha_quiebre": fecha_quiebre,
        "estadisticos_tendencia": {
            "mann_kendall_tau": round(float(mk_tau), 4) if np.isfinite(mk_tau) else None,
            "mann_kendall_p": round(float(mk_p), 6) if np.isfinite(mk_p) else None,
            "sen_slope_por_escena": round(sen_slope, 6),
        },
        "mascaras_espaciales": {
            "total_polygon_pixels": grid.total_polygon_pixels,
            "permanent_water_pixels": int(permanent_water_mask.sum()),
            "terrestrial_pixels": int(terrestrial_mask.sum()),
            "mangrove_core_pixels": int(mangrove_core_mask.sum()),
            "mangrove_core_share_of_land": round(
                float(mangrove_core_mask.sum()) / float(max(1, terrestrial_mask.sum())), 4
            ),
        },
        "escenas_clasificadas": scenes,
        "timeline_mensual": timeline_records,
    }


# ==============================================================================
# MÓDULO 3: MOTOR UNITARIO AISLADO DE EVENTOS DETONANTES (NASA POWER)
# ==============================================================================

def _extract_contiguous_episodes(
    mask: pd.Series,
    values: pd.Series,
    min_duration_days: int,
    merge_gap_days: int,
    agg_mode: Literal["max", "min"],
) -> list[tuple[date, date, float]]:
    active_dates = mask.index[mask.fillna(False)].tolist()
    if not active_dates:
        return []

    spans: list[list[pd.Timestamp]] = [[active_dates[0], active_dates[0]]]
    for dt in active_dates[1:]:
        if (dt - spans[-1][1]).days <= merge_gap_days:
            spans[-1][1] = dt
        else:
            spans.append([dt, dt])

    episodes: list[tuple[date, date, float]] = []
    for start_ts, end_ts in spans:
        duration = (end_ts - start_ts).days + 1
        if duration < min_duration_days:
            continue
        sub = values.loc[start_ts:end_ts].dropna()
        if sub.empty:
            continue
        mag = float(sub.max()) if agg_mode == "max" else float(sub.min())
        episodes.append((start_ts.date(), end_ts.date(), mag))
    return episodes


def detect_trigger_events(daily_df: pd.DataFrame) -> list[TriggerEvent]:
    df = daily_df.copy()
    doy = df.index.day_of_year

    rain_3d = df["PRECTOTCORR"].rolling(3, min_periods=3).sum()
    p99_rain_3d = float(np.nanpercentile(rain_3d.dropna(), 99))
    rain_episodes = _extract_contiguous_episodes(
        mask=rain_3d > p99_rain_3d,
        values=rain_3d,
        min_duration_days=1,
        merge_gap_days=7,
        agg_mode="max",
    )

    p98_wind = float(np.nanpercentile(df["WS10M_MAX"].dropna(), 98))
    is_north_sector = (df["WD10M"] >= 315.0) | (df["WD10M"] <= 45.0)
    norte_episodes = _extract_contiguous_episodes(
        mask=(df["WS10M_MAX"] > p98_wind) & is_north_sector,
        values=df["WS10M_MAX"],
        min_duration_days=1,
        merge_gap_days=5,
        agg_mode="max",
    )

    rain_90d = df["PRECTOTCORR"].rolling(90, min_periods=75).sum()
    p10_by_doy = rain_90d.groupby(doy).transform(
        lambda s: np.nanpercentile(s.dropna(), 10) if s.notna().sum() >= 3 else np.nan
    )
    drought_episodes = _extract_contiguous_episodes(
        mask=rain_90d < p10_by_doy,
        values=rain_90d,
        min_duration_days=7,
        merge_gap_days=15,
        agg_mode="min",
    )

    p98_temp_doy = df["T2M_MAX"].groupby(doy).transform(
        lambda s: np.nanpercentile(s.dropna(), 98) if s.notna().sum() >= 3 else np.nan
    )
    p90_rad = float(np.nanpercentile(df["ALLSKY_SFC_SW_DWN"].dropna(), 90))
    heat_episodes = _extract_contiguous_episodes(
        mask=(df["T2M_MAX"] > p98_temp_doy) & (df["ALLSKY_SFC_SW_DWN"] > p90_rad),
        values=df["T2M_MAX"],
        min_duration_days=3,
        merge_gap_days=5,
        agg_mode="max",
    )

    storm_policy = ObservationWindowPolicy(anchor="fecha_fin", start_offset_days=4, end_offset_days=90)
    radiative_policy = ObservationWindowPolicy(anchor="fecha_inicio", start_offset_days=0, end_offset_days=45)

    events: list[TriggerEvent] = []
    for start_d, end_d, mag in rain_episodes:
        events.append(TriggerEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            tipo="LLUVIA_TORRENCIAL_CICLON",
            fecha_inicio=(start_d - timedelta(days=2)).isoformat(),
            fecha_fin=end_d.isoformat(),
            magnitud_maxima=round(mag, 2),
            unidad="mm/72h",
            window_policy=storm_policy,
        ))
    for start_d, end_d, mag in norte_episodes:
        events.append(TriggerEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            tipo="NORTE_SEVERO",
            fecha_inicio=start_d.isoformat(),
            fecha_fin=end_d.isoformat(),
            magnitud_maxima=round(mag, 2),
            unidad="m/s",
            window_policy=storm_policy,
        ))
    for start_d, end_d, mag in drought_episodes:
        events.append(TriggerEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            tipo="SEQUIA_ESTACIONAL_SEVERA",
            fecha_inicio=start_d.isoformat(),
            fecha_fin=end_d.isoformat(),
            magnitud_maxima=round(mag, 2),
            unidad="mm/90d",
            window_policy=radiative_policy,
        ))
    for start_d, end_d, mag in heat_episodes:
        events.append(TriggerEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            tipo="OLA_CALOR_ALTA_RADIACION",
            fecha_inicio=start_d.isoformat(),
            fecha_fin=end_d.isoformat(),
            magnitud_maxima=round(mag, 2),
            unidad="°C",
            window_policy=radiative_policy,
        ))

    events.sort(key=lambda e: (e.fecha_inicio, e.tipo))
    return events


# ==============================================================================
# MÓDULO 4: EVALUADOR DE SECUELAS POST-EVENTO (CLIMATOLOGÍA JERÁRQUICA)
# ==============================================================================

def _month_to_quarter(m: int) -> str:
    if m in (12, 1, 2):
        return "DJF"
    if m in (3, 4, 5):
        return "MAM"
    if m in (6, 7, 8):
        return "JJA"
    return "SON"


def _month_to_semester(m: int) -> str:
    return "WET_MJJASO" if m in (5, 6, 7, 8, 9, 10) else "DRY_NDJFMA"


class HealthyClimatologyResolver:
    def __init__(self, healthy_rows: pd.DataFrame) -> None:
        self.metrics = ["ndvi", "ndmi", "mndwi", "lst_c", "lst_minus_t2m"]
        self.by_quarter: dict[str, dict[str, float]] = {}
        self.by_semester: dict[str, dict[str, float]] = {}
        self.annual: dict[str, float] = {}

        if healthy_rows.empty:
            for col in self.metrics:
                self.annual[col] = float("nan")
            return

        for col in self.metrics:
            self.annual[col] = float(healthy_rows[col].median(skipna=True))

        for q, grp in healthy_rows.groupby("quarter"):
            self.by_quarter[str(q)] = {
                col: float(grp[col].median(skipna=True)) for col in self.metrics
            }

        for sem, grp in healthy_rows.groupby("semester"):
            self.by_semester[str(sem)] = {
                col: float(grp[col].median(skipna=True)) for col in self.metrics
            }

    def get_baseline(self, month: int) -> dict[str, float]:
        q = _month_to_quarter(month)
        sem = _month_to_semester(month)
        result: dict[str, float] = {}
        for col in self.metrics:
            val = self.by_quarter.get(q, {}).get(col, float("nan"))
            if not np.isfinite(val):
                val = self.by_semester.get(sem, {}).get(col, float("nan"))
            if not np.isfinite(val):
                val = self.annual.get(col, float("nan"))
            result[col] = val
        return result


def evaluate_post_event_impacts(
    events: list[TriggerEvent],
    scenes: list[SceneObservation],
    daily_climate_df: pd.DataFrame,
    fecha_quiebre: str | None,
) -> list[dict[str, Any]]:
    if not scenes:
        return []

    records: list[dict[str, Any]] = []
    for s in scenes:
        ts = pd.Timestamp(s.date)
        t2m = float(daily_climate_df.loc[ts, "T2M_MAX"]) if ts in daily_climate_df.index else np.nan
        sw_rad = float(daily_climate_df.loc[ts, "ALLSKY_SFC_SW_DWN"]) if ts in daily_climate_df.index else np.nan
        records.append({
            "scene_id": s.scene_id,
            "date": s.date,
            "month": s.date.month,
            "quarter": _month_to_quarter(s.date.month),
            "semester": _month_to_semester(s.date.month),
            "regimen": s.regime,
            "ndvi": s.ndvi,
            "ndmi": s.ndmi,
            "mndwi": s.mndwi,
            "lst_c": s.lst_c,
            "t2m_max": t2m,
            "sw_rad": sw_rad,
            "lst_minus_t2m": s.lst_c - t2m if np.isfinite(s.lst_c) and np.isfinite(t2m) else np.nan,
        })

    scene_df = pd.DataFrame(records)
    healthy_df = scene_df[scene_df["regimen"] == "CON_MANGLAR"]
    climatology = HealthyClimatologyResolver(healthy_df)
    quiebre_dt = date.fromisoformat(fecha_quiebre) if fecha_quiebre else None

    evaluated: list[dict[str, Any]] = []
    for ev in events:
        start_d = date.fromisoformat(ev.fecha_inicio)
        end_d = date.fromisoformat(ev.fecha_fin)
        win_start, win_end = ev.window_policy.resolve(start_d, end_d)

        if quiebre_dt is None or end_d < quiebre_dt:
            event_regime = "CON_MANGLAR"
        else:
            event_regime = "SIN_MANGLAR"

        in_win = scene_df[(scene_df["date"] >= win_start) & (scene_df["date"] <= win_end)]
        if in_win.empty:
            evaluated.append({
                "event_id": ev.event_id,
                "tipo": ev.tipo,
                "fecha_inicio": ev.fecha_inicio,
                "fecha_fin": ev.fecha_fin,
                "magnitud_maxima": ev.magnitud_maxima,
                "unidad": ev.unidad,
                "regimen_evento": event_regime,
                "estado_observacion": "SIN_OBSERVACION_POSTERIOR",
                "ventana_busqueda": [win_start.isoformat(), win_end.isoformat()],
                "escenas_post_evento": 0,
                "deltas": None,
            })
            continue

        early_end = min(win_end, win_start + timedelta(days=45))
        early_win = in_win[in_win["date"] <= early_end]
        target_win = early_win if not early_win.empty else in_win

        ref_month = int(target_win["month"].mode().iloc[0])
        base = climatology.get_baseline(ref_month)

        obs_ndvi = float(target_win["ndvi"].median(skipna=True))
        obs_ndmi = float(target_win["ndmi"].median(skipna=True))
        obs_mndwi = float(target_win["mndwi"].median(skipna=True))
        obs_lst = float(target_win["lst_c"].median(skipna=True))
        obs_coupling = float(target_win["lst_minus_t2m"].median(skipna=True))
        obs_rad = float(target_win["sw_rad"].median(skipna=True))

        def _delta(obs: float, ref: float) -> float | None:
            if not (np.isfinite(obs) and np.isfinite(ref)):
                return None
            return round(obs - ref, 4)

        evaluated.append({
            "event_id": ev.event_id,
            "tipo": ev.tipo,
            "fecha_inicio": ev.fecha_inicio,
            "fecha_fin": ev.fecha_fin,
            "magnitud_maxima": ev.magnitud_maxima,
            "unidad": ev.unidad,
            "regimen_evento": event_regime,
            "estado_observacion": "OBSERVADO",
            "ventana_busqueda": [win_start.isoformat(), win_end.isoformat()],
            "escenas_post_evento": int(len(target_win)),
            "radiacion_media_sobrevuelo_mj": round(obs_rad, 2) if np.isfinite(obs_rad) else None,
            "deltas": {
                "delta_ndvi": _delta(obs_ndvi, base["ndvi"]),
                "delta_ndmi": _delta(obs_ndmi, base["ndmi"]),
                "delta_mndwi": _delta(obs_mndwi, base["mndwi"]),
                "delta_lst_c": _delta(obs_lst, base["lst_c"]),
                "delta_acoplamiento_termico_c": _delta(obs_coupling, base["lst_minus_t2m"]),
            },
        })

    return evaluated


# ==============================================================================
# MÓDULO 5: AGRUPADOR DE RECURRENCIA HISTÓRICA (TOP-3 COMPARATIVO)
# ==============================================================================

def group_historical_recurrence(
    evaluated_events: list[dict[str, Any]],
    config: PipelineConfig = PipelineConfig(),
) -> list[dict[str, Any]]:
    observed = [e for e in evaluated_events if e["estado_observacion"] == "OBSERVADO"]
    if not observed:
        return []

    deduped_by_type: dict[str, list[dict[str, Any]]] = {}
    for ev in sorted(observed, key=lambda x: x["fecha_inicio"]):
        bucket = deduped_by_type.setdefault(ev["tipo"], [])
        if not bucket:
            bucket.append(ev)
        else:
            prev_end = date.fromisoformat(bucket[-1]["fecha_fin"])
            curr_start = date.fromisoformat(ev["fecha_inicio"])
            if (curr_start - prev_end).days >= config.min_episode_separation_days:
                bucket.append(ev)
            elif ev["magnitud_maxima"] > bucket[-1]["magnitud_maxima"]:
                bucket[-1] = ev

    groups: list[dict[str, Any]] = []
    for tipo, items in deduped_by_type.items():
        regimes_present = {i["regimen_evento"] for i in items}
        has_both_regimes = ("CON_MANGLAR" in regimes_present) and ("SIN_MANGLAR" in regimes_present)
        groups.append({
            "tipo_amenaza": tipo,
            "total_episodios_observados": len(items),
            "compara_antes_y_despues_quiebre": has_both_regimes,
            "ocurrencias": items,
        })

    groups.sort(
        key=lambda g: (g["compara_antes_y_despues_quiebre"], g["total_episodios_observados"]),
        reverse=True,
    )
    return groups[:3]


# ==============================================================================
# MÓDULO 6: TRADUCCIÓN SEMÁNTICA DECLARATIVA Y SERIALIZACIÓN RFC 8259
# ==============================================================================

def translate_episode_semantics(episode: dict[str, Any]) -> dict[str, Any]:
    if episode["estado_observacion"] != "OBSERVADO" or not episode["deltas"]:
        return {
            **episode,
            "diagnostico_global": "Sin escenas ópticas limpias en los 90 días posteriores debido a nubosidad persistente.",
            "ejes": None,
        }

    deltas = episode["deltas"]
    ejes_out: dict[str, dict[str, Any]] = {}
    severities: list[str] = []

    for axis_key, rule in SEVERITY_RULES.items():
        val = deltas.get(axis_key)
        if val is None or not math.isfinite(float(val)):
            ejes_out[rule.label] = {
                "severidad": "NO_DISPONIBLE",
                "descripcion": "Dato espectral o térmico no disponible para esta observación.",
            }
            continue
        sev, desc = rule.evaluate(float(val))
        severities.append(sev)
        ejes_out[rule.label] = {
            "severidad": sev,
            "descripcion": desc,
        }

    if episode["regimen_evento"] == "CON_MANGLAR" and severities and all(s == "NORMAL" for s in severities):
        diagnostico_global = "Sin daños estructurales: el manglar amortiguó el impacto del evento."
    elif "CRITICO" in severities:
        diagnostico_global = "Impacto ecológico crítico detectado tras el evento extremo."
    elif "ALTO" in severities:
        diagnostico_global = "Alteración biofísica alta en uno o más ejes del ecosistema."
    elif "MODERADO" in severities:
        diagnostico_global = "Respuesta moderada con señales tempranas de estrés ambiental."
    else:
        diagnostico_global = "Variabilidad posterior dentro de rangos esperados."

    return {
        **episode,
        "diagnostico_global": diagnostico_global,
        "ejes": ejes_out,
    }


def _sanitize_rfc8259(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _sanitize_rfc8259(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_rfc8259(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        val = float(obj)
        return val if math.isfinite(val) else None
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (date, datetime, pd.Timestamp)):
        return obj.isoformat()
    return obj


# ==============================================================================
# ORQUESTADOR PRINCIPAL (PARIDAD 1:1 JUPYTER <-> BACKEND .PY)
# ==============================================================================

def run_mango_pipeline(
    bbox: tuple[float, float, float, float],
    start_date: str = "2005-01-01",
    end_date: str = "2024-12-31",
    config: PipelineConfig = PipelineConfig(),
) -> str:
    grid = build_canonical_grid(bbox, pixel_size_m=config.pixel_size_m)

    climate_res = fetch_nasa_power_series(
        centroid_lon=grid.centroid_lon,
        centroid_lat=grid.centroid_lat,
        start_date=start_date,
        end_date=end_date,
        config=config,
    )
    landsat_res = fetch_landsat_scenes(
        grid=grid,
        start_date=start_date,
        end_date=end_date,
        config=config,
    )

    zone_res = analyze_zone_and_regimes(
        grid=grid,
        scenes=landsat_res["scenes"],
        monthly_climate_df=climate_res["monthly_df"],
        config=config,
    )

    if not zone_res["admisible"]:
        rejected_payload = {
            "status": "REJECTED_CONTROLLED",
            "caso": zone_res["caso"],
            "motivo_rechazo": zone_res["motivo_rechazo"],
            "metricas_admisibilidad": zone_res.get("metricas_admisibilidad"),
            "ingesta_satelital": landsat_res["stats"],
            "ingesta_climatica": climate_res["metadata"],
        }
        return json.dumps(_sanitize_rfc8259(rejected_payload), ensure_ascii=False, indent=2, allow_nan=False)

    trigger_events = detect_trigger_events(climate_res["daily_df"])

    evaluated_events = evaluate_post_event_impacts(
        events=trigger_events,
        scenes=zone_res["escenas_clasificadas"],
        daily_climate_df=climate_res["daily_df"],
        fecha_quiebre=zone_res["fecha_quiebre"],
    )

    top3_groups = group_historical_recurrence(evaluated_events, config=config)

    translated_events = [translate_episode_semantics(e) for e in evaluated_events]
    translated_top3 = [
        {
            **grp,
            "ocurrencias": [translate_episode_semantics(occ) for occ in grp["ocurrencias"]],
        }
        for grp in top3_groups
    ]

    final_payload = {
        "status": "COMPLETED",
        "caso": zone_res["caso"],
        "fecha_quiebre": zone_res["fecha_quiebre"],
        "estadisticos_tendencia": zone_res["estadisticos_tendencia"],
        "mascaras_espaciales": zone_res["mascaras_espaciales"],
        "ingesta_climatica": climate_res["metadata"],
        "ingesta_satelital": landsat_res["stats"],
        "resumen_eventos": {
            "total_detonantes_detectados": len(trigger_events),
            "total_con_observacion_posterior": sum(
                1 for e in evaluated_events if e["estado_observacion"] == "OBSERVADO"
            ),
        },
        "comparativa_recurrencia_top3": translated_top3,
        "episodios_evaluados": translated_events,
        "timeline_mensual": zone_res["timeline_mensual"],
    }

    return json.dumps(_sanitize_rfc8259(final_payload), ensure_ascii=False, indent=2, allow_nan=False)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Ejecuta el pipeline analítico MANGO sobre un Bounding Box.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Coordenadas EPSG:4326 en orden: oeste sur este norte.",
    )
    parser.add_argument("--start-date", default="2005-01-01", help="Fecha inicial YYYY-MM-DD.")
    parser.add_argument("--end-date", default="2024-12-31", help="Fecha final YYYY-MM-DD.")
    parser.add_argument("--out", default=None, help="Ruta opcional para guardar el JSON resultante.")
    args = parser.parse_args()

    bbox_tuple = (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])
    result_json = run_mango_pipeline(
        bbox=bbox_tuple,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if args.out:
        Path(args.out).write_text(result_json, encoding="utf-8")
    else:
        print(result_json)


if __name__ == "__main__":
    _cli()
