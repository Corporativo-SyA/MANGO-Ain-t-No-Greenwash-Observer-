// Cuadrículas de Global Mangrove Watch (generadas con backend/scripts/capas_gmw_a_json.py).
// Se descargan solo cuando se activa la capa o se pide la referencia, y se guardan en memoria.
export const LIMITES_MEXICO = { oeste: -118.5, sur: 14.5, este: -86.5, norte: 32.8 };
export const ATRIBUCION_GMW = '©Global Mangrove Watch (CC BY 4.0)';

const cache = {};

async function cargar(nombre) {
  if (!cache[nombre]) {
    cache[nombre] = fetch(`/capas/${nombre}`).then((r) => {
      if (!r.ok) throw new Error(`No se encontró la capa ${nombre}`);
      return r.json();
    });
    cache[nombre].catch(() => delete cache[nombre]);
  }
  return cache[nombre];
}

export const cargarMundo = () => cargar('gmw_mundo_2025.json');
export const cargarMexico = () => cargar('gmw_mexico.json');

/** Por mil de manglar de cada celda de México en el año pedido (decodifica los cambios). */
export function porMilDelAnio(mexico, anio) {
  const k = mexico.anios.indexOf(anio);
  const out = new Uint16Array(mexico.pm.length);
  for (let c = 0; c < mexico.pm.length; c += 1) {
    const r = mexico.pm[c];
    let v = r[0];
    for (let j = 1; j < r.length; j += 2) {
      if (r[j] > k) break;
      v = r[j + 1];
    }
    out[c] = v;
  }
  return out;
}

/** Área (ha) de una celda lat/lon en una esfera de radio medio. */
export function haCelda(latMin, celda) {
  const R = 6371.0088;
  const rad = Math.PI / 180;
  return R * R * celda * rad * (Math.sin((latMin + celda) * rad) - Math.sin(latMin * rad)) * 100;
}

/**
 * Hectáreas de manglar GMW dentro del bbox por año, ponderando por el área de intersección con cada
 * celda de 0.01°. SUPONE manglar uniforme dentro de cada celda (~1 km²): solo orientativo.
 */
export function serieGmwAproximada(mexico, [oeste, sur, este, norte]) {
  const e = mexico.escala;
  const cel = mexico.celda;
  const celdas = [];
  for (let c = 0; c < mexico.lat.length; c += 1) {
    const la = mexico.lat[c] / e;
    const lo = mexico.lon[c] / e;
    const fx = Math.max(0, Math.min(lo + cel, este) - Math.max(lo, oeste)) / cel;
    const fy = Math.max(0, Math.min(la + cel, norte) - Math.max(la, sur)) / cel;
    if (fx > 0 && fy > 0) celdas.push({ c, peso: fx * fy * haCelda(la, cel) });
  }
  if (!celdas.length) return [];
  return mexico.anios.map((anio) => {
    const pm = porMilDelAnio(mexico, anio);
    const ha = celdas.reduce((s, { c, peso }) => s + (peso * pm[c]) / 1000, 0);
    return { anio, ha: Math.round(ha * 100) / 100 };
  });
}

export const dentroDeMexico = ([oeste, sur, este, norte]) =>
  oeste >= LIMITES_MEXICO.oeste && este <= LIMITES_MEXICO.este && sur >= LIMITES_MEXICO.sur && norte <= LIMITES_MEXICO.norte;
