"""
Pruebas científicas del pipeline MANGO v2 (sin internet).

Se sustituyen solo las descargas; todo el análisis es el código real. Cada prueba documenta
una propiedad que el pipeline debe cumplir para ser defendible (ver docs/METODOLOGIA.md).
"""

import json
import warnings
import zipfile
from datetime import date
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from app.pipeline import mango, sintetico
from app.pipeline import mango_v1_referencia as v1

warnings.filterwarnings("ignore", category=RuntimeWarning)


@pytest.fixture(scope="module")
def resultados():
    return {e: json.loads(sintetico.ejecutar(e)) for e in sintetico.ESCENARIOS}


# ------------------------------------------------------------------------------
# Decisiones de caso
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("escenario,status,caso", [
    ("A", "COMPLETED", "CASO_A"),
    ("B", "COMPLETED", "CASO_B"),
    ("B_parcial", "COMPLETED", "CASO_B"),
    ("huracan", "COMPLETED", "CASO_A"),
    ("cultivo", "REJECTED_CONTROLLED", "CASO_C"),
    ("pocos", "INSUFFICIENT_DATA", None),
    ("sur", "COMPLETED", "CASO_A"),
    ("neblina", "COMPLETED", "CASO_A"),
    ("sequia", "COMPLETED", "CASO_A"),
])
def test_caso_por_escenario(resultados, escenario, status, caso):
    r = resultados[escenario]
    assert r["status"] == status and r["caso"] == caso


def test_falta_de_datos_no_se_reporta_como_sin_manglar(resultados):
    r = resultados["pocos"]
    assert r["motivo_codigo"] == "DATOS_INSUFICIENTES" and "sugerencia" in r
    assert resultados["cultivo"]["motivo_codigo"] == "SIN_MANGLAR"


@pytest.mark.parametrize("escenario", ["B", "B_parcial"])
def test_fecha_y_extension_de_la_perdida(resultados, escenario):
    r = resultados[escenario]
    quiebre = date.fromisoformat(r["fecha_quiebre"])
    assert 0 <= (quiebre - sintetico.FECHA_QUIEBRE).days <= 40          # primera pasada tras el relleno
    grid = mango.build_canonical_grid(sintetico.BBOX["norte"])
    cov = sintetico._coberturas(grid, escenario, 11)
    verdad = float((cov["afectado"] & grid.polygon_interior_mask).sum()) * grid.pixel_area_ha
    estimado = r["naturaleza_quiebre"]["ha_perdidas_estimadas"]
    assert abs(estimado - verdad) / verdad < 0.05
    assert r["naturaleza_quiebre"]["tipo"] == "CONVERSION_PERSISTENTE"
    assert r["naturaleza_quiebre"]["destino_cobertura"] == "SUELO_DESNUDO_O_CONSTRUIDO"


def test_relleno_humano_no_se_atribuye_por_azar_a_un_evento(resultados):
    assert resultados["B"]["naturaleza_quiebre"]["evento_climatico_asociado"] is None


def test_regimenes_en_linea_de_tiempo(resultados):
    reg = [t["terrestre"]["regimen"] for t in resultados["B"]["timeline_mensual"] if t["terrestre"]]
    assert reg[0] == "CON_MANGLAR" and reg[-1] == "SIN_MANGLAR"
    assert set(reg) <= {"CON_MANGLAR", "TRANSICION", "SIN_MANGLAR"}
    assert set(t["terrestre"]["regimen"] for t in resultados["A"]["timeline_mensual"]
               if t["terrestre"]) == {"CON_MANGLAR"}


# ------------------------------------------------------------------------------
# Severidad calibrada
# ------------------------------------------------------------------------------
def _terrestre(r, desde="0000-00", hasta="9999-99"):
    return [m["terrestre"] for m in r["timeline_mensual"]
            if m["terrestre"] and desde <= m["year_month"] <= hasta]


