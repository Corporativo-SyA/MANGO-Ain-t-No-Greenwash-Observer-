"""Pruebas de punta a punta de la API con el pipeline MANGO real y fuentes sintéticas (sin internet)."""

import json
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.contrato import Consulta
from app.main import crear_app
from app.pipeline import sintetico
from app.pipeline.puente import normalizar_fechas

BBOX = [-96.0897, 19.0554, -96.0787, 19.0654]


def _config(tmp_path) -> Config:
    cfg = Config()
    cfg.carpeta_datos = tmp_path / "datos"
    cfg.abrir_navegador = False
    cfg.gmw_ruta = None
    return cfg


@pytest.fixture()
def cliente(tmp_path):
    with TestClient(crear_app(_config(tmp_path))) as c:
        yield c


@contextmanager
def escenario(nombre):
    with sintetico.fuentes_sinteticas(nombre):
        yield


def esperar(cliente, id_consulta, limite_s=60):
    t0 = time.time()
    while time.time() - t0 < limite_s:
        datos = cliente.get(f"/api/consultas/{id_consulta}").json()
        if datos["estado"] in ("completada", "error"):
            return datos
        time.sleep(0.1)
    raise TimeoutError


def lanzar(cliente, inicio="2005-01", fin="2024-12", **extra):
    cuerpo = {"bbox": BBOX, "fecha_inicio": inicio, "fecha_fin": fin, **extra}
    return cliente.post("/api/consultas", json=cuerpo)


def test_salud(cliente):
    r = cliente.get("/api/salud").json()
    from app.pipeline.mango import PIPELINE_VERSION
    assert r["estado"] == "ok" and r["version_pipeline"] == PIPELINE_VERSION and r["gmw_configurado"] is False


@pytest.mark.parametrize("nombre,status,caso", [
    ("B", "COMPLETED", "CASO_B"), ("A", "COMPLETED", "CASO_A"),
    ("cultivo", "REJECTED_CONTROLLED", "CASO_C"), ("pocos", "INSUFFICIENT_DATA", None),
])
def test_flujo_completo_por_caso(cliente, nombre, status, caso):
    with escenario(nombre):
        r = lanzar(cliente)
        assert r.status_code == 202 and r.json()["estado"] == "en_cola"
        assert r.json()["ejecucion"]["start_date"] == "2005-01-01"
        final = esperar(cliente, r.json()["id_consulta"])
    Consulta.model_validate(final)
    assert final["estado"] == "completada" and final["progreso"]["porcentaje"] == 100
    assert final["resultado"]["status"] == status and final["resultado"]["caso"] == caso


def test_progreso_visible_durante_la_consulta(cliente):
    vistos = []
    with escenario("A"):
        id_ = lanzar(cliente).json()["id_consulta"]
        t0 = time.time()
        while time.time() - t0 < 60:
            d = cliente.get(f"/api/consultas/{id_}").json()
            vistos.append(d["progreso"]["porcentaje"])
            if d["estado"] == "completada":
                break
            time.sleep(0.02)
    assert vistos[-1] == 100 and len(set(vistos)) >= 3


def test_historial_cache_renombrar_descargar_borrar(cliente):
    with escenario("A"):
        id1 = lanzar(cliente).json()["id_consulta"]
        esperar(cliente, id1)
        r2 = lanzar(cliente)                                     # misma zona y fechas -> reutilizada
    assert r2.status_code == 200 and r2.json()["reutilizada"] is True and r2.json()["id_consulta"] == id1

    hist = cliente.get("/api/consultas").json()
    assert hist["total"] == 1 and hist["consultas"][0]["caso"] == "CASO_A"
    assert hist["consultas"][0]["status"] == "COMPLETED"

    assert cliente.patch(f"/api/consultas/{id1}", json={"nombre": "Mandinga"}).json()["nombre"] == "Mandinga"
    d = cliente.get(f"/api/consultas/{id1}/descargar")
    assert "attachment" in d.headers["content-disposition"] and json.loads(d.content)["id_consulta"] == id1
    assert cliente.delete(f"/api/consultas/{id1}").json()["eliminada"] is True
    assert cliente.get(f"/api/consultas/{id1}").status_code == 404


