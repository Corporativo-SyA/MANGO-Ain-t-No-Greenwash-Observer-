"""Pruebas de los conectores sin internet (respuestas simuladas)."""

from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from app.clientes.nasa_power import ClienteNasaPower
from app.errores import ErrorFuenteExterna


def respuesta(status=200, cuerpo=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = cuerpo
    r.text = str(cuerpo)
    return r


def test_power_limpia_centinela_y_fechas():
    cuerpo = {"properties": {"parameter": {
        "PRECTOTCORR": {"20240101": 1.5, "20240102": -999, "20240103": 0.0},
        "WS10M_MAX": {"20240101": 8.1, "20240102": 7.0, "20240103": -999.0}}},
        "parameters": {"PRECTOTCORR": {"units": "mm/day"}, "WS10M_MAX": {"units": "m/s"}}}
    sesion = MagicMock()
    sesion.get.return_value = respuesta(200, cuerpo)
    r = ClienteNasaPower(sesion=sesion).obtener_diario(19.04, -96.07, date(2024, 1, 1), date(2024, 1, 3))
    assert r["datos"]["PRECTOTCORR"] == {"2024-01-01": 1.5, "2024-01-02": None, "2024-01-03": 0.0}
    assert r["datos"]["WS10M_MAX"]["2024-01-03"] is None
    assert r["unidades"]["WS10M_MAX"] == "m/s"
    params = sesion.get.call_args.kwargs["params"]
    assert params["start"] == "20240101" and params["format"] == "JSON"


def test_power_errores_de_red_y_http():
    sesion = MagicMock()
    sesion.get.side_effect = requests.ConnectionError("sin red")
    with pytest.raises(ErrorFuenteExterna):
        ClienteNasaPower(sesion=sesion).obtener_diario(19, -96, date(2024, 1, 1), date(2024, 1, 2))
    sesion.get.side_effect = None
    sesion.get.return_value = respuesta(503, {"msg": "caído"})
    with pytest.raises(ErrorFuenteExterna):
        ClienteNasaPower(sesion=sesion).obtener_diario(19, -96, date(2024, 1, 1), date(2024, 1, 2))
    sesion.get.return_value = respuesta(200, {"inesperado": True})
    with pytest.raises(ErrorFuenteExterna):
        ClienteNasaPower(sesion=sesion).obtener_diario(19, -96, date(2024, 1, 1), date(2024, 1, 2))


def test_stac_agrupa_por_mes(monkeypatch):
    from app.clientes.landsat_stac import ClienteLandsatSTAC
    cli = ClienteLandsatSTAC()
    monkeypatch.setattr(cli, "buscar_escenas", lambda *a, **k: [
        {"id": "a", "fecha": "2024-01-05"}, {"id": "b", "fecha": "2024-03-09"}])
    agrupado = cli.buscar_por_mes({}, "2024-01", "2024-03")
    assert [len(v) for v in agrupado.values()] == [1, 0, 1]


def test_stac_sin_red_lanza_error_controlado(monkeypatch):
    import pystac_client

    from app.clientes.landsat_stac import ClienteLandsatSTAC
    monkeypatch.setattr(pystac_client.Client, "open",
                        MagicMock(side_effect=ConnectionError("bloqueado")))
    with pytest.raises(ErrorFuenteExterna):
        ClienteLandsatSTAC().buscar_escenas({"type": "Point", "coordinates": [0, 0]}, "2024-01", "2024-02")
