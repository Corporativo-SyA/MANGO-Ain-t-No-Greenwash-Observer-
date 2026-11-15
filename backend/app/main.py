"""
Servidor local del MVP. Arranque:
    python -m app.main            (o los scripts iniciar.bat / iniciar.sh)
Documentación interactiva: http://127.0.0.1:8000/docs
"""

import logging
import threading
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import VERSION_APP, Config
from app.errores import ErrorApp
from app.rutas import consultas, pilotos, sistema
from app.servicios.almacen import Almacen
from app.servicios.trabajos import GestorTrabajos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _error(estado: int, codigo: str, mensaje: str, detalles=None) -> JSONResponse:
    return JSONResponse(status_code=estado, content={
        "error": {"codigo": codigo, "mensaje": mensaje, "detalles": detalles or []}})


def crear_app(config: Config | None = None) -> FastAPI:
    config = config or Config()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        app.state.trabajos.recuperar_interrumpidas()
        yield
        app.state.trabajos.cerrar()

    app = FastAPI(title="MVP Manglares – Backend local", version=VERSION_APP,
                  lifespan=ciclo_de_vida)
    app.state.config = config
    app.state.almacen = Almacen(config.carpeta_datos)
    app.state.trabajos = GestorTrabajos(app.state.almacen, config)

    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origenes,
                       allow_methods=["*"], allow_headers=["*"])

    # ---- Todos los errores salen como JSON con la misma forma ----------------
    @app.exception_handler(ErrorApp)
    async def _error_app(_: Request, e: ErrorApp):
        return _error(e.estado_http, e.codigo, e.mensaje, e.detalles)

    @app.exception_handler(RequestValidationError)
    async def _error_validacion(_: Request, e: RequestValidationError):
        detalles = [{"campo": ".".join(str(p) for p in err["loc"]), "problema": err["msg"]}
                    for err in e.errors()]
        return _error(422, "SOLICITUD_INVALIDA",
                      "Los datos enviados no tienen el formato esperado.", detalles)

    @app.exception_handler(StarletteHTTPException)
    async def _error_http(_: Request, e: StarletteHTTPException):
        codigo = "RUTA_NO_ENCONTRADA" if e.status_code == 404 else f"HTTP_{e.status_code}"
        return _error(e.status_code, codigo, str(e.detail))

    @app.exception_handler(Exception)
    async def _error_inesperado(_: Request, e: Exception):
        logging.getLogger("manglares").exception("Error no controlado")
        return _error(500, "ERROR_INTERNO", "Ocurrió un error inesperado en el servidor.")

    app.include_router(sistema.router)
    app.include_router(consultas.router)
    app.include_router(pilotos.router)

    # ---- Frontend React compilado (npm run build) en "/" --------------------
    if (config.carpeta_frontend / "index.html").exists():
        app.mount("/", StaticFiles(directory=config.carpeta_frontend, html=True), name="frontend")
    return app


app = crear_app()


def main() -> None:
    import uvicorn
    cfg = app.state.config
    url = f"http://{cfg.host}:{cfg.puerto}"
    if cfg.abrir_navegador:
        destino = url if (cfg.carpeta_frontend / "index.html").exists() else f"{url}/docs"
        threading.Timer(1.5, lambda: webbrowser.open(destino)).start()
    uvicorn.run(app, host=cfg.host, port=cfg.puerto)


if __name__ == "__main__":
    main()