def test_cache_redondea_bbox_a_4_decimales(cliente):
    with escenario("A"):
        id1 = lanzar(cliente).json()["id_consulta"]
        esperar(cliente, id1)
        casi_igual = [v + 0.00001 for v in BBOX]
        r = cliente.post("/api/consultas", json={"bbox": casi_igual, "fecha_inicio": "2005-01",
                                                 "fecha_fin": "2024-12"})
    assert r.status_code == 200 and r.json()["id_consulta"] == id1


def test_poligono_se_analiza_por_su_rectangulo(cliente):
    triangulo = {"type": "Polygon", "coordinates": [[[-96.0897, 19.0554], [-96.0787, 19.0554],
                                                     [-96.0897, 19.0654], [-96.0897, 19.0554]]]}
    with escenario("A"):
        r = cliente.post("/api/consultas", json={"poligono": triangulo, "fecha_inicio": "2005-01",
                                                 "fecha_fin": "2024-12"})
        assert r.status_code == 202
        assert r.json()["ejecucion"]["bbox"] == pytest.approx(BBOX)
        assert any("rectángulo" in a for a in r.json()["advertencias"])
        esperar(cliente, r.json()["id_consulta"])


def test_persistencia_entre_reinicios(tmp_path):
    cfg = _config(tmp_path)
    with escenario("A"), TestClient(crear_app(cfg)) as c:
        id_ = lanzar(c).json()["id_consulta"]
        esperar(c, id_)
    with TestClient(crear_app(cfg)) as c:
        assert c.get(f"/api/consultas/{id_}").json()["resultado"]["caso"] == "CASO_A"


def test_falla_de_red_llega_como_error_claro(cliente, monkeypatch):
    from app.pipeline import mango

    def caida(*a, **k):
        raise mango.FuenteDatosError("NASA POWER", "sin respuesta")
    monkeypatch.setattr(mango, "_download_power", caida)
    final = esperar(cliente, lanzar(cliente).json()["id_consulta"])
    assert final["estado"] == "error" and final["error"]["codigo"] == "FUENTE_EXTERNA_NO_DISPONIBLE"


def test_rango_de_un_mes_se_acepta(cliente):
    """Commit 18: con el histórico completo, un solo mes es una consulta válida."""
    with escenario("A"):
        r = lanzar(cliente, inicio="2018-03", fin="2018-03")
        assert r.status_code == 202
        final = esperar(cliente, r.json()["id_consulta"])
    assert final["estado"] == "completada" and final["resultado"]["status"] == "COMPLETED"
    assert [m["year_month"] for m in final["resultado"]["timeline_mensual"]] == ["2018-03"]


def test_minimo_de_meses_configurable(tmp_path):
    cfg = _config(tmp_path)
    cfg.meses_minimos = 3
    with TestClient(crear_app(cfg)) as c:
        r = c.post("/api/consultas", json={"bbox": BBOX, "fecha_inicio": "2023-01", "fecha_fin": "2023-02"})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "RANGO_INSUFICIENTE"
    assert r.json()["error"]["detalles"] == [{"meses": 2, "minimo": 3}]


