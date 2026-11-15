"""
Almacenamiento local en archivos JSON (sin base de datos).

<carpeta_datos>/
  consultas/<id_consulta>.json   -> cada consulta del usuario (historial)
  cache/<huella>.json            -> {"id_consulta": ...}: misma entrada = misma consulta
"""

import json
import os
import tempfile
import threading
from pathlib import Path

from app.contrato import Consulta, ResumenConsulta


class Almacen:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self.dir_consultas = self.raiz / "consultas"
        self.dir_cache = self.raiz / "cache"
        self.dir_consultas.mkdir(parents=True, exist_ok=True)
        self.dir_cache.mkdir(parents=True, exist_ok=True)
        self._candado = threading.Lock()

    # --- utilidades ---------------------------------------------------------
    @staticmethod
    def _escribir_json(ruta: Path, datos: dict) -> None:
        """Escritura atómica: nunca deja un JSON a medias si se corta la luz."""
        fd, tmp = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ruta)

    def _ruta(self, id_consulta: str) -> Path:
        if not id_consulta.replace("_", "").isalnum():   # evita rutas tipo ../../
            raise KeyError(id_consulta)
        return self.dir_consultas / f"{id_consulta}.json"

    # --- consultas ----------------------------------------------------------
    def guardar(self, consulta: Consulta) -> None:
        with self._candado:
            datos = consulta.model_dump(mode="json", exclude={"reutilizada"})
            self._escribir_json(self._ruta(consulta.id_consulta), datos)

    def obtener(self, id_consulta: str) -> Consulta | None:
        try:
            ruta = self._ruta(id_consulta)
        except KeyError:
            return None
        if not ruta.exists():
            return None
        return Consulta.model_validate_json(ruta.read_text(encoding="utf-8"))

    def listar(self) -> list[ResumenConsulta]:
        resumenes = []
        for ruta in self.dir_consultas.glob("*.json"):
            try:
                c = Consulta.model_validate_json(ruta.read_text(encoding="utf-8"))
            except Exception:
                continue   # archivo dañado o de otra versión: se ignora
            resumenes.append(ResumenConsulta(
                id_consulta=c.id_consulta, nombre=c.nombre, estado=c.estado,
                status=c.resultado.status if c.resultado else None,
                caso=c.resultado.caso if c.resultado else None,
                fecha_inicio=c.parametros.fecha_inicio, fecha_fin=c.parametros.fecha_fin,
                creada_en=c.creada_en))
        return sorted(resumenes, key=lambda r: r.creada_en, reverse=True)

    def eliminar(self, id_consulta: str) -> bool:
        with self._candado:
            try:
                ruta = self._ruta(id_consulta)
            except KeyError:
                return False
            if not ruta.exists():
                return False
            ruta.unlink()
            for c in self.dir_cache.glob("*.json"):
                try:
                    if json.loads(c.read_text(encoding="utf-8")).get("id_consulta") == id_consulta:
                        c.unlink()
                except Exception:
                    pass
            return True

    def consultas_sin_terminar(self) -> list[Consulta]:
        pendientes = []
        for ruta in self.dir_consultas.glob("*.json"):
            try:
                c = Consulta.model_validate_json(ruta.read_text(encoding="utf-8"))
            except Exception:
                continue
            if c.estado in ("en_cola", "en_proceso"):
                pendientes.append(c)
        return pendientes

    # --- caché --------------------------------------------------------------
    def buscar_por_huella(self, h: str) -> Consulta | None:
        ruta = self.dir_cache / f"{h}.json"
        if not ruta.exists():
            return None
        id_consulta = json.loads(ruta.read_text(encoding="utf-8")).get("id_consulta", "")
        consulta = self.obtener(id_consulta)
        if consulta is None or consulta.estado == "error":
            ruta.unlink(missing_ok=True)
            return None
        return consulta

    def registrar_huella(self, h: str, id_consulta: str) -> None:
        with self._candado:
            self._escribir_json(self.dir_cache / f"{h}.json", {"id_consulta": id_consulta})
