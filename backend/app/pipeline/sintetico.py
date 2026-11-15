"""
Escenarios SINTÉTICOS para probar el pipeline MANGO de punta a punta sin internet.

Se sustituyen solo las tres descargas (NASA POWER, Landsat, GMW); TODO el análisis
científico (módulos 2-6) corre con el código real. Los datos imitan reflectancias
típicas por cobertura (manglar, agua, pasto/cultivo, suelo desnudo) con estacionalidad,
ruido, nubes y bandeado. NO representan ningún sitio real.

Escenarios:
  A          manglar estable                                   -> CASO_A
  B          45 % del manglar se rellena (2012-04)            -> CASO_B, CONVERSION_PERSISTENTE
  B_parcial  18 % del manglar se rellena (2012-04)            -> CASO_B (la v1 no lo detecta)
  huracan    50 % defoliado por un ciclón (2012-09) y rebrote -> CASO_A + evento con daño
  cultivo    sin manglar, cultivo verde                        -> CASO_C (la v1 lo aceptaba)
  pocos      casi siempre nublado                              -> INSUFFICIENT_DATA
  sur        manglar estable en el hemisferio sur             -> CASO_A
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from unittest import mock

import numpy as np
import pandas as pd

from app.pipeline import mango

ESCENARIOS = ("A", "B", "B_parcial", "huracan", "cultivo", "pocos", "sur", "neblina", "sequia")
BBOX = {"norte": (-96.090, 19.050, -96.084, 19.056),   # ~0.6 x 0.6 km
        "sur": (-47.925, -25.030, -47.919, -25.024)}
MUNDO = ("2005-01-01", "2024-12-31")                 # registro satelital del planeta sintético
FECHA_QUIEBRE = date(2012, 4, 10)
FECHA_HURACAN = date(2012, 9, 15)
SEQUIA = (date(2011, 2, 1), date(2011, 5, 31))       # sequía de 4 meses; recuperación lineal en 6
NEBLINA_FRIA = (40, 80, 120, 160, 200, 240)          # n.º de escena con nube fría que la QA no marcó
BRUMA_TIBIA = (60, 140, 220)                         # n.º de escena con bruma tibia (NDVI ~0.25)
NUBE = (0.350, 0.360, 0.380, 0.300)                  # reflectancia de nube: NDVI ~0.03, MNDWI ~+0.08

# Reflectancias de superficie típicas (verde, rojo, NIR, SWIR1)
REFLECTANCIA = {
    "manglar": (0.045, 0.030, 0.300, 0.090),     # NDVI ~0.82, MVI ~5.7
    "agua": (0.060, 0.040, 0.020, 0.010),        # MNDWI ~0.71
    "cultivo": (0.080, 0.060, 0.350, 0.250),     # NDVI ~0.71, MVI ~1.6
    "desnudo": (0.100, 0.120, 0.180, 0.250),     # NDVI ~0.20, MNDWI ~-0.43
    "defoliado": (0.060, 0.070, 0.160, 0.140),   # NDVI ~0.39
}
LST_BASE = {"manglar": 27.0, "agua": 25.0, "cultivo": 30.0, "desnudo": 34.0, "defoliado": 31.0}


# ------------------------------------------------------------------------------
# Clima diario sintético (1981 -> fin)
# ------------------------------------------------------------------------------
def clima_sintetico(fin: str, hemisferio_sur: bool = False, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("1981-01-01", fin, freq="D")
    doy = idx.dayofyear.to_numpy()
    fase = (doy - 172) if not hemisferio_sur else (doy - 355)
    estacion = np.cos(2 * np.pi * fase / 365.25)                 # +1 en temporada húmeda
    p_lluvia = 0.25 + 0.35 * (estacion + 1) / 2
    lluvia = np.where(rng.random(len(idx)) < p_lluvia,
                      rng.gamma(0.8, 6.0 + 10.0 * (estacion + 1) / 2), 0.0)
    invierno = np.isin(idx.month, (10, 11, 12, 1, 2, 3)) if not hemisferio_sur else np.zeros(len(idx), bool)
    ws_max = rng.gamma(6.0, 1.2, len(idx)) + np.where(invierno, 1.5, 0.0)
    wd = rng.uniform(0, 360, len(idx))
    t2m = 30.0 + 3.0 * estacion + rng.normal(0, 1.2, len(idx))
    rad = 20.0 + 4.0 * estacion + rng.normal(0, 2.0, len(idx))
    df = pd.DataFrame({"PRECTOTCORR": lluvia, "WS10M": ws_max * 0.45, "WS10M_MAX": ws_max,
                       "WD10M": wd, "T2M_MAX": t2m, "ALLSKY_SFC_SW_DWN": rad}, index=idx)
    # Eventos inyectados en el periodo de estudio
    for dia, mm in ((date(2008, 10, 1), 140.0), (date(2012, 9, 14), 220.0), (date(2016, 9, 20), 160.0),
                    (date(2019, 10, 5), 150.0)):
        for k, frac in enumerate((0.3, 0.5, 0.2)):
            ts = pd.Timestamp(dia) + pd.Timedelta(days=k)
            if ts in df.index:
                df.loc[ts, "PRECTOTCORR"] = mm * frac
    for dia in (date(2010, 1, 12), date(2014, 12, 3), date(2018, 2, 8), date(2021, 11, 20)):
        ts = pd.Timestamp(dia)
        if ts in df.index:
            df.loc[ts:ts + pd.Timedelta(days=1), "WS10M_MAX"] = 21.0
            df.loc[ts:ts + pd.Timedelta(days=1), "WD10M"] = 350.0
    return df


# ------------------------------------------------------------------------------
# Escenas Landsat sintéticas
# ------------------------------------------------------------------------------
def _coberturas(grid: mango.CanonicalGrid, escenario: str, seed: int) -> dict[str, np.ndarray]:
    h, w = grid.height, grid.width
    filas = np.arange(h)[:, None] * np.ones((1, w))
    agua = filas < int(0.15 * h)
    tierra = ~agua
    rng = np.random.default_rng(seed)
    if escenario == "cultivo":
        manglar = np.zeros((h, w), bool)
    else:
        manglar = tierra & (filas < int(0.80 * h))
    otro = tierra & ~manglar
    idx_m = np.flatnonzero(manglar.ravel())
    frac = {"B": 0.45, "B_parcial": 0.18, "huracan": 0.50}.get(escenario, 0.0)
    afectado = np.zeros(h * w, bool)
    if frac:
        # bloque contiguo (relleno/tala real) para B; aleatorio para huracán
        n = int(frac * idx_m.size)
        afectado[idx_m[-n:] if escenario != "huracan" else rng.choice(idx_m, n, replace=False)] = True
    return {"agua": agua, "manglar": manglar, "otro": otro, "afectado": afectado.reshape(h, w)}


def escenas_sinteticas(grid: mango.CanonicalGrid, inicio: str, fin: str, escenario: str,
                       seed: int = 11, hemisferio_sur: bool = False) -> list[mango.SceneObservation]:
    rng = np.random.default_rng(seed)
    cov = _coberturas(grid, escenario, seed)
    cfg = mango.DEFAULT_CONFIG
    escenas = []
    d = date.fromisoformat(inicio)
    d_fin = date.fromisoformat(fin)
    p_nube = 0.98 if escenario == "pocos" else 0.30   # 0.98: ni con todo el registro hay datos suficientes
    n_escena = 0
    while d <= d_fin:
        if rng.random() >= p_nube:
            n_escena += 1
            doy = d.timetuple().tm_yday
            fase = (doy - 172) if not hemisferio_sur else (doy - 355)
            est = np.cos(2 * np.pi * fase / 365.25)
            clase = np.full((grid.height, grid.width), "otro", dtype=object)
            clase[cov["agua"]] = "agua"
            clase[cov["manglar"]] = "manglar"
            clase[cov["otro"]] = "cultivo"
            if escenario in ("B", "B_parcial") and d >= FECHA_QUIEBRE:
                clase[cov["afectado"]] = "desnudo"
            if escenario == "huracan" and FECHA_HURACAN <= d < FECHA_HURACAN + timedelta(days=365):
                meses = (d - FECHA_HURACAN).days / 30.4
                if meses < 6 or rng.random() < (12 - meses) / 6:
                    clase[cov["afectado"]] = "defoliado"
            # sequía: el NIR del manglar baja (NDVI -0.25 en el pico) y se recupera en 6 meses
            f_sequia = 1.0
            if escenario == "sequia":
                if SEQUIA[0] <= d <= SEQUIA[1]:
                    f_sequia = 0.365
                elif SEQUIA[1] < d <= SEQUIA[1] + timedelta(days=183):
                    f_sequia = 0.365 + 0.635 * (d - SEQUIA[1]).days / 183
            bandas = []
            for b in range(4):
                arr = np.zeros((grid.height, grid.width), np.float32)
                for nombre, refl in REFLECTANCIA.items():
                    sel = clase == nombre
                    factor = 1.0 + (0.06 * est if b == 2 and nombre in ("manglar", "cultivo") else 0.0)
                    if b == 2 and nombre == "manglar":
                        factor *= f_sequia
                    arr[sel] = refl[b] * factor
                if escenario == "neblina" and n_escena in NEBLINA_FRIA:
                    arr[:] = NUBE[b]                                   # nube que cubre todo el recuadro
                elif escenario == "neblina" and n_escena in BRUMA_TIBIA:
                    arr = 0.6 * arr + 0.4 * NUBE[b]                    # bruma: mezcla nube + superficie
                arr += rng.normal(0, 0.006, arr.shape).astype(np.float32)
                bandas.append(np.clip(arr, 0.001, 1.0))
            # nubes locales y bandeado tipo SLC-off
            valido = grid.polygon_interior_mask.copy()
            if rng.random() < 0.4:
                r0, c0 = rng.integers(0, grid.height), rng.integers(0, grid.width)
                valido[max(0, r0 - 4):r0 + 4, max(0, c0 - 4):c0 + 4] = False
            if 2003 <= d.year <= 2012 and rng.random() < 0.3:
                valido[:, ::7] = False
            bandas = [np.where(valido, x, np.nan) for x in bandas]
            arrays = mango.scene_indices(*bandas, cfg)
            lst = np.zeros((grid.height, grid.width), np.float32)
            for nombre, t in LST_BASE.items():
                lst[clase == nombre] = t + 2.5 * est
            lst += rng.normal(0, 0.5, lst.shape).astype(np.float32)
            arrays["lst_c"] = np.where(valido, lst, np.nan).astype(np.float32)
            cobertura = float(valido.sum()) / grid.total_polygon_pixels
            if cobertura >= cfg.min_valid_polygon_coverage:
                escenas.append(mango.SceneObservation(
                    scene_id=f"SINT_{escenario}_{d.isoformat()}", date=d, year_month=d.strftime("%Y-%m"),
                    platform="landsat-8" if d.year >= 2013 else "landsat-5",
                    valid_coverage=round(cobertura, 4), arrays=arrays, st_uncertainty_k=1.2))
        d += timedelta(days=16)
    return escenas


def gmw_sintetico(grid: mango.CanonicalGrid, escenario: str, seed: int = 11) -> dict:
    cov = _coberturas(grid, escenario, seed)
    m = cov["manglar"] & grid.polygon_interior_mask
    serie = []
    for anio in range(1985, 2026):
        mk = m.copy()
        if escenario in ("B", "B_parcial") and anio >= FECHA_QUIEBRE.year + 1:
            mk &= ~cov["afectado"]
        serie.append({"anio": anio, "ha": round(float(mk.sum()) * grid.pixel_area_ha, 2)})
    return {"disponible": True, "mascara": m, "anio_referencia": 2005, "serie_anual": serie, "mosaicos": 1}


@contextmanager
def fuentes_sinteticas(escenario: str):
    """Sustituye SOLO las descargas; el análisis científico es el código real."""
    sur = escenario == "sur"

    def _power(lon, lat, hist_start, end_date, config):
        return clima_sintetico(end_date, hemisferio_sur=sur).loc[hist_start:end_date]

    def _landsat(grid, start_date, end_date, config=None, progreso=mango._sin_progreso):
        # El "planeta sintético" tiene registro satelital solo en MUNDO: pedir el histórico completo
        # devuelve siempre las mismas escenas, como el archivo Landsat real.
        ini, fin = max(start_date, MUNDO[0]), min(end_date, MUNDO[1])
        esc = escenas_sinteticas(grid, ini, fin, escenario, hemisferio_sur=sur)
        for i in range(len(esc)):
            progreso((i + 1) / max(1, len(esc)), f"Procesando escenas Landsat {i + 1}/{len(esc)}")
        return {"scenes": esc, "stats": {
            "stac_candidates": len(esc), "valid_scenes_retained": len(esc),
            "discarded_by_cloud_or_slc": 0, "failed_remote_assets": 0,
            "grid_shape": [grid.height, grid.width], "epsg": grid.epsg_code,
            "max_stac_cloud_cover": 60.0, "plataformas": sorted({s.platform for s in esc})}}

    def _gmw(grid, reference_year, config):
        return gmw_sintetico(grid, escenario)

    with mock.patch.object(mango, "_download_power", _power), \
         mock.patch.object(mango, "fetch_landsat_scenes", _landsat), \
         mock.patch.object(mango, "load_gmw_extent", _gmw):
        yield


def ejecutar(escenario: str, inicio: str = "2005-01-01", fin: str = "2024-12-31",
             progreso: mango.Progreso | None = None) -> str:
    if escenario not in ESCENARIOS:
        raise ValueError(f"Escenario desconocido: {escenario}")
    bbox = BBOX["sur" if escenario == "sur" else "norte"]
    with fuentes_sinteticas(escenario):
        return mango.run_mango_pipeline(bbox, inicio, fin, mango.DEFAULT_CONFIG, progreso)
