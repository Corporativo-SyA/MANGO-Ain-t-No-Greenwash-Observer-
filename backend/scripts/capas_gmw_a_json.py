"""
Convierte las cuadrículas GMW (CSV de scripts/generar_cuadricula_gmw.py) a JSON compacto para la capa
de manglares del mapa. Los JSON van a frontend/public/capas/ (se sirven como estáticos y solo se
descargan cuando el usuario activa la capa).

Uso (desde backend/):
    uv run python scripts/capas_gmw_a_json.py --mundo RUTA/manglares_2025_01.csv \
        --mexico RUTA/manglares_mexico_1985-2025_001.csv

Formato (enteros para que pese poco):
  gmw_mundo_2025.json  {"celda": 0.1, "anio": 2025, "escala": 10,
                        "lat": [lat_min*10], "lon": [lon_min*10], "pct": [pct*10]}
  gmw_mexico.json      {"celda": 0.01, "anios": [1985..2025], "escala": 100,
                        "lat": [lat_min*100], "lon": [lon_min*100],
                        "pm": [[v_1985, i, v_i, j, v_j, ...], ...]}   # por mil del área de la celda;
                        solo se guardan los años en que el valor CAMBIA (codificación por cambios)
Atribución obligatoria donde se muestre: ©Global Mangrove Watch (CC BY 4.0).
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DESTINO = Path(__file__).resolve().parents[2] / "frontend" / "public" / "capas"


def mundo(csv: Path) -> dict:
    d = pd.read_csv(csv)
    return {"celda": 0.1, "anio": 2025, "escala": 10,
            "lat": np.rint(d.lat_min * 10).astype(int).tolist(),
            "lon": np.rint(d.lon_min * 10).astype(int).tolist(),
            "pct": np.rint(d.pct_manglar_2025.clip(0, 100) * 10).astype(int).tolist(),
            "fuente": "©Global Mangrove Watch v4.1 (CC BY 4.0)"}


def mexico(csv: Path) -> dict:
    d = pd.read_csv(csv)
    cols = sorted([c for c in d.columns if c.startswith("ha_manglar_")], key=lambda c: int(c[-4:]))
    anios = [int(c[-4:]) for c in cols]
    pm = np.rint(d[cols].to_numpy() / d.ha_celda.to_numpy()[:, None] * 1000).clip(0, 1000).astype(int)
    codif = []
    for fila in pm:
        r, previo = [int(fila[0])], fila[0]
        for i, v in enumerate(fila[1:], start=1):
            if v != previo:
                r += [i, int(v)]
                previo = v
        codif.append(r)
    return {"celda": 0.01, "anios": anios, "escala": 100,
            "lat": np.rint(d.lat_min * 100).astype(int).tolist(),
            "lon": np.rint(d.lon_min * 100).astype(int).tolist(),
            "pm": codif, "fuente": "©Global Mangrove Watch v4.1 (CC BY 4.0)"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mundo", type=Path, required=True)
    ap.add_argument("--mexico", type=Path, required=True)
    ap.add_argument("--destino", type=Path, default=DESTINO)
    a = ap.parse_args()
    a.destino.mkdir(parents=True, exist_ok=True)
    for nombre, datos in (("gmw_mundo_2025.json", mundo(a.mundo)), ("gmw_mexico.json", mexico(a.mexico))):
        ruta = a.destino / nombre
        ruta.write_text(json.dumps(datos, separators=(",", ":")), encoding="utf-8")
        print(f"{ruta}  {ruta.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
