"""
Precalcula los casos piloto con el PIPELINE REAL (requiere internet; ~10-16 min cada uno)
y los guarda en datos_iniciales/pilotos/*.json para servirlos en < 50 ms en la demo.

Uso (desde backend/):
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/generar_pilotos.py            # todos
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/generar_pilotos.py tiburon    # uno
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import TypeAdapter  # noqa: E402

from app.config import Config  # noqa: E402
from app.contrato import (  # noqa: E402
    Consulta,
    ParametrosEjecutados,
    Piloto,
    Progreso,
    ResultadoMango,
    SolicitudConsulta,
)
from app.pipeline.puente import ejecutar_experimento, normalizar_fechas  # noqa: E402
from app.servicios.validacion import validar_solicitud  # noqa: E402
from app.utilidades import ahora_iso  # noqa: E402

# Escenarios validados del documento de integración (sección 2)
PILOTOS = {
    "mandinga_sur": ("Mandinga Sur, Veracruz", "Manglar conservado del sistema lagunar de Mandinga.",
                     [-96.105, 19.045, -96.080, 19.070], "2005-01", "2024-12"),
    "tiburon": ("Marina Punta Tiburón, Veracruz", "Quiebre estructural por desarrollo de la marina.",
                [-96.0897, 19.0554, -96.0787, 19.0654], "2005-01", "2024-12"),
    "veracruz_centro": ("Centro urbano de Veracruz", "Zona urbana sin manglar (grupo de control).",
                        [-96.145, 19.175, -96.125, 19.195], "2020-01", "2024-12"),
    "cananeia": ("Cananéia-Iguape, Brasil", "Manglar del hemisferio sur (validación global).",
                 [-47.935, -25.035, -47.905, -25.005], "2015-01", "2024-12"),
}


def generar(id_: str, config: Config) -> None:
    nombre, descripcion, bbox, ini, fin = PILOTOS[id_]
    sol = SolicitudConsulta(bbox=bbox, fecha_inicio=ini, fecha_fin=fin, nombre=nombre)
    bbox_t, area, _ = validar_solicitud(sol, config)
    start, end = normalizar_fechas(ini, fin)
    t0 = time.time()

    def avance(p: float, texto: str) -> None:
        print(f"\r  [{p:5.1f}%] {texto[:90]:<90}", end="", flush=True)

    bruto = ejecutar_experimento(bbox_t, ini, fin, avance, gmw_ruta=config.gmw_ruta, hilos=config.pipeline_hilos)
    resultado = TypeAdapter(ResultadoMango).validate_python(bruto)
    ahora = ahora_iso()
    consulta = Consulta(id_consulta=f"piloto_{id_}", nombre=nombre, estado="completada",
                        progreso=Progreso(porcentaje=100, etapa="Listo"), origen="piloto",
                        creada_en=ahora, actualizada_en=ahora, parametros=sol,
                        ejecucion=ParametrosEjecutados(bbox=list(bbox_t), start_date=start, end_date=end,
                                                       area_ha=area),
                        resultado=resultado)
    piloto = Piloto(id=id_, nombre=nombre, descripcion=descripcion, caso=resultado.caso,
                    status=resultado.status, parametros=sol)
    destino = config.carpeta_pilotos / f"{id_}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"piloto": piloto.model_dump(mode="json"),
                                   "consulta": consulta.model_dump(mode="json", exclude={"reutilizada"})},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {resultado.status} / {resultado.caso} en {time.time() - t0:.0f} s  ({destino.name})")


def main():
    config = Config()
    if not config.gmw_ruta:
        print("AVISO: GMW_RUTA no está definida; se usará el respaldo espectral (menos preciso).")
    ids = sys.argv[1:] or list(PILOTOS)
    for id_ in ids:
        if id_ not in PILOTOS:
            raise SystemExit(f"Piloto desconocido: {id_}. Opciones: {', '.join(PILOTOS)}")
        print(f"\n== {id_}: {PILOTOS[id_][0]}")
        generar(id_, config)


if __name__ == "__main__":
    main()