def test_extension_separada_de_condicion(resultados):
    """Commit 6: la defoliación con rebrote es CONDICIÓN (z < -2), no hectáreas perdidas; la tala sí es
    extensión, con la proporción correcta."""
    hur = resultados["huracan"]
    assert min(t["fraccion_manglar_en_pie"] for t in _terrestre(hur)
               if t["fraccion_manglar_en_pie"] is not None) >= 0.95
    assert min(t["indice_condicion"] for t in _terrestre(hur, "2012-09", "2013-04")
               if t["indice_condicion"] is not None) < -2
    b = resultados["B"]
    esperado = 1 - b["naturaleza_quiebre"]["ha_perdidas_estimadas"] / b["mascaras_espaciales"]["mangrove_core_ha"]
    post = [t["fraccion_manglar_en_pie"] for t in _terrestre(b, "2012-06")
            if t["fraccion_manglar_en_pie"] is not None]
    assert abs(float(np.median(post)) - esperado) < 0.05
    sano = resultados["A"]
    assert all(t["fraccion_manglar_en_pie"] >= 0.95 for t in _terrestre(sano)
               if t["fraccion_manglar_en_pie"] is not None)
    assert any(t["extension_provisional"] for t in _terrestre(sano, "2023-07"))
    assert not any(t["extension_provisional"] for t in _terrestre(sano, hasta="2022-12"))


def test_confianza_y_descartes_por_mes(resultados):
    """Commit 7: cada mes con extensión trae confianza; meses de 1 escena = MEDIA; nunca sin clasificar."""
    for e in ("A", "B", "huracan"):
        meses = [t for t in _terrestre(resultados[e]) if t["fraccion_manglar_en_pie"] is not None]
        assert all(t["confianza"] in ("ALTA", "MEDIA", "BAJA") for t in meses)
        assert all(t["confianza"] == "MEDIA" for t in meses if t["escenas_validas"] == 1)
        assert all(t["escenas_descartadas"] >= 0 for t in meses)
    total = sum(t["escenas_descartadas"] for t in _terrestre(resultados["huracan"]))
    assert total <= resultados["huracan"]["ingesta_satelital"]["descartadas_por_contaminacion_temporal"]


def test_neblina_no_baja_la_extension(resultados):
    """Commit 14: 6 escenas de nube completa sin bandera QA + 3 de bruma -> la extensión no cae y las nubes
    se descartan por el filtro multitemporal."""
    r = resultados["neblina"]
    tau = r["umbrales_aplicados"]["zona"]["perdida_extension_umbral_fraccion"]
    ext = [t["fraccion_manglar_en_pie"] for t in _terrestre(r) if t["fraccion_manglar_en_pie"] is not None]
    assert min(ext) >= 1 - tau
    descartadas = r["ingesta_satelital"]["descartadas_por_contaminacion_temporal"]
    assert descartadas >= len(sintetico.NEBLINA_FRIA)
    # sin el filtro, la nube hunde la condición del dosel a z ≈ -290; con él queda en su ruido natural
    assert min(t["indice_condicion"] for t in _terrestre(r) if t["indice_condicion"] is not None) > -5
    # los meses cuya única escena era nube quedan sin dato (terrestre = null), no en 0 ha
    assert sum(t["escenas_descartadas"] for t in _terrestre(r)) <= descartadas


def test_sequia_es_condicion_no_extension(resultados):
    """Commit 14: 4 meses de sequía (NDVI -0.25) con recuperación -> Caso A, extensión intacta y
    condición del dosel < -2 durante el evento."""
    r = resultados["sequia"]
    tau = r["umbrales_aplicados"]["zona"]["perdida_extension_umbral_fraccion"]
    assert min(t["fraccion_manglar_en_pie"] for t in _terrestre(r)
               if t["fraccion_manglar_en_pie"] is not None) >= 1 - tau
    assert min(t["indice_condicion"] for t in _terrestre(r, "2011-02", "2011-05")
               if t["indice_condicion"] is not None) < -2
    despues = [t["indice_condicion"] for t in _terrestre(r, "2012-06", "2013-06")
               if t["indice_condicion"] is not None]
    assert abs(float(np.median(despues))) < 1                 # recuperado: vuelve a su variación normal


def _corrida(escenario, inicio, fin):
    return json.loads(sintetico.ejecutar(escenario, inicio, fin))


