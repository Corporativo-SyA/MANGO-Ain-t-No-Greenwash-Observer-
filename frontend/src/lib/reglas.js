// Reglas de negocio espejo de backend/app/servicios/validacion.py.
// El backend sigue siendo la autoridad; aquí solo se avisa antes de enviar.

export const MES_MINIMO = '1985-01';
// La línea base usa el histórico completo de la zona (pipeline 2.2): basta un mes. Espejo de MESES_MINIMOS.
export const MESES_MINIMOS = 1;
export const AREA_MAX_KM2 = 25;
export const AREA_MIN_HA = 1;

export const PATRON_MES = /^\d{4}-(0[1-9]|1[0-2])$/;

const NOMBRES_MES = [
  'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
];

export function mesActual() {
  const hoy = new Date();
  return `${hoy.getUTCFullYear()}-${String(hoy.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** Meses inclusivos entre dos 'AAAA-MM' (2023-01 → 2024-12 = 24). */
export function mesesEntre(inicio, fin) {
  const [y1, m1] = inicio.split('-').map(Number);
  const [y2, m2] = fin.split('-').map(Number);
  return (y2 - y1) * 12 + (m2 - m1) + 1;
}

export function sumarMeses(mes, n) {
  const [y, m] = mes.split('-').map(Number);
  const total = y * 12 + (m - 1) + n;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`;
}

/** 'AAAA-MM' o 'AAAA-MM-DD' → 'Agosto 2021'. */
export function etiquetaMes(fecha) {
  if (!fecha) return '';
  const [y, m] = fecha.split('-').map(Number);
  return `${NOMBRES_MES[m - 1]} ${y}`;
}

export function etiquetaFecha(fecha) {
  if (!fecha) return '';
  const [y, m, d] = fecha.split('-').map(Number);
  return d ? `${d} ${NOMBRES_MES[m - 1].toLowerCase()} ${y}` : etiquetaMes(fecha);
}

/** Devuelve un mensaje de error o '' si el rango es válido. */
export function validarRango(inicio, fin) {
  if (!PATRON_MES.test(inicio) || !PATRON_MES.test(fin)) {
    return 'Usa el formato AAAA-MM (año-mes, ej. 2005-01) en ambas fechas.';
  }
  if (inicio < MES_MINIMO) return `Landsat 5 aporta datos útiles desde ${MES_MINIMO}; elige un inicio posterior.`;
  if (fin > mesActual()) return 'La fecha de fin no puede estar en el futuro.';
  if (inicio > fin) return 'La fecha de inicio no puede ser posterior a la fecha final.';
  const n = mesesEntre(inicio, fin);
  if (n < MESES_MINIMOS) {
    return `El rango cubre ${n} meses; se necesita al menos ${MESES_MINIMOS}.`;
  }
  return '';
}

/** Área geodésica aproximada de un bbox en km² (esfera de radio medio). */
export function areaKm2({ lat_min, lat_max, lon_min, lon_max }) {
  const R = 6371.0088;
  const rad = Math.PI / 180;
  return Math.abs(R * R * (lon_max - lon_min) * rad * (Math.sin(lat_max * rad) - Math.sin(lat_min * rad)));
}

/** Mensaje de error del área o '' si cabe en los límites. */
export function validarArea(coords) {
  if (!coords) return '';
  const km2 = areaKm2(coords);
  if (km2 > AREA_MAX_KM2) {
    return `El área es de ${km2.toLocaleString('es-MX', { maximumFractionDigits: 1 })} km² y el máximo es ${AREA_MAX_KM2} km² (≈ 5 × 5 km). Dibuja una zona más pequeña: el análisis es a 30 m por píxel.`;
  }
  if (km2 * 100 < AREA_MIN_HA) return `El área es muy pequeña (mínimo ${AREA_MIN_HA} ha).`;
  return '';
}

export const bboxDe = (c) => [c.lon_min, c.lat_min, c.lon_max, c.lat_max];

export const coordsDeBbox = ([lon_min, lat_min, lon_max, lat_max], extra = {}) => ({
  tipo_seleccion: 'area',
  lat_min,
  lat_max,
  lon_min,
  lon_max,
  ...extra,
});

export const formatoNumero = (n, dec = 1) =>
  n === null || n === undefined || Number.isNaN(n)
    ? '—'
    : Number(n).toLocaleString('es-MX', { maximumFractionDigits: dec, minimumFractionDigits: 0 });
