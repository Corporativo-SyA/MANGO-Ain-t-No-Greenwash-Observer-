"""
Convierte los mosaicos GeoTIFF de Global Mangrove Watch v4.1 en una CUADRÍCULA
de celdas con manglar (CSV y, opcional, GeoJSON) lista para colorear el mapa y
para revisar si un polígono cae sobre manglar (Caso C).

Formato de entrada (GMW v4.1, JAXA/EORC):
  - Un archivo por mosaico de 1° x 1°, p. ej. GMW_S36E174_v4112_mng_ext.tif
  - 41 bandas: banda 1 = 1985 ... banda 41 = 2025
  - Píxel de ~30 m (0.000269469°), valor 1 = manglar, 0 = no manglar, EPSG:4326

Salida: solo las celdas que tienen manglar.
  lat_min,lat_max,lon_min,lon_max,ha_celda,ha_manglar_2025,pct_manglar_2025[,...]

Uso (directo sobre el zip de 1.3 GB, sin descomprimir):
  python scripts/generar_cuadricula_gmw.py RUTA/gmw_v4112.zip --celda 0.1 --geojson
  python scripts/generar_cuadricula_gmw.py RUTA/gmw_v4112.zip --celda 0.01
  python scripts/generar_cuadricula_gmw.py RUTA/carpeta_tifs --anios 2025 1985 --celda 0.1

Opciones útiles:
  --anios 2025 1985    año a mostrar + año base: columnas de ambos y "cambio_ha"
  --anios 1985-2025    serie histórica: una columna ha_manglar_<año> por año
  --anios 1985-2025:5  serie cada 5 años (1985, 1990, ..., 2025)
  --region O S E N     solo celdas cuyo centro cae en el rectángulo; se puede repetir
                       varias veces para aproximar la forma de un país
  --nombre mexico      nombre del archivo de salida (sin extensión)
  --procesos 4         usa varios núcleos

Regla de columnas:
  - 1 o 2 años: ha_manglar_<año> y pct_manglar_<año>; con 2, cambio_ha = año1 - año2
  - 3 o más:    solo ha_manglar_<año> (pct = ha / ha_celda); cambio_ha = último - primero

Atribución obligatoria donde se muestre: ©Global Mangrove Watch (CC BY 4.0).
"""

import argparse
import csv
import json
import math
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path

import numpy as np
import rasterio

ANIO_BANDA_1 = 1985
RADIO_TIERRA_M = 6_371_008.8


def listar_mosaicos(entrada: Path) -> list[str]:
    """Rutas legibles por rasterio: archivos sueltos o /vsizip/ dentro del zip."""
    if entrada.is_dir():
        return sorted(str(p) for p in entrada.rglob("*.tif"))
    if entrada.suffix.lower() == ".zip":
        with zipfile.ZipFile(entrada) as z:
            nombres = sorted(n for n in z.namelist() if n.lower().endswith((".tif", ".tiff")))
        return [f"/vsizip/{entrada.resolve().as_posix()}/{n}" for n in nombres]
    if entrada.suffix.lower() in (".tif", ".tiff"):
        return [str(entrada)]
    raise SystemExit(f"No sé leer {entrada}: usa un .zip, una carpeta o un .tif")


def area_fila_ha(lat_sup: np.ndarray, lat_inf: np.ndarray, dlon_grados: float) -> np.ndarray:
    """Área exacta (esfera) de una franja de píxeles, en hectáreas."""
    return (RADIO_TIERRA_M ** 2 * math.radians(dlon_grados)
            * np.abs(np.sin(np.radians(lat_sup)) - np.sin(np.radians(lat_inf)))) / 10_000


def cortes(origen: float, paso_pix: float, n: int, celda: float, sentido: int) -> np.ndarray:
    """Índices donde empieza cada celda a lo largo de un eje (para np.add.reduceat)."""
    centros = origen + sentido * (np.arange(n) + 0.5) * paso_pix
    rel = (centros - origen) * sentido
    idx = np.floor(rel / celda + 1e-9).astype(np.int64)
    return np.flatnonzero(np.r_[True, idx[1:] != idx[:-1]])


def _toca(region: list[float], izq: float, inf: float, der: float, sup: float) -> bool:
    return not (der <= region[0] or izq >= region[2] or sup <= region[1] or inf >= region[3])


def _dentro(regiones: list[list[float]] | None, lon: float, lat: float) -> bool:
    return not regiones or any(r[0] <= lon < r[2] and r[1] <= lat < r[3] for r in regiones)