def test_historico_completo_hace_el_resultado_independiente_del_inicio():
    """Commit 17: línea base, máscara y ruido salen del registro completo de la zona; una ventana de 3
    años o de 3 meses da el MISMO umbral y el MISMO quiebre que la consulta de 20 años."""
    completo = _corrida("B", "2005-01-01", "2024-12-31")
    ventana = _corrida("B", "2010-01-01", "2014-12-31")
    tau = lambda r: r["umbrales_aplicados"]["zona"]["perdida_extension_umbral_fraccion"]  # noqa: E731
    assert ventana["caso"] == "CASO_B" and ventana["fecha_quiebre"] == completo["fecha_quiebre"]
    assert tau(ventana) == tau(completo)
    meses = [m["year_month"] for m in ventana["timeline_mensual"]]
    assert meses[0] == "2010-01" and meses[-1] == "2014-12"          # solo se muestra el rango
    corta = _corrida("A", "2018-03-01", "2018-05-31")
    assert corta["status"] == "COMPLETED" and corta["caso"] == "CASO_A"
    assert tau(corta) == tau(_corrida("A", "2005-01-01", "2024-12-31"))


def test_quiebre_fuera_del_rango():
    """Commit 17: un quiebre ANTES del rango se reporta (con advertencia); uno DESPUÉS no cuenta."""
    despues = _corrida("B", "2015-01-01", "2020-12-31")
    assert despues["caso"] == "CASO_B" and any("ANTES del rango" in a for a in despues["advertencias"])
    antes = _corrida("B", "2005-01-01", "2010-12-31")
    assert antes["caso"] == "CASO_A" and antes["fecha_quiebre"] is None
    vacio = _corrida("A", "2025-06-01", "2025-08-31")           # el registro sintético acaba en 2024
    assert vacio["status"] == "INSUFFICIENT_DATA" and "rango consultado" in vacio["motivo"]


def test_falsas_alarmas_en_manglar_sano(resultados):
    ev = [e for e in resultados["A"]["episodios_evaluados"] if e["estado_observacion"] == "OBSERVADO"]
    con_alerta = [e for e in ev if any(v["severidad"] not in ("NORMAL", "NO_DISPONIBLE")
                                       for v in e["ejes"].values())]
    criticos = [e for e in ev if any(v["severidad"] == "CRITICO" for v in e["ejes"].values())]
    # 4 ejes x 5 % -> ~18.5 % esperado bajo la hipótesis nula
    assert len(con_alerta) / len(ev) < 0.30
    assert len(criticos) / len(ev) < 0.02


def test_huracan_detectado_como_dano_critico(resultados):
    ev = [e for e in resultados["huracan"]["episodios_evaluados"]
          if e["fecha_inicio"].startswith("2012-09") and e["estado_observacion"] == "OBSERVADO"]
    assert ev and ev[0]["ejes"]["Cobertura y Hábitat"]["severidad"] == "CRITICO"


def test_respuesta_propia_del_regimen_degradado(resultados):
    sin = [e for e in resultados["B"]["episodios_evaluados"]
           if e["regimen_evento"] == "SIN_MANGLAR" and e["respuesta_al_evento"]]
    assert sin and all(e["respuesta_al_evento"]["referencia"] == "SIN_MANGLAR" for e in sin)


# ------------------------------------------------------------------------------
# Mejoras demostradas frente a la v1 (mismos datos)
# ------------------------------------------------------------------------------
def _v1_caso(escenario):
    grid = v1.build_canonical_grid(sintetico.BBOX["norte"])
    esc = sintetico.escenas_sinteticas(mango.build_canonical_grid(sintetico.BBOX["norte"]),
                                       "2005-01-01", "2024-12-31", escenario)
    mensual = pd.DataFrame({"year_month": sorted({s.year_month for s in esc}), "precip_mm_mes": 0.0,
                            "viento_max_ms": 0.0, "viento_medio_ms": 0.0, "temp_aire_max_c": 0.0,
                            "radiacion_media_mj": 0.0})
    return v1.analyze_zone_and_regimes(grid, esc, mensual)["caso"]


def test_v1_no_detecta_tala_parcial_y_v2_si(resultados):
    assert _v1_caso("B_parcial") == "CASO_A"
    assert resultados["B_parcial"]["caso"] == "CASO_B"


