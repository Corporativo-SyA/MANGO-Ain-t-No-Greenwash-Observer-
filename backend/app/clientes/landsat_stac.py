"""
Conector CRUDO al catálogo STAC de Microsoft Planetary Computer
(colección landsat-c2-l2: Landsat 5, 7, 8 y 9).

Hace dos cosas, sin ciencia:
  1. buscar_escenas(): búsqueda POST -> lista de escenas con URLs firmadas a COG.
  2. leer_ventana():   lectura remota por ventana (windowed reading) de un COG.

Ojo: las URLs firmadas caducan (~1 h). Leer las bandas poco después de buscarlas.
"""

from collections.abc import Iterable

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from pystac_client.stac_api_io import StacApiIO
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape
from urllib3.util.retry import Retry

from app.errores import ErrorFuenteExterna
from app.utilidades import meses_entre

URL_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLECCION = "landsat-c2-l2"
FUENTE = "Planetary Computer STAC"

# Opciones de GDAL para leer COG remotos con reintentos y sin listar directorios
ENTORNO_GDAL = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_HTTP_TIMEOUT": "60",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
}


def _fin_de_mes(mes: str) -> str:
    a, m = map(int, mes.split("-"))
    dias = [31, 29 if (a % 4 == 0 and (a % 100 != 0 or a % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return f"{mes}-{dias:02d}"


class ClienteLandsatSTAC:
    def __init__(self, url: str = URL_STAC, timeout_s: float = 60, reintentos: int = 3):
        self.url = url
        self.timeout_s = timeout_s
        self.reintentos = reintentos
        self._cliente: pystac_client.Client | None = None

    def _catalogo(self) -> pystac_client.Client:
        if self._cliente is None:
            io = StacApiIO(timeout=self.timeout_s, max_retries=Retry(
                total=self.reintentos, backoff_factor=1.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "POST"})))
            try:
                self._cliente = pystac_client.Client.open(
                    self.url, modifier=pc.sign_inplace, stac_io=io)
            except Exception as e:
                raise ErrorFuenteExterna(FUENTE, f"no se pudo abrir el catálogo: {e}") from e
        return self._cliente

    def buscar_escenas(self, geometria: dict, mes_inicio: str, mes_fin: str,
                       max_nubes: float | None = None,
                       plataformas: Iterable[str] | None = None,
                       limite: int | None = None) -> list[dict]:
        """
        Devuelve una lista (ordenada por fecha) de:
        {"id", "fecha" (AAAA-MM-DD), "plataforma", "nubosidad", "epsg", "bbox",
         "assets": {"red": url_firmada, "nir08": ..., "qa_pixel": ...}}
        """
        consulta = {}
        if max_nubes is not None:
            consulta["eo:cloud_cover"] = {"lt": max_nubes}
        if plataformas:
            consulta["platform"] = {"in": list(plataformas)}
        try:
            busqueda = self._catalogo().search(
                collections=[COLECCION], intersects=geometria,
                datetime=f"{mes_inicio}-01/{_fin_de_mes(mes_fin)}",
                query=consulta or None, max_items=limite)
            items = list(busqueda.items())
        except ErrorFuenteExterna:
            raise
        except Exception as e:
            raise ErrorFuenteExterna(FUENTE, f"falló la búsqueda: {e}") from e

        escenas = []
        for it in items:
            p = it.properties
            escenas.append({
                "id": it.id,
                "fecha": (it.datetime.date().isoformat() if it.datetime else None),
                "plataforma": p.get("platform"),
                "nubosidad": p.get("eo:cloud_cover"),
                "epsg": p.get("proj:epsg") or p.get("proj:code"),
                "bbox": list(it.bbox) if it.bbox else None,
                "assets": {k: a.href for k, a in it.assets.items()
                           if (a.media_type or "").startswith("image/tiff")},
            })
        return sorted(escenas, key=lambda e: e["fecha"] or "")

    def buscar_por_mes(self, geometria: dict, mes_inicio: str, mes_fin: str,
                       **kwargs) -> dict[str, list[dict]]:
        """Igual que buscar_escenas pero agrupado: {"2022-01": [...], "2022-02": [], ...}."""
        agrupado = {m: [] for m in meses_entre(mes_inicio, mes_fin)}
        for e in self.buscar_escenas(geometria, mes_inicio, mes_fin, **kwargs):
            if e["fecha"]:
                agrupado.setdefault(e["fecha"][:7], []).append(e)
        return agrupado

    def leer_ventana(self, url_cog: str, geometria: dict) -> dict:
        """
        Lee SOLO el recorte del COG que cubre la geometría (sin bajar la escena entera).
        Devuelve {"datos": np.ndarray (filas, columnas), "transform": [a,b,c,d,e,f],
                  "crs": "EPSG:xxxxx", "nodata": valor}
        """
        x0, y0, x1, y1 = shape(geometria).bounds
        try:
            with rasterio.Env(**ENTORNO_GDAL), rasterio.open(url_cog) as src:
                limites = transform_bounds("EPSG:4326", src.crs, x0, y0, x1, y1, densify_pts=21)
                ventana = from_bounds(*limites, transform=src.transform)
                ventana = ventana.round_offsets().round_lengths()
                completa = Window(0, 0, src.width, src.height)
                try:
                    ventana = ventana.intersection(completa)
                except Exception as e:
                    raise ErrorFuenteExterna(FUENTE, "la geometría no cae dentro de la escena") from e
                datos = src.read(1, window=ventana)
                transform = src.window_transform(ventana)
                return {"datos": np.asarray(datos), "transform": list(transform)[:6],
                        "crs": src.crs.to_string(), "nodata": src.nodata}
        except ErrorFuenteExterna:
            raise
        except Exception as e:
            raise ErrorFuenteExterna(FUENTE, f"no se pudo leer el COG: {e}") from e
