"""
Puente backend <-> pipeline MANGO v2.

Las rutas y los trabajos SOLO llaman a `ejecutar_experimento`. Aquí se:
  - normalizan las fechas AAAA-MM a AAAA-MM-01 / último día del mes (sin pasar de hoy - 1),
  - construye la configuración del pipeline (GMW, hilos),
  - traduce cualquier falla de red a ErrorFuenteExterna (mensaje claro para el usuario).
"""

import calendar
import json
from collections.abc import Callable
from datetime import date, timedelta

import requests
from rasterio.errors import RasterioIOError

from app.errores import ErrorFuenteExterna
from app.pipeline import mango

ReportarProgreso = Callable[[float, str], None]


def normalizar_fechas(mes_inicio: str, mes_fin: str, hoy: date | None = None) -> tuple[str, str]:
    """'2005-01','2024-12' -> '2005-01-01','2024-12-31'. El fin nunca pasa de ayer."""
    hoy = hoy or date.today()
    a0, m0 = map(int, mes_inicio.split("-"))
    a1, m1 = map(int, mes_fin.split("-"))
    inicio = date(a0, m0, 1)
    fin = date(a1, m1, calendar.monthrange(a1, m1)[1])
    fin = min(fin, hoy - timedelta(days=1))
    return inicio.isoformat(), fin.isoformat()


def ejecutar_experimento(
    bbox: tuple[float, float, float, float],
    fecha_inicio: str,
    fecha_fin: str,
    reportar: ReportarProgreso,
    *,
    gmw_ruta: str | None = None,
    hilos: int = 8,
    cache_dir: str | None = None,
) -> dict:
    """Devuelve el dict de salida de MANGO (COMPLETED / REJECTED_CONTROLLED / INSUFFICIENT_DATA)."""
    inicio, fin = normalizar_fechas(fecha_inicio, fecha_fin)
    config = mango.PipelineConfig(gmw_path=gmw_ruta, max_workers=hilos, cache_dir=cache_dir)
    try:
        texto = mango.run_mango_pipeline(bbox, inicio, fin, config, reportar)
    except mango.FuenteDatosError as e:
        raise ErrorFuenteExterna(e.fuente, str(e)) from e
    except (requests.RequestException, RasterioIOError, ConnectionError, TimeoutError) as e:
        raise ErrorFuenteExterna("red", str(e)) from e
    return json.loads(texto)
