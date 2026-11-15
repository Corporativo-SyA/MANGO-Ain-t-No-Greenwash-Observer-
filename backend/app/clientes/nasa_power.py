"""
Conector CRUDO a NASA POWER (GET, JSON). Sin ciencia: solo descarga, limpia el
centinela -999 y normaliza las fechas a AAAA-MM-DD.

Documentación: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
"""

from collections.abc import Iterable
from datetime import date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.errores import ErrorFuenteExterna

URL_DIARIO = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETROS_POR_DEFECTO = ("PRECTOTCORR", "WS10M_MAX", "WD10M", "T2M_MAX")
CENTINELA = -999.0
FUENTE = "NASA POWER"


def _sesion_con_reintentos(reintentos: int) -> requests.Session:
    reintento = Retry(total=reintentos, backoff_factor=1.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}), raise_on_status=False)
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=reintento))
    s.headers["User-Agent"] = "manglares-mvp/0.1"
    return s


class ClienteNasaPower:
    def __init__(self, timeout_s: float = 30, reintentos: int = 3,
                 sesion: requests.Session | None = None):
        self.timeout_s = timeout_s
        self.sesion = sesion or _sesion_con_reintentos(reintentos)

    def obtener_diario(self, lat: float, lon: float, inicio: date, fin: date,
                       parametros: Iterable[str] = PARAMETROS_POR_DEFECTO,
                       comunidad: str = "RE") -> dict:
        """
        Devuelve:
        {
          "fuente": "NASA POWER", "lat": .., "lon": .., "inicio": "AAAA-MM-DD", "fin": "...",
          "unidades": {"PRECTOTCORR": "mm/day", ...},
          "datos": {"PRECTOTCORR": {"2022-01-01": 0.4, "2022-01-02": null, ...}, ...}
        }
        """
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("lat/lon fuera de rango")
        if inicio > fin:
            raise ValueError("inicio > fin")
        params = {
            "parameters": ",".join(parametros), "community": comunidad,
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "start": inicio.strftime("%Y%m%d"), "end": fin.strftime("%Y%m%d"),
            "format": "JSON",
        }
        try:
            r = self.sesion.get(URL_DIARIO, params=params, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise ErrorFuenteExterna(FUENTE, f"sin conexión: {e}") from e
        if r.status_code != 200:
            raise ErrorFuenteExterna(FUENTE, f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            cuerpo = r.json()
            crudo = cuerpo["properties"]["parameter"]
        except (ValueError, KeyError) as e:
            raise ErrorFuenteExterna(FUENTE, "respuesta con formato inesperado") from e

        datos = {}
        for nombre, serie in crudo.items():
            datos[nombre] = {
                f"{k[:4]}-{k[4:6]}-{k[6:8]}": (None if v is None or float(v) <= CENTINELA else float(v))
                for k, v in serie.items()
            }
        unidades = {k: v.get("units") for k, v in cuerpo.get("parameters", {}).items()}
        return {"fuente": FUENTE, "lat": lat, "lon": lon,
                "inicio": inicio.isoformat(), "fin": fin.isoformat(),
                "unidades": unidades, "datos": datos}
