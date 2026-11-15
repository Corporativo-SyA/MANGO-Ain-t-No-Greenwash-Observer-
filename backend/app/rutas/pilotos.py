"""
Casos piloto precalculados con el pipeline real (scripts/generar_pilotos.py), servidos al
instante desde datos_iniciales/pilotos/*.json para las demostraciones (< 50 ms).
"""

import json

from fastapi import APIRouter, Request

from app.contrato import Consulta, ListaPilotos, Piloto, RespuestaError
from app.errores import ErrorApp

router = APIRouter(prefix="/api/pilotos", tags=["pilotos"])


def _cargar(request: Request) -> dict[str, dict]:
    carpeta = request.app.state.config.carpeta_pilotos
    pilotos = {}
    for ruta in sorted(carpeta.glob("*.json")):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        pilotos[datos["piloto"]["id"]] = datos
    return pilotos


@router.get("", response_model=ListaPilotos, summary="Lista de casos piloto")
def listar_pilotos(request: Request):
    return ListaPilotos(pilotos=[Piloto.model_validate(d["piloto"])
                                 for d in _cargar(request).values()])


@router.get("/{id_piloto}", response_model=Consulta,
            responses={404: {"model": RespuestaError}},
            summary="Resultado completo de un caso piloto (mismo formato que una consulta)")
def obtener_piloto(id_piloto: str, request: Request):
    datos = _cargar(request).get(id_piloto)
    if datos is None:
        raise ErrorApp("PILOTO_NO_ENCONTRADO", "No existe ese caso piloto.", 404)
    return Consulta.model_validate(datos["consulta"])
