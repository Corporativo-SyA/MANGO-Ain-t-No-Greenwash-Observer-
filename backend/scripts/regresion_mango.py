"""
Regresión v1 (pipeline original validado) contra v2 sobre los 4 escenarios REALES del documento
de integración. Requiere internet: ~1-1.5 h en total. Guarda ambos JSON y un informe.

Uso (desde backend/):
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/regresion_mango.py
    GMW_RUTA=RUTA/gmw.zip uv run python scripts/regresion_mango.py tiburon     # solo uno

Criterios de aceptación (v2 no debe romper lo validado):
    mandinga_sur -> CASO_A | tiburon -> CASO_B con quiebre a <= 183 días de 2009-04-17
    veracruz_centro -> CASO_C | cananeia -> CASO_A

Caso real agregado en v2.1 (solo v2): mandinga_zona_usuario, 2000-06 -> 2026-06 (la consulta donde se
detectaron caídas a 0 ha por nube y la sequía de 2011 contada como pérdida). Criterios en
`_criterios_mandinga`: extensión vs. condición, sensibilidad frente a GMW y coherencia del caso.
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import mango  # noqa: E402
from app.pipeline import mango_v1_referencia as v1  # noqa: E402

ESCENARIOS = {
    "mandinga_sur": ((-96.105, 19.045, -96.080, 19.070), "2005-01-01", "2024-12-31", "CASO_A", None),
    "tiburon": ((-96.0897, 19.0554, -96.0787, 19.0654), "2005-01-01", "2024-12-31", "CASO_B", "2009-04-17"),
    "veracruz_centro": ((-96.145, 19.175, -96.125, 19.195), "2020-01-01", "2024-12-31", "CASO_C", None),
    "cananeia": ((-47.935, -25.035, -47.905, -25.005), "2015-01-01", "2024-12-31", "CASO_A", None),
    "mandinga_zona_usuario": ((-96.0869, 19.0531, -96.0709, 19.0656), "2000-06-01", "2026-06-30",
                              "A_O_B", None),
}
SOLO_V2 = {"mandinga_zona_usuario"}
GMW_BAJA_2010_2014_HA = 2.4   # GMW v4.1 en este recuadro (cuadrícula de 1 km): 25.9 -> 23.5 ha
SALIDA = Path(__file__).resolve().parent.parent / "salidas_regresion"


def _resumen(r: dict) -> dict:
    ev = r.get("episodios_evaluados") or []
    obs = [e for e in ev if e.get("estado_observacion") == "OBSERVADO"]
    alerta = [e for e in obs if e.get("ejes") and any(v["severidad"] in ("ALTO", "CRITICO")
                                                      for v in e["ejes"].values())]
    return {"status": r.get("status"), "caso": r.get("caso"), "fecha_quiebre": r.get("fecha_quiebre"),
            "nucleo_px": (r.get("mascaras_espaciales") or {}).get("mangrove_core_pixels"),
            "eventos": len(ev), "observados": len(obs), "con_alerta_alta": len(alerta),
            "naturaleza": (r.get("naturaleza_quiebre") or {}).get("tipo")}


def _criterios_mandinga(r: dict) -> dict[str, bool]:
    """Criterios del caso real (ver docs/PLAN_DE_CAMBIOS.md, commit 13)."""
    tau = r["umbrales_aplicados"]["zona"]["perdida_extension_umbral_fraccion"]
    core_ha = r["mascaras_espaciales"]["mangrove_core_ha"]
    quiebre = r.get("fecha_quiebre")
    meses = {m["year_month"]: m["terrestre"] for m in r["timeline_mensual"]}

    def extension(ym):
        t = meses.get(ym)
        return None if not t else t.get("fraccion_manglar_en_pie")

    bajos = [ym for ym, t in meses.items()
             if t and t.get("fraccion_manglar_en_pie") is not None and not t.get("extension_provisional")
             and t["fraccion_manglar_en_pie"] < 1 - tau and (quiebre is None or ym < quiebre[:7])]
    sequia = [meses.get(ym) for ym in ("2011-02", "2011-03", "2011-04", "2011-05")]
    criterios = {
        "sin_caidas_de_extension_fuera_de_quiebre": not bajos,
        "sequia_2011_en_condicion": any(t and (t.get("indice_condicion") or 0) < -2 for t in sequia),
        "sequia_2011_no_es_extension": all(extension(ym) is None or extension(ym) >= 1 - tau
                                           for ym in ("2011-02", "2011-03", "2011-04", "2011-05")),
        "nube_2023_12_no_es_extension": extension("2023-12") is None or extension("2023-12") >= 1 - tau,
        "sensibilidad_vs_gmw": tau * core_ha < GMW_BAJA_2010_2014_HA,
    }
    if r.get("caso") == "CASO_B":
        ha = (r.get("naturaleza_quiebre") or {}).get("ha_perdidas_estimadas") or 0
        criterios["quiebre_coherente_con_gmw"] = "2011-01" <= quiebre[:7] <= "2015-12" and 0.5 <= ha <= 5
    if bajos:
        print(f"  meses con extensión < 1 - tau fuera de quiebre: {bajos[:12]}")
    return criterios


def main():
    SALIDA.mkdir(exist_ok=True)
    cfg2 = mango.PipelineConfig(gmw_path=os.getenv("GMW_RUTA") or None)
    ids = sys.argv[1:] or list(ESCENARIOS)
    informe, fallas = {}, []
    for id_ in ids:
        bbox, ini, fin, esperado, quiebre_ref = ESCENARIOS[id_]
        print(f"\n== {id_}  {bbox}  {ini} -> {fin}")
        t = time.time()
        r1 = {} if id_ in SOLO_V2 else json.loads(v1.run_mango_pipeline(bbox, ini, fin))
        t1 = time.time() - t
        t = time.time()
        r2 = json.loads(mango.run_mango_pipeline(
            bbox, ini, fin, cfg2, lambda p, x: print(f"\r  v2 [{p:5.1f}%] {x[:80]:<80}", end="", flush=True)))
        t2 = time.time() - t
        (SALIDA / f"{id_}_v1.json").write_text(json.dumps(r1, ensure_ascii=False, indent=1), encoding="utf-8")
        (SALIDA / f"{id_}_v2.json").write_text(json.dumps(r2, ensure_ascii=False, indent=1), encoding="utf-8")
        s1, s2 = _resumen(r1), _resumen(r2)
        print(f"\n  v1 ({t1:.0f}s): {s1}\n  v2 ({t2:.0f}s): {s2}")
        criterios = None
        if esperado == "A_O_B":
            criterios = _criterios_mandinga(r2)
            print(f"\n  criterios: {criterios}")
            ok = s2["caso"] in ("CASO_A", "CASO_B") and all(criterios.values())
        else:
            ok = s2["caso"] == esperado
        if ok and quiebre_ref:
            ok = s2["fecha_quiebre"] is not None and abs(
                (date.fromisoformat(s2["fecha_quiebre"]) - date.fromisoformat(quiebre_ref)).days) <= 183
        if not ok:
            fallas.append(id_)
        detalle = f"{esperado}" + (f" ~{quiebre_ref}" if quiebre_ref else "")
        print("  ACEPTADO" if ok else f"  REVISAR: se esperaba {detalle}")
        informe[id_] = {"esperado": esperado, "quiebre_referencia": quiebre_ref, "v1": s1, "v2": s2,
                        "criterios": criterios, "aceptado": ok, "segundos": {"v1": round(t1), "v2": round(t2)}}
    (SALIDA / "informe_regresion.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    print(f"\nInforme: {SALIDA / 'informe_regresion.json'}")
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