def test_v1_acepta_cultivo_como_manglar_y_v2_no(resultados):
    assert _v1_caso("cultivo") == "CASO_A"
    assert resultados["cultivo"]["caso"] == "CASO_C"


def test_v2_conserva_lo_que_v1_hacia_bien(resultados):
    assert _v1_caso("B") == resultados["B"]["caso"] == "CASO_B"
    assert _v1_caso("huracan") == resultados["huracan"]["caso"] == "CASO_A"


# ------------------------------------------------------------------------------
# Contrato: compatibilidad con v1 y RFC 8259
# ------------------------------------------------------------------------------
CLAVES_V1 = {"status", "caso", "fecha_quiebre", "estadisticos_tendencia", "mascaras_espaciales",
             "ingesta_climatica", "ingesta_satelital", "resumen_eventos",
             "comparativa_recurrencia_top3", "episodios_evaluados", "timeline_mensual"}
CLAVES_EPISODIO_V1 = {"event_id", "tipo", "fecha_inicio", "fecha_fin", "magnitud_maxima", "unidad",
                      "regimen_evento", "estado_observacion", "ventana_busqueda", "escenas_post_evento",
                      "deltas", "diagnostico_global", "ejes"}


def test_contrato_compatible_con_v1(resultados):
    r = resultados["B"]
    assert CLAVES_V1 <= set(r)
    assert {"mann_kendall_tau", "mann_kendall_p", "sen_slope_por_escena"} <= set(r["estadisticos_tendencia"])
    assert {"total_polygon_pixels", "permanent_water_pixels", "terrestrial_pixels", "mangrove_core_pixels",
            "mangrove_core_share_of_land"} <= set(r["mascaras_espaciales"])
    for e in r["episodios_evaluados"]:
        assert CLAVES_EPISODIO_V1 <= set(e)
        assert not any(k.startswith("_") for k in e)
    assert set(r["episodios_evaluados"][0]["ejes"]) == {"Cobertura y Hábitat", "Suelo", "Agua", "Microclima"}


def test_json_estricto_sin_nan():
    texto = sintetico.ejecutar("B_parcial")
    json.loads(texto, parse_constant=lambda c: pytest.fail(f"constante no válida: {c}"))


def test_ids_de_evento_deterministas():
    a = json.loads(sintetico.ejecutar("A"))
    b = json.loads(sintetico.ejecutar("A"))
    assert [e["event_id"] for e in a["episodios_evaluados"]] == [e["event_id"] for e in b["episodios_evaluados"]]


def test_progreso_monotono_hasta_100():
    vistos = []
    sintetico.ejecutar("A", progreso=lambda p, t: vistos.append(p))
    assert vistos == sorted(vistos) and vistos[-1] == 100.0 and len(vistos) > 50


def test_umbrales_aplicados_documentados(resultados):
    u = resultados["A"]["umbrales_aplicados"]
    assert u["eventos"]["periodo_base"] == "1991-2020"
    assert {"lluvia_3d_mm", "viento_max_ms", "spi_severo"} <= set(u["eventos"])
    assert u["zona"]["ndvi_piso_manglar_zona"] < u["zona"]["ndvi_mediana_manglar_zona"]


# ------------------------------------------------------------------------------
# Componentes estadísticos
# ------------------------------------------------------------------------------
def test_spi_calibrado_en_periodo_base():
    clima = sintetico.clima_sintetico("2020-12-31")
    base = clima.loc["1991":"2020"].index
    spi = mango.compute_spi(clima["PRECTOTCORR"], base, 90).loc["1991":"2020"].dropna()
    assert abs(spi.mean()) < 0.1 and 0.85 < spi.std() < 1.15
    assert 0.03 < float((spi <= -1.5).mean()) < 0.11        # ~6.7 % teórico


def test_percentil_de_calendario_ctx90():
    clima = sintetico.clima_sintetico("2020-12-31")
    base = clima.loc["1991":"2020"].index
    thr = mango._calendar_percentile(clima["T2M_MAX"], base, 90, 15)
    tasa = float((clima["T2M_MAX"].loc[base] > thr.loc[base]).mean())
    assert 0.08 < tasa < 0.12


def test_runs_declustering():
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    m = pd.Series(False, index=idx)
    m.iloc[[2, 3, 5, 12]] = True
    assert len(mango._runs(m, 3)) == 2          # {2,3,5} es un cúmulo; 12 es otro


