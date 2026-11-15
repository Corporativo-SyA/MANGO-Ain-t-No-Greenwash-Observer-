"""Endpoints de consultas: crear, ver progreso/resultado, historial, renombrar, borrar, descargar."""

import json
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.contrato import (
    CambioNombre,
    Consulta,
    Eliminacion,
    Historial,
    ParametrosEjecutados,
    Progreso,
    RespuestaError,
    SolicitudConsulta,
)
from app.errores import ErrorApp
from app.pipeline.mango import PIPELINE_VERSION
from app.pipeline.puente import normalizar_fechas
from app.servicios.validacion import validar_solicitud
from app.utilidades import ahora_iso, huella

router = APIRouter(prefix="/api/consultas", tags=["consultas"])
ERRORES = {404: {"model": RespuestaError}, 409: {"model": RespuestaError}, 422: {"model": RespuestaError}}


def _nuevo_id() -> str:
    return f"c_{datetime.now(UTC):%Y%m%d%H%M%S}_{secrets.token_hex(3)}"


def _obtener_o_404(request: Request, id_consulta: str) -> Consulta:
    consulta = request.app.state.almacen.obtener(id_consulta)
    if consulta is None:
        raise ErrorApp("CONSULTA_NO_ENCONTRADA", "No existe esa consulta.", 404)
    return consulta


@router.post("", response_model=Consulta, status_code=202, responses=ERRORES,
             summary="Lanzar un análisis MANGO (bbox + rango de meses)")
def crear_consulta(solicitud: SolicitudConsulta, request: Request):
    """
    Responde 202 con la consulta `en_cola`. El frontend consulta GET /api/consultas/{id}
    hasta que `estado` sea `completada` o `error` (6-16 min en frío).
    Si la misma zona y fechas ya se analizaron, responde 200 con `reutilizada: true`
    (caché: bbox redondeado a 4 decimales + fechas + versión del pipeline).
    """
    config = request.app.state.config
    almacen = request.app.state.almacen
    bbox, area_ha, avisos = validar_solicitud(solicitud, config)
    inicio, fin = normalizar_fechas(solicitud.fecha_inicio, solicitud.fecha_fin)

    h = huella({"bbox": [round(v, 4) for v in bbox], "inicio": inicio, "fin": fin,
                "pipeline": PIPELINE_VERSION, "gmw": bool(config.gmw_ruta)})
    existente = almacen.buscar_por_huella(h)
    if existente is not None:
        existente.reutilizada = True
        return JSONResponse(status_code=200, content=existente.model_dump(mode="json"))

    ahora = ahora_iso()
    consulta = Consulta(
        id_consulta=_nuevo_id(),
        nombre=solicitud.nombre or f"Consulta {solicitud.fecha_inicio} a {solicitud.fecha_fin}",
        estado="en_cola", progreso=Progreso(porcentaje=0, etapa="En cola"), origen="pipeline",
        creada_en=ahora, actualizada_en=ahora, parametros=solicitud,
        ejecucion=ParametrosEjecutados(bbox=list(bbox), start_date=inicio, end_date=fin, area_ha=area_ha),
        advertencias=avisos)
    almacen.guardar(consulta)
    almacen.registrar_huella(h, consulta.id_consulta)
    request.app.state.trabajos.enviar(consulta.id_consulta)
    return consulta


@router.get("", response_model=Historial, summary="Historial de consultas guardadas")
def listar_consultas(request: Request):
    resumenes = request.app.state.almacen.listar()
    return Historial(total=len(resumenes), consultas=resumenes)


@router.get("/{id_consulta}", response_model=Consulta, responses=ERRORES,
            summary="Estado, progreso y resultado de una consulta")
def obtener_consulta(id_consulta: str, request: Request):
    return _obtener_o_404(request, id_consulta)


@router.patch("/{id_consulta}", response_model=Consulta, responses=ERRORES,
              summary="Cambiar el nombre de una consulta guardada")
def renombrar_consulta(id_consulta: str, cambio: CambioNombre, request: Request):
    consulta = _obtener_o_404(request, id_consulta)
    if consulta.estado in ("en_cola", "en_proceso"):
        raise ErrorApp("CONSULTA_EN_PROCESO", "Espera a que termine la consulta para renombrarla.", 409)
    consulta.nombre = cambio.nombre
    consulta.actualizada_en = ahora_iso()
    request.app.state.almacen.guardar(consulta)
    return consulta


@router.delete("/{id_consulta}", response_model=Eliminacion, responses=ERRORES,
               summary="Borrar una consulta del historial")
def eliminar_consulta(id_consulta: str, request: Request):
    consulta = _obtener_o_404(request, id_consulta)
    if consulta.estado in ("en_cola", "en_proceso"):
        raise ErrorApp("CONSULTA_EN_PROCESO", "No se puede borrar una consulta en proceso.", 409)
    request.app.state.almacen.eliminar(id_consulta)
    return Eliminacion(eliminada=True, id_consulta=id_consulta)


@router.get("/{id_consulta}/descargar", responses=ERRORES,
            summary="Descargar la consulta completa como archivo .json")
def descargar_consulta(id_consulta: str, request: Request):
    consulta = _obtener_o_404(request, id_consulta)
    cuerpo = json.dumps(consulta.model_dump(mode="json", exclude={"reutilizada"}), ensure_ascii=False, indent=2)
    return Response(content=cuerpo, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{id_consulta}.json"'})
