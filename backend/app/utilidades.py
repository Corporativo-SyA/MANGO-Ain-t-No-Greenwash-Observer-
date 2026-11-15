"""Funciones pequeñas compartidas."""

import hashlib
import json
from datetime import UTC, datetime


def ahora_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def meses_entre(inicio: str, fin: str) -> list[str]:
    """['2022-11', '2022-12', '2023-01', ...] ambos extremos incluidos."""
    a, m = map(int, inicio.split("-"))
    a_fin, m_fin = map(int, fin.split("-"))
    meses = []
    while (a, m) <= (a_fin, m_fin):
        meses.append(f"{a:04d}-{m:02d}")
        m += 1
        if m == 13:
            a, m = a + 1, 1
    return meses


def mes_actual() -> str:
    hoy = datetime.now(UTC)
    return f"{hoy.year:04d}-{hoy.month:02d}"


def _redondear(obj, decimales=6):
    if isinstance(obj, float):
        return round(obj, decimales)
    if isinstance(obj, list):
        return [_redondear(o, decimales) for o in obj]
    return obj


def huella(datos: dict) -> str:
    """Hash estable de los parámetros de una consulta (para la caché)."""
    normal = json.loads(json.dumps(datos, sort_keys=True))
    if "poligono" in normal:
        normal["poligono"]["coordinates"] = _redondear(normal["poligono"]["coordinates"])
    texto = json.dumps(normal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:24]