def test_otsu_bimodal_y_respaldo():
    rng = np.random.default_rng(0)
    bimodal = np.r_[rng.normal(-0.4, 0.05, 500), rng.normal(0.6, 0.05, 200)]
    thr, eta = mango.otsu_threshold(bimodal)
    assert -0.3 < thr < 0.5 and eta > 0.8
    _, eta_u = mango.otsu_threshold(rng.normal(-0.4, 0.05, 500))
    assert eta_u < 0.8


def test_norte_solo_en_el_golfo():
    clima = sintetico.clima_sintetico("2022-12-31")
    golfo, _ = mango.detect_trigger_events(clima, "2005-01-01", "2022-12-31", (-96.08, 19.05))
    brasil, _ = mango.detect_trigger_events(clima, "2005-01-01", "2022-12-31", (-47.9, -25.0))
    assert any(e.tipo == "NORTE_SEVERO" for e in golfo)
    assert not any(e.tipo == "NORTE_SEVERO" for e in brasil)
    for e in golfo:
        if e.tipo == "NORTE_SEVERO":
            assert int(e.fecha_inicio[5:7]) in (10, 11, 12, 1, 2, 3)


def test_least_extreme():
    assert mango.least_extreme([-0.3, -0.1]) == -0.1
    assert mango.least_extreme([0.2, -0.1]) == 0.0


# ------------------------------------------------------------------------------
# Ingesta: reintentos, COG local y GMW desde zip
# ------------------------------------------------------------------------------
def test_power_reintenta_y_falla_controladamente():
    cfg = mango.PipelineConfig(http_retries=2, http_backoff_s=0.0)
    ok = SimpleNamespace(status_code=200, json=lambda: {"properties": {"parameter": {
        "PRECTOTCORR": {"20240101": 1.0}}}})
    caida = SimpleNamespace(status_code=503, text="caído", json=lambda: {})
    sesion = mock.Mock()
    sesion.get.side_effect = [caida, caida, ok]
    with mock.patch.object(mango, "_http_session", return_value=sesion):
        df = mango._download_power(-96, 19, "2024-01-01", "2024-01-01", cfg)
    assert sesion.get.call_count == 3 and df.iloc[0, 0] == 1.0
    sesion.get.side_effect = [caida] * 5
    with mock.patch.object(mango, "_http_session", return_value=sesion), \
         pytest.raises(mango.FuenteDatosError):
        mango._download_power(-96, 19, "2024-01-01", "2024-01-01", cfg)


def test_stac_reintenta_al_abrir_catalogo():
    cfg = mango.PipelineConfig(http_retries=2, http_backoff_s=0.0)
    with mock.patch.object(mango.pystac_client.Client, "open",
                           side_effect=[ConnectionError("x"), ConnectionError("x"), "catalogo"]):
        assert mango._open_catalog(cfg) == "catalogo"


def _tif(path, arr, grid, dtype):
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1,
                       dtype=dtype, crs=grid.crs_wkt, transform=grid.transform) as dst:
        dst.write(arr.astype(dtype), 1)


def _escena_cog(tmp_path, qa=None, lwir_k=300.15, config=None, bbox=None):
    """Escena Landsat sintética escrita como COG locales (rutas reales de rasterio, sin internet)."""
    grid = mango.build_canonical_grid(bbox or sintetico.BBOX["norte"])
    sh = (grid.height, grid.width)
    dn = lambda refl: np.full(sh, (refl + 0.2) / 0.0000275)  # noqa: E731
    if qa is None:
        qa = np.full(sh, 21824)                               # despejado (confianzas bajas)
    lwir = lwir_k if isinstance(lwir_k, np.ndarray) else np.full(sh, lwir_k)
    archivos = {"qa_pixel": (qa, "uint16"), "green": (dn(0.045), "uint16"), "red": (dn(0.03), "uint16"),
                "nir08": (dn(0.30), "uint16"), "swir16": (dn(0.09), "uint16"),
                "lwir11": ((lwir - 149.0) / 0.00341802, "uint16"),
                "qa": (np.full(sh, 120), "int16")}
    assets = {}
    for k, (arr, dt) in archivos.items():
        _tif(tmp_path / f"{k}.tif", arr, grid, dt)
        assets[k] = SimpleNamespace(href=str(tmp_path / f"{k}.tif"))
    item = SimpleNamespace(id="LC08_TEST", datetime=pd.Timestamp("2020-03-01"),
                           properties={"platform": "landsat-8"}, assets=assets)
    with mock.patch.object(mango.planetary_computer, "sign", side_effect=lambda it: it):
        return grid, mango._process_single_stac_item(item, grid, config or mango.DEFAULT_CONFIG)