@pytest.mark.parametrize("cuerpo,codigo", [
    ({"bbox": BBOX, "fecha_inicio": "2024-12", "fecha_fin": "2022-01"}, "RANGO_FECHAS_INVALIDO"),
    ({"bbox": BBOX, "fecha_inicio": "1970-01", "fecha_fin": "2022-01"}, "RANGO_FECHAS_INVALIDO"),
    ({"bbox": BBOX, "fecha_inicio": "2022-1", "fecha_fin": "2025-01"}, "SOLICITUD_INVALIDA"),
    ({"bbox": [-96.5, 18.8, -95.8, 19.5], "fecha_inicio": "2015-01", "fecha_fin": "2024-12"}, "AREA_EXCEDIDA"),
    ({"bbox": [-96.0, 19.0, -96.1, 19.1], "fecha_inicio": "2015-01", "fecha_fin": "2024-12"}, "BBOX_INVALIDO"),
    ({"bbox": [-96.0, 19.0, -95.99], "fecha_inicio": "2015-01", "fecha_fin": "2024-12"}, "SOLICITUD_INVALIDA"),
    ({"fecha_inicio": "2015-01", "fecha_fin": "2024-12"}, "SOLICITUD_INVALIDA"),
    ({"poligono": {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0.01], [0.01, 0], [0, 0.01], [0, 0]]]},
      "fecha_inicio": "2015-01", "fecha_fin": "2024-12"}, "POLIGONO_INVALIDO"),
])
def test_errores_en_json(cliente, cuerpo, codigo):
    r = cliente.post("/api/consultas", json=cuerpo)
    assert r.status_code == 422 and r.json()["error"]["codigo"] == codigo and r.json()["error"]["mensaje"]


def test_404_en_json(cliente):
    assert cliente.get("/api/consultas/no_existe").json()["error"]["codigo"] == "CONSULTA_NO_ENCONTRADA"
    assert cliente.get("/api/ruta/inventada").json()["error"]["codigo"] == "RUTA_NO_ENCONTRADA"


def test_pilotos_se_sirven_al_instante(cliente, tmp_path):
    cfg = cliente.app.state.config
    cfg.carpeta_pilotos = tmp_path / "pilotos"
    cfg.carpeta_pilotos.mkdir()
    ejemplo = json.loads((cfg.archivo_contrato.parent / "contrato/ejemplos/consulta_caso_B.json").read_text())
    ejemplo["origen"] = "piloto"
    piloto = {"id": "tiburon", "nombre": "Punta Tiburón", "descripcion": "x", "caso": "CASO_B",
              "status": "COMPLETED", "parametros": ejemplo["parametros"]}
    (cfg.carpeta_pilotos / "tiburon.json").write_text(json.dumps({"piloto": piloto, "consulta": ejemplo}))
    assert cliente.get("/api/pilotos").json()["pilotos"][0]["id"] == "tiburon"
    t0 = time.perf_counter()
    r = cliente.get("/api/pilotos/tiburon")
    assert time.perf_counter() - t0 < 0.5 and r.json()["resultado"]["caso"] == "CASO_B"
    assert cliente.get("/api/pilotos/nada").json()["error"]["codigo"] == "PILOTO_NO_ENCONTRADO"


def test_contrato_servido(cliente):
    datos = cliente.get("/api/contrato").json()
    from app.contrato import VERSION_CONTRATO
    assert datos["version"] == VERSION_CONTRATO and "ResultadoMango" in datos["esquemas"]


def test_normalizar_fechas():
    from datetime import date
    assert normalizar_fechas("2005-01", "2024-02") == ("2005-01-01", "2024-02-29")
    assert normalizar_fechas("2024-01", "2026-11", hoy=date(2026, 11, 15)) == ("2024-01-01", "2026-11-14")


def test_consulta_interrumpida_se_marca_error(tmp_path):
    from app.contrato import ParametrosEjecutados, Progreso, SolicitudConsulta
    app = crear_app(_config(tmp_path))
    c = Consulta(id_consulta="c_x", nombre="x", estado="en_proceso", progreso=Progreso(porcentaje=50, etapa="..."),
                 origen="pipeline", creada_en="2026-01-01T00:00:00+00:00", actualizada_en="2026-01-01T00:00:00+00:00",
                 parametros=SolicitudConsulta(bbox=BBOX, fecha_inicio="2020-01", fecha_fin="2024-12"),
                 ejecucion=ParametrosEjecutados(bbox=BBOX, start_date="2020-01-01", end_date="2024-12-31",
                                                area_ha=128.0))
    app.state.almacen.guardar(c)
    with TestClient(app) as cli:
        assert cli.get("/api/consultas/c_x").json()["error"]["codigo"] == "CONSULTA_INTERRUMPIDA"
