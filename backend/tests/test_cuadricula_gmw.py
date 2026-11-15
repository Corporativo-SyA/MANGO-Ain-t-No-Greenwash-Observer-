"""Prueba del script de cuadrícula GMW con un mosaico sintético (sin descargar nada)."""

import csv
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generar_cuadricula_gmw.py"
N = 3711                      # píxeles por lado, igual que GMW v4.1
RES = 1 / N


def _mosaico(ruta: Path, oeste: float, norte: float) -> None:
    """41 bandas: manglar en un bloque de la celda [norte-0.2, norte-0.1] x [oeste+0.3, oeste+0.4].
    1985 (banda 1) tiene el doble de manglar que 2025 (banda 41)."""
    datos = np.zeros((41, N, N), dtype=np.uint8)
    f0, c0 = int(0.12 * N), int(0.32 * N)
    datos[:, f0:f0 + 100, c0:c0 + 100] = 1          # 10,000 px todos los años
    datos[0, f0 + 100:f0 + 200, c0:c0 + 100] = 1    # +10,000 px solo en 1985
    perfil = dict(driver="GTiff", width=N, height=N, count=41, dtype="uint8", crs="EPSG:4326",
                  transform=from_origin(oeste, norte, RES, RES), nodata=0, compress="deflate")
    with rasterio.open(ruta, "w", **perfil) as dst:
        dst.write(datos)


def _cargar_modulo():
    spec = importlib.util.spec_from_file_location("gcg", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_celdas_y_hectareas(tmp_path):
    tif = tmp_path / "GMW_N19W097_v4112_mng_ext.tif"
    _mosaico(tif, -97.0, 19.0)
    mod = _cargar_modulo()
    filas = mod.procesar_mosaico(str(tif), 0.1, [2025, 1985], 1, None)
    assert len(filas) == 1
    f = filas[0]
    assert (f["lat_min"], f["lat_max"], f["lon_min"], f["lon_max"]) == (18.8, 18.9, -96.7, -96.6)
    # 10,000 px de ~0.085 ha (28.4 m x 30 m cerca de 19°N) ≈ 850 ha; en 1985 el doble
    assert 830 < f["ha_manglar_2025"] < 870
    assert abs(f["ha_manglar_1985"] - 2 * f["ha_manglar_2025"]) < 1
    assert f["cambio_ha"] < 0                                   # se perdió manglar
    # Área geométrica de una celda de 0.1° entre 18.8 y 18.9 °N ≈ 11,693 ha
    assert abs(f["ha_celda"] - 11693) < 15


def test_area_de_celda_no_depende_de_los_pixeles(tmp_path):
    tif = tmp_path / "GMW_N30W090_v4112_mng_ext.tif"
    perfil = dict(driver="GTiff", width=N, height=N, count=41, dtype="uint8", crs="EPSG:4326",
                  transform=from_origin(-90.0, 30.0, RES, RES), nodata=0)
    datos = np.zeros((41, N, N), dtype=np.uint8)
    datos[:, :400, :] = 1                         # manglar en toda la franja norte
    with rasterio.open(tif, "w", **perfil) as dst:
        dst.write(datos)
    filas = _cargar_modulo().procesar_mosaico(str(tif), 0.01, [2025], 2, None)
    fila_norte = [f for f in filas if f["lat_max"] == 30.0]
    assert len(fila_norte) == 100
    assert len({f["ha_celda"] for f in fila_norte}) == 1       # todas iguales en la misma latitud
    assert all(95 < f["pct_manglar_2025"] <= 100 for f in fila_norte)


def test_zip_region_y_salidas(tmp_path):
    for nombre, oeste, norte in [("GMW_N19W097_v4112_mng_ext.tif", -97.0, 19.0),
                                 ("GMW_N02E100_v4112_mng_ext.tif", 100.0, 2.0)]:
        _mosaico(tmp_path / nombre, oeste, norte)
    zipf = tmp_path / "gmw.zip"
    with zipfile.ZipFile(zipf, "w") as z:
        for t in tmp_path.glob("*.tif"):
            z.write(t, f"gmw_v4112/{t.name}")
    salida = tmp_path / "salida"
    r = subprocess.run([sys.executable, str(SCRIPT), str(zipf), "--celda", "0.01", "--geojson",
                        "--region", "-118", "14", "-86", "33", "--salida", str(salida)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with (salida / "manglares_2025_001.csv").open() as fh:
        filas = list(csv.DictReader(fh))
    assert filas and all(-97 <= float(f["lon_min"]) < -96 for f in filas)   # solo México
    assert (salida / "manglares_2025_001.geojson").exists()


def test_serie_historica_y_varias_regiones(tmp_path):
    """--anios 1985-2025 da 41 columnas; --region repetido recorta por celda."""
    _mosaico(tmp_path / "GMW_N19W097_v4112_mng_ext.tif", -97.0, 19.0)
    salida = tmp_path / "salida"
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--celda", "0.1",
                        "--anios", "1985-2025", "--nombre", "mexico_historico",
                        "--region", "-97", "18.8", "-96.6", "18.9",       # contiene la celda
                        "--region", "-120", "30", "-110", "31",           # no toca nada
                        "--salida", str(salida)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with (salida / "mexico_historico.csv").open() as fh:
        filas = list(csv.DictReader(fh))
    assert len(filas) == 1
    columnas = [c for c in filas[0] if c.startswith("ha_manglar_")]
    assert columnas[0] == "ha_manglar_1985" and columnas[-1] == "ha_manglar_2025" and len(columnas) == 41
    assert not any(c.startswith("pct_") for c in filas[0])           # serie: solo hectáreas
    assert float(filas[0]["cambio_ha"]) < 0                           # 2025 - 1985: pérdida

    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--celda", "0.1",
                        "--region", "-96.6", "18.8", "-96.0", "18.9", "--salida", str(salida)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "No se encontró manglar" in r.stdout    # celda fuera de la región
