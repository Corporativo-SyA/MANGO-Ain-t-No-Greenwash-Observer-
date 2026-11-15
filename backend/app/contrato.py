"""
Contrato de intercambio JSON backend <-> frontend (api_contract.json), versión 2.

- El SOBRE (Consulta, Historial, errores...) es del backend.
- El `resultado` es la salida del pipeline MANGO v2 (app/pipeline/mango.py), validada aquí.
  Los bloques que dibuja el frontend son estrictos (extra="forbid"); los bloques de
  transparencia científica (umbrales_aplicados, ingesta_*, referencias) son libres.

Si cambia algo: python scripts/exportar_contrato.py  (la CI verifica que esté sincronizado).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_CONTRATO = "2.1.0"
PATRON_MES = r"^\d{4}-(0[1-9]|1[0-2])$"
PATRON_FECHA = r"^\d{4}-(0[1-9]|1[0-2])-\d{2}$"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Libre(BaseModel):
    model_config = ConfigDict(extra="allow")


# ==========================================================================
# ENTRADA
# ==========================================================================
class GeometriaGeoJSON(Base):
    """Geometría GeoJSON EPSG:4326 [lon, lat]. MANGO analiza su rectángulo envolvente."""
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]


class SolicitudConsulta(Base):
    """Cuerpo de POST /api/consultas. Enviar `bbox` (recomendado) o `poligono`."""
    bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4,
        description="[oeste, sur, este, norte] en grados EPSG:4326",
        examples=[[-96.0897, 19.0554, -96.0787, 19.0654]])
    poligono: GeometriaGeoJSON | None = Field(
        default=None, description="Alternativa: se analiza su rectángulo envolvente")
    fecha_inicio: str = Field(pattern=PATRON_MES, description="Mes inicial AAAA-MM", examples=["2005-01"])
    fecha_fin: str = Field(pattern=PATRON_MES, description="Mes final AAAA-MM", examples=["2024-12"])
    nombre: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _una_zona(self) -> "SolicitudConsulta":
        if (self.bbox is None) == (self.poligono is None):
            raise ValueError("Envía exactamente uno: 'bbox' o 'poligono'.")
        return self


class CambioNombre(Base):
    nombre: str = Field(min_length=1, max_length=120)


# ==========================================================================
# SALIDA DEL PIPELINE MANGO v2
# ==========================================================================
Severidad = Literal["NORMAL", "MODERADO", "ALTO", "CRITICO", "NO_DISPONIBLE"]
Regimen = Literal["CON_MANGLAR", "TRANSICION", "SIN_MANGLAR"]
RegimenEvento = Literal["CON_MANGLAR", "SIN_MANGLAR"]
TipoEvento = Literal["LLUVIA_TORRENCIAL_CICLON", "NORTE_SEVERO", "VIENTO_SEVERO",
                     "SEQUIA_ESTACIONAL_SEVERA", "OLA_CALOR_ALTA_RADIACION"]
Eje = Literal["Cobertura y Hábitat", "Suelo", "Agua", "Microclima"]
FuenteMascara = Literal["GMW", "ESPECTRAL", "ESPECTRAL_LITERATURA"]


class EjeSeveridad(Base):
    severidad: Severidad
    descripcion: str


class RespuestaEvento(Base):
    """Efecto del evento contra la climatología de SU régimen (aísla el evento de la pérdida)."""
    referencia: RegimenEvento
    ejes: dict[Eje, EjeSeveridad]


class Deltas(Base):
    delta_ndvi: float | None = None
    delta_ndmi: float | None = None
    delta_mndwi: float | None = None
    delta_lst_c: float | None = None
    delta_acoplamiento_termico_c: float | None = None


class AnomaliasZ(Base):
    z_ndvi: float | None = None
    z_ndmi: float | None = None
    z_mndwi: float | None = None
    z_acoplamiento_termico: float | None = None


class ProbabilidadNatural(Base):
    """Probabilidad de ver una anomalía así en condiciones normales de la zona (menor = más raro)."""
    ndvi: float | None = None
    ndmi: float | None = None
    mndwi: float | None = None
    lst_minus_t2m: float | None = None


class Episodio(Base):
    event_id: str
    tipo: TipoEvento
    fecha_inicio: str = Field(pattern=PATRON_FECHA)
    fecha_fin: str = Field(pattern=PATRON_FECHA)
    fecha_pico: str = Field(pattern=PATRON_FECHA)
    magnitud_maxima: float
    unidad: str
    umbral_local: float
    regimen_evento: RegimenEvento
    ventana_busqueda: list[str] = Field(min_length=2, max_length=2)
    confundido_con: list[str]
    estado_observacion: Literal["OBSERVADO", "SIN_OBSERVACION_POSTERIOR"]
    escenas_post_evento: int
    radiacion_media_sobrevuelo_mj: float | None = None
    deltas: Deltas | None
    anomalias_z: AnomaliasZ | None
    probabilidad_natural: ProbabilidadNatural | None
    diagnostico_global: str
    ejes: dict[Eje, EjeSeveridad] | None
    respuesta_al_evento: RespuestaEvento | None


class GrupoRecurrencia(Base):
    tipo_amenaza: TipoEvento
    total_episodios_observados: int
    compara_antes_y_despues_quiebre: bool
    ocurrencias: list[Episodio]


class ClimaMes(Base):
    precip_mm_mes: float | None = None
    viento_max_ms: float | None = None
    viento_medio_ms: float | None = None
    temp_aire_max_c: float | None = None
    radiacion_media_mj: float | None = None


class TerrestreMes(Base):
    escenas_validas: int
    regimen: Regimen
    ndvi: float | None = None
    ndmi: float | None = None
    mndwi: float | None = None
    mvi: float | None = None
    lst_c: float | None = None
    fraccion_manglar_en_pie: float | None = Field(
        None, description="Extensión en pie: solo pérdida PERSISTENTE (12-18 meses) por píxel.")
    ha_manglar_estimada: float | None = None
    indice_condicion: float | None = Field(
        None, description="Condición del dosel: z robusto vs. el mes calendario sano; < 0 = estrés.")
    extension_provisional: bool = Field(
        False, description="Sin 18 meses de seguimiento: la extensión aún puede cambiar.")
    confianza: Literal["ALTA", "MEDIA", "BAJA"] | None = Field(
        None, description="ALTA: >=2 escenas que coinciden (dif. <= tau); MEDIA: 1 escena; BAJA: discrepan.")
    escenas_descartadas: int = Field(0, description="Escenas del mes descartadas por el filtro multitemporal.")


class PuntoTimeline(Base):
    year_month: str = Field(pattern=PATRON_MES)
    clima: ClimaMes
    terrestre: TerrestreMes | None = Field(description="null = mes sin escena limpia: NO interpolar")


class EventoAsociado(Base):
    event_id: str
    tipo: TipoEvento
    fecha_inicio: str
    fecha_fin: str


class NaturalezaQuiebre(Base):
    tipo: Literal["CONVERSION_PERSISTENTE", "PERTURBACION_CON_RECUPERACION",
                  "INDETERMINADO_SEGUIMIENTO_CORTO"]
    destino_cobertura: Literal["AGUA", "SUELO_DESNUDO_O_CONSTRUIDO", "OTRA_VEGETACION", "INDETERMINADO"]
    ha_perdidas_estimadas: float
    meses_de_seguimiento: float
    evento_climatico_asociado: EventoAsociado | None
    ultima_observacion_intacta: str
    primera_observacion_con_perdida: str
    criterio_atribucion: str


class EstadisticosTendencia(Base):
    mann_kendall_tau: float | None = None
    mann_kendall_p: float | None = None
    sen_slope_por_escena: float | None = None
    mann_kendall_tau_mensual_desestacionalizado: float | None = None
    mann_kendall_p_mensual_desestacionalizado: float | None = None
    sen_slope_por_anio: float | None = None


class MascarasEspaciales(Base):
    total_polygon_pixels: int
    permanent_water_pixels: int
    terrestrial_pixels: int
    mangrove_core_pixels: int
    mangrove_core_share_of_land: float
    mangrove_core_ha: float
    fuente_mascara: FuenteMascara


class ExtensionAnio(Base):
    anio: int
    ha: float


class ResumenEventos(Base):
    total_detonantes_detectados: int
    total_con_observacion_posterior: int


class _Comun(Base):
    version_pipeline: str
    referencias: dict[str, str]
    extension_gmw_anual: list[ExtensionAnio]
    ingesta_climatica: dict[str, Any]
    ingesta_satelital: dict[str, Any]


class ResultadoCompletado(_Comun):
    """Caso A (manglar conservado) o B (quiebre estructural)."""
    status: Literal["COMPLETED"]
    caso: Literal["CASO_A", "CASO_B"]
    fecha_quiebre: str | None
    naturaleza_quiebre: NaturalezaQuiebre | None
    fuente_mascara: FuenteMascara
    advertencias: list[str]
    estadisticos_tendencia: EstadisticosTendencia
    mascaras_espaciales: MascarasEspaciales
    umbrales_aplicados: dict[str, Any]
    resumen_eventos: ResumenEventos
    comparativa_recurrencia_top3: list[GrupoRecurrencia]
    episodios_evaluados: list[Episodio]
    timeline_mensual: list[PuntoTimeline]


class ResultadoRechazado(_Comun):
    """Caso C: se CONFIRMÓ que el polígono no contiene manglar."""
    status: Literal["REJECTED_CONTROLLED"]
    caso: Literal["CASO_C"]
    motivo_codigo: Literal["SIN_MANGLAR"]
    motivo_rechazo: str
    metricas_admisibilidad: dict[str, Any]
    fuente_mascara: FuenteMascara
    advertencias: list[str]


class ResultadoInsuficiente(_Comun):
    """Faltan observaciones para decidir (NO es Caso C)."""
    status: Literal["INSUFFICIENT_DATA"]
    caso: None
    motivo_codigo: Literal["DATOS_INSUFICIENTES"]
    motivo: str
    sugerencia: str


ResultadoMango = Annotated[ResultadoCompletado | ResultadoRechazado | ResultadoInsuficiente,
                          Field(discriminator="status")]


# ==========================================================================
# SOBRE DE LA CONSULTA (backend)
# ==========================================================================
EstadoConsulta = Literal["en_cola", "en_proceso", "completada", "error"]


class Progreso(Base):
    porcentaje: int = Field(ge=0, le=100)
    etapa: str


class ErrorDetalle(Base):
    codigo: str
    mensaje: str
    detalles: list[Any] = Field(default_factory=list)


class ParametrosEjecutados(Base):
    """Lo que realmente se envió al pipeline (fechas normalizadas, bbox usado)."""
    bbox: list[float]
    start_date: str
    end_date: str
    area_ha: float


class Consulta(Base):
    version_contrato: str = VERSION_CONTRATO
    id_consulta: str
    nombre: str
    estado: EstadoConsulta
    progreso: Progreso
    origen: Literal["pipeline", "piloto"]
    reutilizada: bool = False
    creada_en: str
    actualizada_en: str
    parametros: SolicitudConsulta
    ejecucion: ParametrosEjecutados | None = None
    advertencias: list[str] = Field(default_factory=list)
    resultado: ResultadoMango | None = None
    error: ErrorDetalle | None = None


class ResumenConsulta(Base):
    id_consulta: str
    nombre: str
    estado: EstadoConsulta
    status: str | None = None
    caso: str | None = None
    fecha_inicio: str
    fecha_fin: str
    creada_en: str


class Historial(Base):
    total: int
    consultas: list[ResumenConsulta]


class Piloto(Base):
    id: str
    nombre: str
    descripcion: str
    caso: str | None
    status: str
    parametros: SolicitudConsulta


class ListaPilotos(Base):
    pilotos: list[Piloto]


class Eliminacion(Base):
    eliminada: bool
    id_consulta: str


class Salud(Base):
    estado: Literal["ok"]
    version_app: str
    version_contrato: str
    version_pipeline: str
    gmw_configurado: bool


class EstadoConector(Base):
    ok: bool
    latencia_ms: int | None = None
    detalle: str


class DiagnosticoConectores(Base):
    nasa_power: EstadoConector
    landsat_stac: EstadoConector
    global_mangrove_watch: EstadoConector


class RespuestaError(Base):
    error: ErrorDetalle
