"""
Ejecución en segundo plano de las consultas (patrón de job asíncrono).

POST /api/consultas responde al instante (202); MANGO corre aquí (6-16 min en frío).
El progreso se guarda en el JSON de la consulta; el frontend lo lee con
GET /api/consultas/{id} cada ~1-2 segundos.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import TypeAdapter, ValidationError

from app.config import Config
from app.contrato import ErrorDetalle, Progreso, ResultadoMango
from app.errores import ErrorFuenteExterna
from app.pipeline.puente import ejecutar_experimento
from app.servicios.almacen import Almacen
from app.utilidades import ahora_iso

log = logging.getLogger("manglares.trabajos")
_VALIDADOR = TypeAdapter(ResultadoMango)


class GestorTrabajos:
    def __init__(self, almacen: Almacen, config: Config):
        self.almacen = almacen
        self.config = config
        self.pool = ThreadPoolExecutor(max_workers=config.max_trabajos, thread_name_prefix="consulta")

    def recuperar_interrumpidas(self) -> None:
        """Si la app se cerró con consultas a medias, se marcan como error (se pueden relanzar)."""
        for c in self.almacen.consultas_sin_terminar():
            c.estado = "error"
            c.error = ErrorDetalle(codigo="CONSULTA_INTERRUMPIDA",
                                   mensaje="La aplicación se cerró antes de terminar. Vuelve a lanzarla.")
            c.actualizada_en = ahora_iso()
            self.almacen.guardar(c)

    def enviar(self, id_consulta: str) -> None:
        self.pool.submit(self._procesar, id_consulta)

    def _procesar(self, id_consulta: str) -> None:
        consulta = self.almacen.obtener(id_consulta)
        if consulta is None or consulta.ejecucion is None:
            return
        consulta.estado = "en_proceso"
        consulta.progreso = Progreso(porcentaje=0, etapa="Iniciando")
        consulta.actualizada_en = ahora_iso()
        self.almacen.guardar(consulta)
        ultimo = [0.0, -1]

        def reportar(porcentaje: float, etapa: str) -> None:
            pct = max(0, min(99, int(porcentaje)))
            ahora = time.monotonic()
            if pct == ultimo[1] and ahora - ultimo[0] < 1.0:
                return                      # no reescribir el archivo por cada escena
            ultimo[0], ultimo[1] = ahora, pct
            consulta.progreso = Progreso(porcentaje=pct, etapa=etapa)
            consulta.actualizada_en = ahora_iso()
            self.almacen.guardar(consulta)

        e = consulta.ejecucion
        try:
            bruto = ejecutar_experimento(tuple(e.bbox), consulta.parametros.fecha_inicio,
                                         consulta.parametros.fecha_fin, reportar,
                                         gmw_ruta=self.config.gmw_ruta, hilos=self.config.pipeline_hilos,
                                         cache_dir=(str(self.config.carpeta_datos / "cache_satelital")
                                                    if self.config.cache_satelital else None))
            consulta.resultado = _VALIDADOR.validate_python(bruto)
            consulta.estado = "completada"
            consulta.progreso = Progreso(porcentaje=100, etapa="Listo")
        except ErrorFuenteExterna as ex:
            log.exception("Fuente externa falló")
            consulta.estado = "error"
            consulta.error = ErrorDetalle(
                codigo="FUENTE_EXTERNA_NO_DISPONIBLE",
                mensaje=("No pudimos conectar con los servidores de datos (NASA / Planetary Computer). "
                         "Revisa tu internet e inténtalo de nuevo."), detalles=[str(ex)])
        except ValidationError as ex:
            log.exception("El pipeline devolvió algo fuera del contrato")
            consulta.estado = "error"
            consulta.error = ErrorDetalle(
                codigo="RESULTADO_FUERA_DE_CONTRATO",
                mensaje="El análisis devolvió datos con un formato inesperado.",
                detalles=ex.errors(include_url=False, include_input=False)[:20])
        except Exception as ex:
            log.exception("Error inesperado en la consulta %s", id_consulta)
            consulta.estado = "error"
            consulta.error = ErrorDetalle(codigo="ERROR_INTERNO",
                                          mensaje="Ocurrió un error al analizar la zona.", detalles=[repr(ex)])
        consulta.actualizada_en = ahora_iso()
        self.almacen.guardar(consulta)

    def cerrar(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