def procesar_mosaico(ruta: str, celda: float, anios: list[int], decimales: int,
                     regiones: list[list[float]] | None) -> list[dict]:
    with rasterio.open(ruta) as src:
        izq, inf, der, sup = src.bounds
        if regiones and not any(_toca(r, izq, inf, der, sup) for r in regiones):
            return []
        res_x, res_y = src.res
        h, w = src.height, src.width
        bandas = [a - ANIO_BANDA_1 + 1 for a in anios]
        if max(bandas) > src.count or min(bandas) < 1:
            raise ValueError(f"{ruta}: tiene {src.count} bandas, no alcanza para {anios}")

        # Área de cada fila de píxeles (cambia con la latitud)
        lat_sup_fila = sup - np.arange(h) * res_y
        area_pix = area_fila_ha(lat_sup_fila, lat_sup_fila - res_y, res_x).astype(np.float64)

        filas = cortes(sup, res_y, h, celda, -1)
        cols = cortes(izq, res_x, w, celda, +1)
        # Área geométrica exacta de cada celda (tierra + agua), a partir de sus límites.
        # No se cuenta por píxeles: una celda de 0.01° abarca 37 o 38 píxeles y eso
        # inflaría hasta 5 % el área de algunas celdas.
        lat_sup_celda = sup - np.arange(len(filas)) * celda
        area_por_fila = area_fila_ha(lat_sup_celda, lat_sup_celda - celda, celda)
        area_celda = np.repeat(area_por_fila[:, None], len(cols), axis=1)

        por_anio = []
        for b in bandas:
            m = src.read(b) == 1
            if not m.any():
                por_anio.append(None)
                continue
            ha = m.astype(np.float32) * area_pix[:, None].astype(np.float32)
            ha = np.add.reduceat(np.add.reduceat(ha, cols, axis=1), filas, axis=0)
            por_anio.append(ha)

    if all(x is None for x in por_anio):
        return []
    forma = area_celda.shape
    por_anio = [x if x is not None else np.zeros(forma, np.float32) for x in por_anio]
    con_manglar = np.zeros(forma, bool)
    for x in por_anio:
        con_manglar |= x > 0

    filas_salida = []
    paso = Decimal(str(celda))
    sup_d, izq_d = Decimal(str(round(sup, 6))), Decimal(str(round(izq, 6)))
    serie = len(anios) > 2
    for i, j in zip(*np.nonzero(con_manglar), strict=True):
        lat_max = sup_d - paso * int(i)
        lon_min = izq_d + paso * int(j)
        if not _dentro(regiones, float(lon_min + paso / 2), float(lat_max - paso / 2)):
            continue
        fila = {"lat_min": float(lat_max - paso), "lat_max": float(lat_max),
                "lon_min": float(lon_min), "lon_max": float(lon_min + paso),
                "ha_celda": round(float(area_celda[i, j]), 1)}
        for anio, x in zip(anios, por_anio, strict=True):
            ha = float(x[i, j])
            fila[f"ha_manglar_{anio}"] = round(ha, 2)
            if not serie:
                # Tope en 100: una celda puede recibir 38 filas/columnas de píxeles en vez de 37.11
                fila[f"pct_manglar_{anio}"] = round(min(100.0, 100 * ha / float(area_celda[i, j])), 2)
        if serie:
            fila["cambio_ha"] = round(fila[f"ha_manglar_{anios[-1]}"] - fila[f"ha_manglar_{anios[0]}"], 2)
        elif len(anios) == 2:
            fila["cambio_ha"] = round(fila[f"ha_manglar_{anios[0]}"] - fila[f"ha_manglar_{anios[1]}"], 2)
        filas_salida.append(fila)
    return filas_salida


def _tarea(args):
    ruta, celda, anios, decimales, regiones = args
    try:
        return ruta, procesar_mosaico(ruta, celda, anios, decimales, regiones), None
    except Exception as e:  # un mosaico dañado no detiene todo
        return ruta, [], repr(e)


def escribir_geojson(filas: list[dict], ruta: Path, anio: int) -> None:
    feats = []
    for f in filas:
        x0, y0, x1, y1 = f["lon_min"], f["lat_min"], f["lon_max"], f["lat_max"]
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon",
                                   "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
                      "properties": {k: v for k, v in f.items()
                                     if k not in ("lat_min", "lat_max", "lon_min", "lon_max")}})
    datos = {"type": "FeatureCollection",
             "metadata": {"fuente": "Global Mangrove Watch v4.1", "atribucion": "©Global Mangrove Watch",
                          "licencia": "CC BY 4.0", "anio": anio},
             "features": feats}
    ruta.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def interpretar_anios(valores: list[str]) -> list[int]:
    """'2025' | '2025 1985' | '1985-2025' | '1985-2025:5'  ->  lista de años."""
    anios: list[int] = []
    for v in valores:
        if "-" in v:
            rango, _, paso = v.partition(":")
            ini, fin = (int(x) for x in rango.split("-"))
            paso_i = int(paso) if paso else 1
            lista = list(range(ini, fin + 1, paso_i))
            if lista[-1] != fin:
                lista.append(fin)
            anios += lista
        else:
            anios.append(int(v))
    if any(not 1985 <= x <= 2025 for x in anios):
        raise SystemExit("--anios: los años deben estar entre 1985 y 2025")
    if len(anios) > 2:
        anios = sorted(set(anios))      # serie histórica en orden cronológico
    elif len(set(anios)) != len(anios):
        raise SystemExit("--anios: años repetidos")
    return anios


