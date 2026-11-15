"""
Genera api_contract.json (esquemas JSON + endpoints + reglas) y los ejemplos en
contrato/ejemplos/ que el frontend usa como mocks.

Los ejemplos de resultado se producen con el PIPELINE REAL sobre datos SINTÉTICOS
(app/pipeline/sintetico.py): misma forma exacta que una consulta real, sin internet.
Uso:  python scripts/exportar_contrato.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, TypeAdapter  # noqa: E402

from app import contrato as c  # noqa: E402
from app.pipeline import sintetico  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
BBOX = [-96.0897, 19.0554, -96.0787, 19.0654]
ENTRADA = {"bbox": BBOX, "fecha_inicio": "2005-01", "fecha_fin": "2024-12", "nombre": "Marina Punta Tiburón"}

ENDPOINTS = [
    {"metodo": "GET", "ruta": "/api/salud", "respuesta": "Salud"},
    {"metodo": "GET", "ruta": "/api/contrato", "respuesta": "este archivo"},
    {"metodo": "POST", "ruta": "/api/consultas", "cuerpo": "SolicitudConsulta", "respuesta": "Consulta",
     "codigos": {"202": "creada (en_cola)", "200": "reutilizada (ya existía)", "422": "RespuestaError"}},
    {"metodo": "GET", "ruta": "/api/consultas", "respuesta": "Historial"},
    {"metodo": "GET", "ruta": "/api/consultas/{id_consulta}", "respuesta": "Consulta",
     "nota": "Consultar cada 1-2 s hasta estado 'completada' o 'error'. Una consulta fría tarda 6-16 min."},
    {"metodo": "PATCH", "ruta": "/api/consultas/{id_consulta}", "cuerpo": "CambioNombre", "respuesta": "Consulta"},
    {"metodo": "DELETE", "ruta": "/api/consultas/{id_consulta}", "respuesta": "Eliminacion"},
    {"metodo": "GET", "ruta": "/api/consultas/{id_consulta}/descargar", "respuesta": "archivo .json"},
    {"metodo": "GET", "ruta": "/api/pilotos", "respuesta": "ListaPilotos"},
    {"metodo": "GET", "ruta": "/api/pilotos/{id_piloto}", "respuesta": "Consulta"},
    {"metodo": "GET", "ruta": "/api/diagnostico/conectores", "respuesta": "DiagnosticoConectores"},
]

REGLAS = [
    "Todo intercambio es JSON (UTF-8, RFC 8259: sin NaN; los faltantes son null).",
    "Entrada: 'bbox' [oeste, sur, este, norte] en EPSG:4326 (o 'poligono' GeoJSON: se usa su rectángulo).",
    "Fechas de entrada AAAA-MM; mínimo configurable (MESES_MINIMOS, 1 por defecto); área máxima 25 km².",
    "resultado.status: COMPLETED (caso A/B), REJECTED_CONTROLLED (caso C: NO hay manglar), "
    "INSUFFICIENT_DATA (faltan datos: NO afirmar que no hay manglar; mostrar 'sugerencia').",
    "timeline_mensual[].terrestre == null: mes sin escena limpia. NO interpolar.",
    "Caso B: dividir línea de tiempo y tablas con fecha_quiebre y regimen (CON_MANGLAR/TRANSICION/SIN_MANGLAR); "
    "priorizar grupos del Top-3 con compara_antes_y_despues_quiebre == true.",
    "ejes = estado frente al manglar sano; respuesta_al_evento = efecto del evento frente a su propio régimen.",
    "Errores: siempre {error:{codigo,mensaje,detalles}}; 'mensaje' se puede mostrar tal cual.",
    "Atribución obligatoria si se usa GMW: ©Global Mangrove Watch (CC BY 4.0).",
]


def _consulta(escenario: str, nombre: str) -> dict:
    resultado = TypeAdapter(c.ResultadoMango).validate_python(json.loads(sintetico.ejecutar(escenario)))
    return c.Consulta(
        id_consulta=f"c_20261115120000_{escenario.lower()[:6]}", nombre=f"EJEMPLO SINTÉTICO - {nombre}",
        estado="completada", progreso=c.Progreso(porcentaje=100, etapa="Listo"), origen="pipeline",
        creada_en="2026-11-15T12:00:00+00:00", actualizada_en="2026-11-15T12:09:41+00:00",
        parametros=ENTRADA,
        ejecucion=c.ParametrosEjecutados(bbox=BBOX, start_date="2005-01-01", end_date="2024-12-31",
                                         area_ha=128.21),
        resultado=resultado).model_dump(mode="json", exclude={"reutilizada"})


def main():
    modelos = [m for m in vars(c).values() if isinstance(m, type) and issubclass(m, BaseModel)
               and m.__module__ == c.__name__ and m not in (c.Base, c.Libre) and not m.__name__.startswith("_")]
    esquemas = {}
    for m in modelos:
        esq = m.model_json_schema(ref_template="#/esquemas/{model}")
        for nombre, sub in esq.pop("$defs", {}).items():
            esquemas.setdefault(nombre, sub)
        esquemas[m.__name__] = esq
    esq_res = TypeAdapter(c.ResultadoMango).json_schema(ref_template="#/esquemas/{model}")
    esq_res.pop("$defs", None)
    esquemas["ResultadoMango"] = esq_res

    en_proceso = c.Consulta(
        id_consulta="c_20261115120000_proces", nombre="Marina Punta Tiburón", estado="en_proceso",
        progreso=c.Progreso(porcentaje=47, etapa="Procesando escenas Landsat 104/248 (61 limpias)"),
        origen="pipeline", creada_en="2026-11-15T12:00:00+00:00", actualizada_en="2026-11-15T12:04:10+00:00",
        parametros=ENTRADA, ejecucion=c.ParametrosEjecutados(bbox=BBOX, start_date="2005-01-01",
                                                             end_date="2024-12-31", area_ha=128.21),
    ).model_dump(mode="json", exclude={"reutilizada"})
    pequenos = {
        "entrada_consulta": ENTRADA,
        "consulta_en_proceso": en_proceso,
        "error_area_excedida": {"error": {
            "codigo": "AREA_EXCEDIDA",
            "mensaje": "El área es de 5,820.3 km² y el máximo es 25 km². Dibuja una zona más pequeña.",
            "detalles": [{"area_km2": 5820.3, "maximo_km2": 25.0}]}},
        "error_rango_insuficiente": {"error": {
            "codigo": "RANGO_INSUFICIENTE",
            "mensaje": "El rango es de 2 meses; esta instalación pide al menos 3 (MESES_MINIMOS en backend/.env).",
            "detalles": [{"meses": 2, "minimo": 3}]}},
    }
    grandes = {
        "consulta_caso_A": _consulta("A", "manglar conservado"),
        "consulta_caso_B": _consulta("B", "relleno del 45 % del manglar en 2012"),
        "consulta_caso_C": _consulta("cultivo", "zona sin manglar"),
        "consulta_datos_insuficientes": _consulta("pocos", "zona casi siempre nublada"),
    }
    contrato = {
        "nombre": "Contrato de intercambio JSON - MVP Manglares (MANGO v2)",
        "version": c.VERSION_CONTRATO,
        "reglas": REGLAS,
        "endpoints": ENDPOINTS,
        "esquemas": esquemas,
        "ejemplos": {**pequenos, "resultados_completos": {k: f"contrato/ejemplos/{k}.json" for k in grandes}},
    }
    (RAIZ / "api_contract.json").write_text(json.dumps(contrato, ensure_ascii=False, indent=2), encoding="utf-8")
    carpeta = RAIZ / "contrato" / "ejemplos"
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("*.json"):
        viejo.unlink()
    for nombre, datos in {**pequenos, **grandes}.items():
        (carpeta / f"{nombre}.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"api_contract.json v{c.VERSION_CONTRATO}: {len(esquemas)} esquemas | "
          f"{len(pequenos) + len(grandes)} ejemplos en contrato/ejemplos/")


if __name__ == "__main__":
    main()
