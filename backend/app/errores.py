"""Errores de la aplicación. Todos se devuelven al frontend como JSON (RespuestaError)."""


class ErrorApp(Exception):
    def __init__(self, codigo: str, mensaje: str, estado_http: int = 400, detalles=None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.estado_http = estado_http
        self.detalles = detalles or []


class ErrorFuenteExterna(Exception):
    """Falla de red o de formato en NASA POWER, Planetary Computer o GMW."""

    def __init__(self, fuente: str, mensaje: str):
        super().__init__(f"[{fuente}] {mensaje}")
        self.fuente = fuente
        self.mensaje = mensaje
