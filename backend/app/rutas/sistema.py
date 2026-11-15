"""Salud del servidor, contrato JSON y diagnóstico de conectores."""

import json
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Query, Request

from app.clientes.landsat_stac import ClienteLandsatSTAC
from app.clientes.nasa_power import ClienteNasaPower
from app.config import VERSION_APP
from app.contrato import VERSION_CONTRATO, DiagnosticoConectores, EstadoConector, Salud
from app.pipeline import mango

router = APIRouter(prefix="/api", tags=["sistema"])

# Punto de prueba: Sistema Lagunar de Mandinga
PRUEBA_LAT, PRUEBA_LON = 19.0425, -96.075
PRUEBA_POLIGONO = {"type": "Polygon", "coordinates": [[
    [-96.08, 19.04], [-96.07, 19.04], [-96.07, 19.05], [-96.08, 19.05], [-96.08, 19.04]]]}


@router.get("/salud", response_model=Salud, summary="¿Está vivo el backend?")
def salud(request: Request):
    cfg = request.app.state.config
    return Salud(estado="ok", version_app=VERSION_APP, version_contrato=VERSION_CONTRATO,
                 version_pipeline=mango.PIPELINE_VERSION,
                 gmw_configurado=bool(cfg.gmw_ruta and Path(cfg.gmw_ruta).exists()))


@router.get("/contrato", summary="api_contract.json (esquemas + ejemplos)")
def contrato(request: Request):
    return json.loads(request.app.state.config.archivo_contrato.read_text(encoding="utf-8"))


def _medir(funcion):
    t0 = time.perf_counter()
    try:
        detalle = funcion()
        return EstadoConector(ok=True, latencia_ms=int((time.perf_counter() - t0) * 1000),
                              detalle=detalle)
    except Exception as e:
        return EstadoConector(ok=False, latencia_ms=int((time.perf_counter() - t0) * 1000),
                              detalle=str(e)[:400])


@router.get("/diagnostico/conectores", response_model=DiagnosticoConectores,
            summary="Prueba en vivo la conexión con NASA POWER, Planetary Computer y GMW")
def diagnostico(request: Request,
                leer_cog: bool = Query(False, description="También leer un recorte de una banda")):
    def probar_power():
        fin = date.today() - timedelta(days=30)
        r = ClienteNasaPower(timeout_s=20, reintentos=1).obtener_diario(
            PRUEBA_LAT, PRUEBA_LON, fin - timedelta(days=6), fin)
        dias = len(r["datos"].get("PRECTOTCORR", {}))
        return f"OK: {dias} días de lluvia recibidos"

    def probar_stac():
        cliente = ClienteLandsatSTAC(timeout_s=30, reintentos=1)
        hoy = date.today()
        mes_fin = f"{hoy.year:04d}-{hoy.month:02d}"
        mes_ini = f"{hoy.year - 1:04d}-{hoy.month:02d}"
        escenas = cliente.buscar_escenas(PRUEBA_POLIGONO, mes_ini, mes_fin, max_nubes=30, limite=5)
        if not escenas:
            return "Catálogo OK, pero sin escenas en el último año para el punto de prueba"
        texto = f"OK: {len(escenas)} escenas (ej. {escenas[-1]['id']})"
        if leer_cog:
            v = cliente.leer_ventana(escenas[-1]["assets"]["red"], PRUEBA_POLIGONO)
            texto += f"; recorte leído {v['datos'].shape} en {v['crs']}"
        return texto

    def probar_gmw():
        ruta = request.app.state.config.gmw_ruta
        if not ruta:
            raise RuntimeError("GMW_RUTA no está configurada: se usará el respaldo espectral (menos preciso)")
        if not Path(ruta).exists():
            raise RuntimeError(f"GMW_RUTA apunta a un archivo que no existe: {ruta}")
        n = len(mango._gmw_index(ruta))
        if n == 0:
            raise RuntimeError("No se encontraron mosaicos GMW_*.tif en GMW_RUTA")
        return f"OK: {n} mosaicos de 1° disponibles"

    return DiagnosticoConectores(nasa_power=_medir(probar_power),
                                 landsat_stac=_medir(probar_stac),
                                 global_mangrove_watch=_medir(probar_gmw))