def main():
    p = argparse.ArgumentParser(description="Cuadrícula de manglar a partir de GMW v4.1")
    p.add_argument("entrada", type=Path, help=".zip de GMW, carpeta con .tif o un .tif")
    p.add_argument("--celda", type=float, default=0.1, help="tamaño de celda en grados (0.1, 0.05, 0.01...)")
    p.add_argument("--anios", nargs="+", default=["2025"],
                   help="2025 | 2025 1985 | 1985-2025 | 1985-2025:5 (ver docstring)")
    p.add_argument("--region", type=float, nargs=4, action="append",
                   metavar=("OESTE", "SUR", "ESTE", "NORTE"),
                   help="rectángulo a incluir; repetir para combinar varios")
    p.add_argument("--nombre", default=None, help="nombre del archivo de salida, sin extensión")
    p.add_argument("--min-ha", type=float, default=0.5,
                   help="descarta celdas con menos manglar (ruido); default 0.5 ha")
    p.add_argument("--salida", type=Path, default=None, help="carpeta de salida")
    p.add_argument("--geojson", action="store_true", help="además escribir GeoJSON de rectángulos")
    p.add_argument("--procesos", type=int, default=1)
    a = p.parse_args()

    if not (0 < a.celda <= 1) or abs(round(1 / a.celda) - 1 / a.celda) > 1e-9:
        raise SystemExit("--celda debe dividir exacto 1 grado: 1, 0.5, 0.25, 0.2, 0.1, 0.05, 0.02, 0.01")
    a.anios = interpretar_anios(a.anios)
    decimales = max(0, -Decimal(str(a.celda)).as_tuple().exponent)

    mosaicos = listar_mosaicos(a.entrada)
    texto_anios = (f"{a.anios[0]}..{a.anios[-1]} ({len(a.anios)} años)" if len(a.anios) > 2
                   else str(a.anios))
    print(f"{len(mosaicos)} mosaicos | celda {a.celda}° | años {texto_anios}"
          + (f" | {len(a.region)} región(es)" if a.region else " | todo el mundo"))
    salida = a.salida or Path(__file__).resolve().parent.parent / "datos_iniciales" / "gmw"
    salida.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    filas, errores = [], []
    tareas = [(m, a.celda, a.anios, decimales, a.region) for m in mosaicos]
    if a.procesos > 1:
        with ProcessPoolExecutor(a.procesos) as pool:
            resultados = pool.map(_tarea, tareas, chunksize=4)
            for n, (ruta, f, err) in enumerate(resultados, 1):
                filas += f
                if err:
                    errores.append((ruta, err))
                if n % 25 == 0 or n == len(tareas):
                    print(f"  {n}/{len(tareas)} mosaicos, {len(filas)} celdas, {time.time() - t0:.0f} s")
    else:
        for n, t in enumerate(tareas, 1):
            ruta, f, err = _tarea(t)
            filas += f
            if err:
                errores.append((ruta, err))
            if n % 25 == 0 or n == len(tareas):
                print(f"  {n}/{len(tareas)} mosaicos, {len(filas)} celdas, {time.time() - t0:.0f} s")

    anio = a.anios[-1] if len(a.anios) > 2 else a.anios[0]
    filas = [f for f in filas if max(f[f"ha_manglar_{x}"] for x in a.anios) >= a.min_ha]
    filas.sort(key=lambda f: (-f["lat_max"], f["lon_min"]))
    sufijo = str(a.celda).replace(".", "")
    if a.nombre:
        nombre = a.nombre
    elif len(a.anios) > 2:
        nombre = f"manglares_{a.anios[0]}-{a.anios[-1]}_{sufijo}"
    else:
        nombre = f"manglares_{anio}_{sufijo}" + (f"_vs_{a.anios[1]}" if len(a.anios) > 1 else "")

    if not filas:
        print("No se encontró manglar con esos parámetros.")
        sys.exit(1)
    ruta_csv = salida / f"{nombre}.csv"
    with ruta_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
    print(f"\nCSV: {ruta_csv} ({len(filas)} celdas, {ruta_csv.stat().st_size / 1e6:.1f} MB)")
    if a.geojson:
        ruta_gj = salida / f"{nombre}.geojson"
        escribir_geojson(filas, ruta_gj, anio)
        print(f"GeoJSON: {ruta_gj} ({ruta_gj.stat().st_size / 1e6:.1f} MB)")
    total = sum(f[f"ha_manglar_{anio}"] for f in filas)
    print(f"Manglar total {anio}: {total:,.0f} ha ({total / 100:,.0f} km²)")
    if errores:
        print(f"\n{len(errores)} mosaicos con error:")
        for r, e in errores[:20]:
            print(f"  {r}: {e}")


if __name__ == "__main__":
    main()
