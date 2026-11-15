// Cliente del backend MANGO (contrato v2.0.0, ver backend/api_contract.json).
// Por defecto usa rutas relativas: en producción el backend sirve este build en el mismo origen
// y en desarrollo Vite reenvía /api al backend (vite.config.js). VITE_API_URL lo sobrescribe.
const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export class ErrorApi extends Error {
  constructor(codigo, mensaje, detalles = [], status = 0) {
    super(mensaje);
    this.codigo = codigo;
    this.detalles = detalles;
    this.status = status;
  }
}

async function pedir(ruta, opciones = {}) {
  let respuesta;
  try {
    respuesta = await fetch(`${API}${ruta}`, {
      ...opciones,
      headers: { 'Content-Type': 'application/json', ...(opciones.headers || {}) },
    });
  } catch {
    throw new ErrorApi(
      'BACKEND_APAGADO',
      'No se pudo conectar con el backend local. Abre MANGO con iniciar.bat (o iniciar.sh) y vuelve a intentarlo.'
    );
  }
  let cuerpo = null;
  try {
    cuerpo = await respuesta.json();
  } catch {
    /* respuesta sin JSON */
  }
  if (!respuesta.ok) {
    const e = cuerpo?.error;
    throw new ErrorApi(
      e?.codigo || `HTTP_${respuesta.status}`,
      e?.mensaje || `El servidor respondió con estado ${respuesta.status}.`,
      e?.detalles || [],
      respuesta.status
    );
  }
  return cuerpo;
}

export const api = {
  salud: () => pedir('/api/salud'),
  crearConsulta: (solicitud) => pedir('/api/consultas', { method: 'POST', body: JSON.stringify(solicitud) }),
  obtenerConsulta: (id) => pedir(`/api/consultas/${encodeURIComponent(id)}`),
  historial: () => pedir('/api/consultas'),
  renombrar: (id, nombre) =>
    pedir(`/api/consultas/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ nombre }) }),
  eliminar: (id) => pedir(`/api/consultas/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  pilotos: () => pedir('/api/pilotos'),
  piloto: (id) => pedir(`/api/pilotos/${encodeURIComponent(id)}`),
};

export const enCurso = (consulta) => consulta && (consulta.estado === 'en_cola' || consulta.estado === 'en_proceso');

/** Descarga cualquier objeto como archivo .json (sirve igual para consultas y pilotos). */
export function descargarJson(objeto, nombreArchivo) {
  const blob = new Blob([JSON.stringify(objeto, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombreArchivo.endsWith('.json') ? nombreArchivo : `${nombreArchivo}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
