"""
Mide el sesgo de NDVI entre Landsat 7 (ETM+, LEDAPS) y Landsat 8/9 (OLI, LaSRC) en las zonas reales,
para decidir con datos si hace falta armonizar (Roy et al. 2016). Requiere internet: ~1 h.

Método: escenas de distinto sensor tomadas a <= 8 días entre sí (2013-04 a 2021-12, ambos operando)
-> diferencia de la mediana de NDVI del núcleo (OLI - ETM+). Se compara con el ruido de pasadas del
MISMO sensor a <= 16 días. Intervalo de confianza del sesgo por bootstrap (2000 remuestreos).

Regla de decisión (se imprime al final):
    armonizar si el IC 95 % del sesgo excluye 0  Y  |sesgo| > σ_mismo_sensor / sqrt(n_pares)

Uso (desde backend/):
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/sesgo_sensores.py
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/sesgo_sensores.py mandinga_zona_usuario
"""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import mango  # noqa: E402

ZONAS = {
    "mandinga_sur": ((-96.105, 19.045, -96.080, 19.070), "2012-01-01", "2022-12-31"),
    "tiburon": ((-96.0897, 19.0554, -96.0787, 19.0654), "2012-01-01", "2022-12-31"),
    "mandinga_zona_usuario": ((-96.0869, 19.0531, -96.0709, 19.0656), "2012-01-01", "2022-12-31"),
    "cananeia": ((-47.935, -25.035, -47.905, -25.005), "2015-01-01", "2022-12-31"),
}
VENTANA_PAR = timedelta(days=8)
VENTANA_MISMO = timedelta(days=16)
OLI = ("landsat-8", "landsat-9")


def sesgo_entre_sensores(escenas: list, semilla: int = 0) -> dict:
    """Escenas con .date, .platform y .ndvi (mediana del núcleo). Devuelve sesgo OLI - ETM+ y su decisión."""
    validas = sorted([s for s in escenas if np.isfinite(s.ndvi)], key=lambda s: s.date)
    l7 = [s for s in validas if s.platform == "landsat-7"]
    oli = [s for s in validas if s.platform in OLI]
    pares = []
    for a in l7:
        cerca = [b for b in oli if abs(b.date - a.date) <= VENTANA_PAR]
        if cerca:
            b = min(cerca, key=lambda b: abs(b.date - a.date))
            pares.append(b.ndvi - a.ndvi)
    mismos = []
    for grupo in (l7, oli):
        for a, b in zip(grupo, grupo[1:], strict=False):
            if b.date - a.date <= VENTANA_MISMO:
                mismos.append(b.ndvi - a.ndvi)
    if len(pares) < 5 or len(mismos) < 5:
        return {"n_pares": len(pares), "decision": "SIN_DATOS_SUFICIENTES"}
    pares_a = np.asarray(pares)
    rng = np.random.default_rng(semilla)
    boot = np.median(rng.choice(pares_a, size=(2000, pares_a.size), replace=True), axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sesgo = float(np.median(pares_a))
    sigma_mismo = float(1.4826 * np.median(np.abs(np.asarray(mismos) - np.median(mismos))))
    umbral = sigma_mismo / np.sqrt(pares_a.size)
    armonizar = bool((lo > 0 or hi < 0) and abs(sesgo) > umbral)
    return {"n_pares": int(pares_a.size), "sesgo_oli_menos_etm": round(sesgo, 4),
            "ic95": [round(float(lo), 4), round(float(hi), 4)],
            "sigma_mismo_sensor": round(sigma_mismo, 4), "umbral_detectable": round(float(umbral), 4),
            "decision": "ARMONIZAR" if armonizar else "NO_HACE_FALTA"}


def _escenas_de_zona(bbox, inicio, fin, cfg):
    grid = mango.build_canonical_grid(bbox, pixel_size_m=cfg.pixel_size_m)
    clima = mango.fetch_nasa_power_series(grid.centroid_lon, grid.centroid_lat, inicio, fin, cfg)
    gmw = mango.load_gmw_extent(grid, int(inicio[:4]), cfg)
    landsat = mango.fetch_landsat_scenes(grid, inicio, fin, cfg)
    zona = mango.analyze_zone_and_regimes(grid, landsat["scenes"], clima["monthly_df"], cfg, gmw)
    return zona.get("escenas_clasificadas") or []


def main():
    cfg = mango.PipelineConfig(gmw_path=os.getenv("GMW_RUTA") or None)
    ids = sys.argv[1:] or list(ZONAS)
    informe = {}
    for id_ in ids:
        bbox, ini, fin = ZONAS[id_]
        print(f"== {id_} {bbox} {ini} -> {fin}")
        informe[id_] = sesgo_entre_sensores(_escenas_de_zona(bbox, ini, fin, cfg))
        print("  ", informe[id_])
    salida = Path(__file__).resolve().parent.parent / "salidas_regresion" / "sesgo_sensores.json"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    decisiones = {r["decision"] for r in informe.values()}
    print(f"\nInforme: {salida}\nDecisión global: "
          + ("ARMONIZAR (commit 10b: Roy et al. 2016 en scene_indices)" if "ARMONIZAR" in decisiones
             else "no hace falta armonizar"))


if __name__ == "__main__":
    main()
