"""
Validación de INGENIERÍA de la entrada (formato, geometría, límites de rendimiento).
No decide si hay manglar: eso lo decide el pipeline (Caso C / datos insuficientes).
"""

from pyproj import Geod
from shapely.geometry import box, shape
from shapely.validation import explain_validity

from app.config import Config
from app.contrato import SolicitudConsulta
from app.errores import ErrorApp
from app.utilidades import mes_actual, meses_entre

_GEOD = Geod(ellps="WGS84")


def zona_a_bbox(sol: SolicitudConsulta) -> tuple[tuple[float, float, float, float], list[str]]:
    """Devuelve (bbox, advertencias). Un polígono se convierte a su rectángulo envolvente."""
    if sol.bbox is not None:
        oeste, sur, este, norte = sol.bbox
        if not (-180 <= oeste <= 180 and -180 <= este <= 180 and -90 <= sur <= 90 and -90 <= norte <= 90):
            raise ErrorApp("BBOX_INVALIDO",
                           "Coordenadas fuera de rango. El orden es [oeste, sur, este, norte] en grados.", 422)
        if not (oeste < este and sur < norte):
            raise ErrorApp("BBOX_INVALIDO",
                           "El rectángulo está invertido: se requiere oeste < este y sur < norte.", 422)
        return (oeste, sur, este, norte), []
    try:
        geom = shape(sol.poligono.model_dump())
    except Exception as e:
        raise ErrorApp("POLIGONO_INVALIDO", "El polígono no tiene un formato GeoJSON válido.",
                       422, [str(e)]) from e
    if geom.is_empty:
        raise ErrorApp("POLIGONO_INVALIDO", "El polígono está vacío.", 422)
    if not geom.is_valid:
        raise ErrorApp("POLIGONO_INVALIDO", "El polígono se cruza consigo mismo. Vuelve a dibujarlo.",
                       422, [explain_validity(geom)])
    x0, y0, x1, y1 = geom.bounds
    if not (-180 <= x0 <= 180 and -180 <= x1 <= 180 and -90 <= y0 <= 90 and -90 <= y1 <= 90):
        raise ErrorApp("POLIGONO_INVALIDO",
                       "Coordenadas fuera de rango. Usa [longitud, latitud] en grados.", 422)
    aviso = []
    if geom.area < 0.98 * box(x0, y0, x1, y1).area:
        aviso.append("El análisis usa el rectángulo que contiene al polígono dibujado.")
    return (x0, y0, x1, y1), aviso


def validar_solicitud(sol: SolicitudConsulta, config: Config) -> tuple[tuple[float, ...], float, list[str]]:
    """Lanza ErrorApp si algo está mal. Devuelve (bbox, área en ha, advertencias)."""
    if sol.fecha_inicio > sol.fecha_fin:
        raise ErrorApp("RANGO_FECHAS_INVALIDO",
                       "La fecha de inicio debe ser anterior o igual a la fecha de fin.", 422)
    if sol.fecha_inicio < config.mes_minimo:
        raise ErrorApp("RANGO_FECHAS_INVALIDO", f"Solo hay datos desde {config.mes_minimo}.", 422)
    if sol.fecha_fin > mes_actual():
        raise ErrorApp("RANGO_FECHAS_INVALIDO", "La fecha de fin no puede estar en el futuro.", 422)
    n_meses = len(meses_entre(sol.fecha_inicio, sol.fecha_fin))
    if n_meses < config.meses_minimos:
        raise ErrorApp(
            "RANGO_INSUFICIENTE",
            f"El rango es de {n_meses} meses; esta instalación pide al menos {config.meses_minimos} "
            "(MESES_MINIMOS en backend/.env).",
            422, [{"meses": n_meses, "minimo": config.meses_minimos}])

    bbox, avisos = zona_a_bbox(sol)
    area_m2 = abs(_GEOD.geometry_area_perimeter(box(*bbox))[0])
    area_ha = area_m2 / 10_000
    if area_ha < config.area_min_ha:
        raise ErrorApp("AREA_INSUFICIENTE", f"El área es muy pequeña (mínimo {config.area_min_ha:g} ha).", 422)
    if area_m2 / 1e6 > config.area_max_km2:
        raise ErrorApp("AREA_EXCEDIDA",
                       f"El área es de {area_m2 / 1e6:,.1f} km² y el máximo es {config.area_max_km2:,.0f} km². "
                       "Dibuja una zona más pequeña.",
                       422, [{"area_km2": round(area_m2 / 1e6, 2), "maximo_km2": config.area_max_km2}])
    return bbox, round(area_ha, 2), avisos
