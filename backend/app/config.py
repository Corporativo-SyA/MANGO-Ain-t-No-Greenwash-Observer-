"""Configuración leída de variables de entorno (con valores por defecto sensatos)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION_APP = "0.2.0"
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent


def cargar_env(ruta: Path = RAIZ_PROYECTO / ".env") -> None:
    """Carga backend/.env (CLAVE=valor) sin pisar variables ya definidas. Ver .env.ejemplo."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()


def _lista(valor: str) -> list[str]:
    return [v.strip() for v in valor.split(",") if v.strip()]


def _ruta_opcional(var: str) -> str | None:
    v = os.getenv(var, "").strip()
    return v or None


@dataclass
class Config:
    # Historial y caché (archivos JSON)
    carpeta_datos: Path = field(default_factory=lambda: Path(
        os.getenv("MANGLARES_DATOS", Path.home() / ".manglares_mvp")))
    # Global Mangrove Watch v4.1: zip o carpeta con los mosaicos .tif (recomendado)
    gmw_ruta: str | None = field(default_factory=lambda: _ruta_opcional("GMW_RUTA"))
    # Guardrails del documento de integración (memoria de WarpedVRT y latencia)
    area_max_km2: float = field(default_factory=lambda: float(os.getenv("AREA_MAX_KM2", "25")))
    area_min_ha: float = field(default_factory=lambda: float(os.getenv("AREA_MIN_HA", "1")))
    # Con el histórico completo de la zona (pipeline 2.2) la línea base no sale del rango consultado:
    # cualquier rango de al menos un mes se puede evaluar. Se deja configurable como guardarraíl.
    meses_minimos: int = field(default_factory=lambda: int(os.getenv("MESES_MINIMOS", "1")))
    mes_minimo: str = field(default_factory=lambda: os.getenv("MES_MINIMO", "1985-01"))
    # Una consulta pesada a la vez: el pipeline ya usa varios hilos de descarga
    max_trabajos: int = field(default_factory=lambda: int(os.getenv("MAX_TRABAJOS", "1")))
    pipeline_hilos: int = field(default_factory=lambda: int(os.getenv("PIPELINE_HILOS", "8")))
    # Caché local de escenas Landsat y clima: una zona (o una parte de ella) ya consultada no se vuelve a
    # descargar. Se guarda en <carpeta_datos>/cache_satelital. CACHE_SATELITAL=0 la desactiva.
    cache_satelital: bool = field(default_factory=lambda: os.getenv("CACHE_SATELITAL", "1") == "1")
    # Servidor
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    puerto: int = field(default_factory=lambda: int(os.getenv("PUERTO", "8000")))
    abrir_navegador: bool = field(default_factory=lambda: os.getenv("ABRIR_NAVEGADOR", "1") == "1")
    cors_origenes: list[str] = field(default_factory=lambda: _lista(os.getenv(
        "CORS_ORIGENES", "http://localhost:5173,http://127.0.0.1:5173")))
    carpeta_frontend: Path = field(default_factory=lambda: Path(
        os.getenv("CARPETA_FRONTEND", RAIZ_PROYECTO.parent / "frontend" / "dist")))
    carpeta_pilotos: Path = RAIZ_PROYECTO / "datos_iniciales" / "pilotos"
    archivo_contrato: Path = RAIZ_PROYECTO / "api_contract.json"