def test_escena_cog_local_mascara_qa_escala_y_st_qa(tmp_path):
    grid = mango.build_canonical_grid(sintetico.BBOX["norte"])
    qa = np.full((grid.height, grid.width), 21824)
    qa[: grid.height // 4, :] = 21824 | (1 << 3)              # 25 % nube
    _, (estado, obs) = _escena_cog(tmp_path, qa)
    assert estado == "OK" and 0.70 < obs.valid_coverage < 0.80
    assert np.isnan(obs.arrays["ndvi"][0, 0])                 # pixel nublado enmascarado
    assert abs(np.nanmedian(obs.arrays["ndvi"]) - 0.818) < 0.01
    assert abs(np.nanmedian(obs.arrays["mvi"]) - 5.67) < 0.05
    assert abs(np.nanmedian(obs.arrays["lst_c"]) - 27.0) < 0.05
    assert abs(obs.st_uncertainty_k - 1.2) < 1e-6


def test_nube_de_confianza_media_se_enmascara(tmp_path):
    """Commit 1: el bit 3 solo marca nube de ALTA confianza; la de confianza MEDIA (bits 8-9 = 10) pasaba."""
    grid = mango.build_canonical_grid(sintetico.BBOX["norte"])
    qa = np.full((grid.height, grid.width), 21824)            # bits 8-9 = 01 (baja): se acepta
    media = (21824 & ~(0b11 << 8)) | (0b10 << 8)              # bits 8-9 = 10 (media), sin bit 3
    qa[: grid.height // 4, :] = media
    _, (estado, obs) = _escena_cog(tmp_path, qa)
    assert estado == "OK" and 0.70 < obs.valid_coverage < 0.80
    assert np.isnan(obs.arrays["ndvi"][0, 0])
    assert np.isfinite(obs.arrays["ndvi"][grid.height * 3 // 4, grid.width // 2])


def test_nube_fria_invalida_el_pixel_completo(tmp_path):
    """Commit 2: una LST implausible (cima de nube a -30 °C) invalida TODAS las bandas, no solo la LST."""
    grid = mango.build_canonical_grid(sintetico.BBOX["norte"])
    lwir = np.full((grid.height, grid.width), 300.15)
    lwir[: grid.height // 4, :] = 243.15                      # -30 °C sin bandera QA
    _, (estado, obs) = _escena_cog(tmp_path, lwir_k=lwir)
    assert estado == "OK" and 0.70 < obs.valid_coverage < 0.80
    for k in ("ndvi", "ndmi", "mndwi", "mvi", "lst_c"):
        assert np.isnan(obs.arrays[k][0, 0]), k


def _serie_ndvi(valores_por_escena, dias=16):
    """Escenas mínimas (solo NDVI) para probar el filtro multitemporal."""
    rng = np.random.default_rng(3)
    escenas = []
    for i, f in enumerate(valores_por_escena):
        nd = f(rng) if callable(f) else np.full((10, 10), f) + rng.normal(0, 0.01, (10, 10))
        d = date(2015, 1, 1) + pd.Timedelta(days=dias * i)
        escenas.append(mango.SceneObservation(scene_id=f"S{i}", date=d, year_month=d.strftime("%Y-%m"),
                                              platform="landsat-8", valid_coverage=1.0,
                                              arrays={"ndvi": nd.astype(np.float32)}))
    return escenas


def test_filtro_multitemporal_descarta_caida_aislada_y_respeta_perdida_real():
    """Commit 3: una nube omitida (cae y regresa) se descarta; una pérdida parcial sostenida, no."""
    core = np.ones((10, 10), bool)
    nube = _serie_ndvi([0.8] * 6 + [0.05] + [0.8] * 6)
    assert mango._marcar_escenas_contaminadas(nube, core, mango.DEFAULT_CONFIG) == 1
    assert nube[6].contaminada and not any(s.contaminada for s in nube if s.scene_id != "S6")

    def mitad_perdida(rng):                      # 50 % del núcleo pasa a suelo: distribución bimodal
        nd = np.full((10, 10), 0.8) + rng.normal(0, 0.01, (10, 10))
        nd[:5, :] = 0.1
        return nd
    perdida = _serie_ndvi([0.8] * 6 + [mitad_perdida] * 7)
    assert mango._marcar_escenas_contaminadas(perdida, core, mango.DEFAULT_CONFIG) == 0

    lejos = _serie_ndvi([0.8] * 6 + [0.05] + [0.8] * 6, dias=200)   # vecinos a más de 120 días
    assert mango._marcar_escenas_contaminadas(lejos, core, mango.DEFAULT_CONFIG) == 0


def test_ruido_de_extension_no_lo_fija_una_sola_escena_mala():
    """Commit 4: con 19 escenas de base, una escena al 29 % ya no dicta el umbral (antes: P99 ≈ máximo)."""
    base = np.array([0.0, 0.01, 0.02, 0.0, 0.01, 0.03, 0.02, 0.0, 0.01, 0.02,
                     0.01, 0.0, 0.02, 0.01, 0.03, 0.0, 0.01, 0.02, 0.29])
    assert np.quantile(base, 0.99) > 0.24                       # comportamiento anterior (≈ el máximo)
    assert mango._ruido_extension(base, mango.DEFAULT_CONFIG) < 0.06
    assert mango._ruido_extension(np.array([]), mango.DEFAULT_CONFIG) == 0.0


def test_perdida_por_pixel_exige_dos_observaciones():
    """Commit 5: un parche de nube del 30 % que no se repite no suma pérdida; una tala sí, con su fecha."""
    core = np.ones((10, 10), bool)
    umbral = np.full((10, 10), 0.5)

    def parche(rng):
        nd = np.full((10, 10), 0.8)
        nd[:3, :] = 0.05
        return nd

    def tala(rng):
        nd = np.full((10, 10), 0.8)
        nd[:4, :] = 0.1
        return nd
    nube = _serie_ndvi([0.8] * 4 + [parche] + [0.8] * 4)
    mango._fraccion_perdida_confirmada(nube, core, umbral)
    assert max(s.lost_fraction for s in nube) == 0.0
    perdida = _serie_ndvi([0.8] * 4 + [tala] * 5)
    mango._fraccion_perdida_confirmada(perdida, core, umbral)
    assert perdida[3].lost_fraction == 0.0 and abs(perdida[4].lost_fraction - 0.4) < 1e-9


def test_sesgo_entre_sensores_detecta_y_descarta():
    """Commit 10: la regla de decisión arma un sesgo real de OLI y no inventa uno donde no existe."""
    from scripts.sesgo_sensores import sesgo_entre_sensores
    rng = np.random.default_rng(5)

    def serie(sesgo):
        out = []
        for k in range(120):
            d = date(2014, 1, 1) + pd.Timedelta(days=8 * k)
            plat = "landsat-7" if k % 2 == 0 else "landsat-8"
            v = 0.80 + rng.normal(0, 0.01) + (sesgo if plat == "landsat-8" else 0.0)
            out.append(SimpleNamespace(date=d, platform=plat, ndvi=v))
        return out
    assert sesgo_entre_sensores(serie(0.04))["decision"] == "ARMONIZAR"
    assert sesgo_entre_sensores(serie(0.0))["decision"] == "NO_HACE_FALTA"
    assert sesgo_entre_sensores(serie(0.0)[:4])["decision"] == "SIN_DATOS_SUFICIENTES"


def test_cache_de_escenas_reutiliza_y_recorta(tmp_path):
    """Commit 16: la 2.a lectura sale de la caché (sin tocar los COG) y un recuadro DENTRO del primero se
    recorta de la misma escena con valores idénticos."""
    cfg = mango.PipelineConfig(cache_dir=str(tmp_path / "cache"))
    origen = tmp_path / "cog"
    origen.mkdir()
    grid, (estado, obs) = _escena_cog(origen, config=cfg)
    assert estado == "OK" and not obs.desde_cache
    for f in origen.glob("*.tif"):                       # sin los COG: solo puede venir de la caché
        f.unlink()
    item = SimpleNamespace(id="LC08_TEST", datetime=pd.Timestamp("2020-03-01"),
                           properties={"platform": "landsat-8"}, assets={})
    estado2, obs2 = mango._process_single_stac_item(item, grid, cfg)
    assert estado2 == "OK" and obs2.desde_cache
    assert np.allclose(obs.arrays["ndvi"], obs2.arrays["ndvi"], equal_nan=True)
    x0, y0, x1, y1 = sintetico.BBOX["norte"]
    sub = mango.build_canonical_grid((x0 + 0.001, y0 + 0.001, x1 - 0.001, y1 - 0.001))
    estado3, obs3 = mango._process_single_stac_item(item, sub, cfg)
    assert estado3 == "OK" and obs3.desde_cache and obs3.arrays["ndvi"].shape == (sub.height, sub.width)
    assert abs(np.nanmedian(obs3.arrays["ndvi"]) - np.nanmedian(obs.arrays["ndvi"])) < 1e-6


def test_cache_de_clima_por_celda_power(tmp_path):
    """Commit 16: dos recuadros en la misma celda de NASA POWER descargan el clima una sola vez."""
    cfg = mango.PipelineConfig(cache_dir=str(tmp_path))
    idx = pd.date_range("1981-01-01", "2024-12-31", freq="D")
    crudo = pd.DataFrame({"PRECTOTCORR": 1.0, "WS10M": 3.0, "WS10M_MAX": 6.0, "WD10M": 10.0,
                          "T2M_MAX": 30.0, "ALLSKY_SFC_SW_DWN": 18.0}, index=idx)
    with mock.patch.object(mango, "_download_power", return_value=crudo) as descarga:
        mango.fetch_nasa_power_series(-96.08, 19.06, "2010-01-01", "2012-12-31", cfg)
        mango.fetch_nasa_power_series(-96.07, 19.05, "2005-01-01", "2020-12-31", cfg)   # misma celda
        assert descarga.call_count == 1
        mango.fetch_nasa_power_series(-90.0, 21.0, "2010-01-01", "2012-12-31", cfg)     # otra celda
        assert descarga.call_count == 2


def test_gmw_desde_zip(tmp_path):
    grid = mango.build_canonical_grid(sintetico.BBOX["norte"])
    n, res = 500, 1 / 500                                     # mosaico 1° sintético a resolución gruesa
    datos = np.zeros((41, n, n), dtype=np.uint8)
    datos[:, 0:n // 2, :] = 1                                 # manglar en la mitad norte del mosaico
    datos[30:, :, :] = 0                                      # desaparece desde 2015
    tif = tmp_path / "GMW_N20W097_v4112_mng_ext.tif"
    with rasterio.open(tif, "w", driver="GTiff", width=n, height=n, count=41, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(-97.0, 20.0, res, res)) as dst:
        dst.write(datos)
    z = tmp_path / "gmw.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(tif, f"gmw_v4112/{tif.name}")
    mango._GMW_CACHE.clear()
    cfg = mango.PipelineConfig(gmw_path=str(z))
    # el bbox sintético (19.05 N) está en la mitad SUR del mosaico 19-20 N -> sin manglar
    r = mango.load_gmw_extent(grid, 2005, cfg)
    assert r["disponible"] and r["mosaicos"] == 1 and r["mascara"].sum() == 0
    norte = mango.build_canonical_grid((-96.09, 19.80, -96.084, 19.806))
    r2 = mango.load_gmw_extent(norte, 2005, cfg)
    assert r2["mascara"].sum() > 0
    serie = {x["anio"]: x["ha"] for x in r2["serie_anual"]}
    assert serie[2010] > 0 and serie[2020] == 0
    sin_mosaico = mango.load_gmw_extent(mango.build_canonical_grid((10.0, 50.0, 10.006, 50.006)), 2005, cfg)
    assert sin_mosaico["disponible"] and sin_mosaico["mascara"].sum() == 0
