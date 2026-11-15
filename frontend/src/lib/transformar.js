// Traduce la salida de MANGO v2 (resultado de una Consulta) al modelo de vista de la línea del tiempo.
import { etiquetaMes, etiquetaFecha, formatoNumero } from './reglas';

export const TIPOS_EVENTO = {
  LLUVIA_TORRENCIAL_CICLON: { titulo: 'Lluvia torrencial / ciclón', icono: '🌧️' },
  NORTE_SEVERO: { titulo: 'Norte severo (frente frío)', icono: '🌬️' },
  VIENTO_SEVERO: { titulo: 'Viento severo', icono: '💨' },
  SEQUIA_ESTACIONAL_SEVERA: { titulo: 'Sequía estacional severa', icono: '🏜️' },
  OLA_CALOR_ALTA_RADIACION: { titulo: 'Ola de calor y alta radiación', icono: '🔥' },
};

export const UNIDADES = {
  'mm/72h': 'mm en 72 h',
  'm/s': 'm/s',
  'SPI-3': 'SPI-3',
};

// Orden y paleta de severidad (tokens de marca).
export const SEVERIDADES = {
  SIN_DATO: { rango: -1, etiqueta: 'SIN IMAGEN', color: '#64748B', fondo: '#F1F5F9', borde: '#CBD5E1',
    gradiente: 'linear-gradient(135deg, #64748B 0%, #94A3B8 100%)' },
  NORMAL: { rango: 0, etiqueta: 'CONSERVADO', color: '#1A824A', fondo: '#ECFDF5', borde: '#A7F3D0',
    gradiente: 'linear-gradient(135deg, #162B4D 0%, #1A824A 100%)' },
  MODERADO: { rango: 1, etiqueta: 'MODERADO', color: '#B7791F', fondo: '#FFFBEB', borde: '#FDE68A',
    gradiente: 'linear-gradient(135deg, #E09F1F 0%, #FBB03B 100%)' },
  ALTO: { rango: 2, etiqueta: 'ALTO', color: '#D95B12', fondo: '#FFF7ED', borde: '#FED7AA',
    gradiente: 'linear-gradient(135deg, #F37021 0%, #FF8C42 100%)' },
  CRITICO: { rango: 3, etiqueta: 'CRÍTICO', color: '#D94136', fondo: '#FEF2F2', borde: '#FECACA',
    gradiente: 'linear-gradient(135deg, #F37021 0%, #D94136 100%)' },
};

export const QUIEBRE_ESTILO = {
  etiqueta: 'QUIEBRE',
  color: '#D94136',
  fondo: '#FEF2F2',
  borde: '#FECACA',
  gradiente: 'linear-gradient(135deg, #7F1D1D 0%, #D94136 100%)',
};

const TIPO_QUIEBRE = {
  CONVERSION_PERSISTENTE: 'Conversión persistente: el manglar no se recuperó',
  PERTURBACION_CON_RECUPERACION: 'Perturbación con recuperación posterior',
  INDETERMINADO_SEGUIMIENTO_CORTO: 'Seguimiento corto: aún no se puede saber si se recupera',
};

const DESTINO = {
  AGUA: 'agua (inundación o dragado)',
  SUELO_DESNUDO_O_CONSTRUIDO: 'suelo desnudo o construido',
  OTRA_VEGETACION: 'otra vegetación (no manglar)',
};

export const ORDEN_EJES = ['Cobertura y Hábitat', 'Suelo', 'Agua', 'Microclima'];
export const ICONO_EJE = { 'Cobertura y Hábitat': '🌿', Suelo: '🟫', Agua: '💧', Microclima: '🌡️' };

export const textoTipoQuiebre = (t) => TIPO_QUIEBRE[t] || t;
export const textoDestino = (d) => DESTINO[d] || d;

export function textoCaso(resultado) {
  if (!resultado) return null;
  if (resultado.status === 'INSUFFICIENT_DATA') return 'DATOS';
  return { CASO_A: 'A', CASO_B: 'B', CASO_C: 'C' }[resultado.caso] || null;
}

/** Severidad máxima entre los 4 ejes (null si no hubo observación). */
export function severidadMaxima(ejes) {
  if (!ejes) return 'SIN_DATO';
  let max = 'NORMAL';
  for (const eje of Object.values(ejes)) {
    if ((SEVERIDADES[eje.severidad]?.rango ?? 0) > SEVERIDADES[max].rango) max = eje.severidad;
  }
  return max;
}

/** Un mes sirve de referencia si tiene extensión, no es de baja confianza ni provisional.
 *  (Consultas hechas con el pipeline 2.0 no traen estos campos: se aceptan tal cual.) */
const mesConfiable = (t) =>
  Boolean(t) && t.fraccion_manglar_en_pie !== null && t.fraccion_manglar_en_pie !== undefined
  && t.confianza !== 'BAJA' && !t.extension_provisional;

const MESES_RELLENO = 2; // un mes sin imagen hereda el último dato confiable de hasta 2 meses atrás

/** Índice por mes con relleno hacia adelante LIMITADO de la fracción de manglar. */
function indexarTimeline(timeline) {
  const porMes = new Map();
  let ultima = null;
  let edad = Infinity;
  for (const m of timeline || []) {
    const t = m.terrestre;
    if (mesConfiable(t)) {
      ultima = { fraccion: t.fraccion_manglar_en_pie, ha: t.ha_manglar_estimada };
      edad = 0;
    } else {
      edad += 1;
    }
    const vigente = ultima && edad <= MESES_RELLENO;
    porMes.set(m.year_month, {
      ...m,
      fraccionRelleno: vigente ? ultima.fraccion : null,
      haRelleno: vigente ? ultima.ha : null,
    });
  }
  return porMes;
}

const mesDe = (fecha) => (fecha ? fecha.slice(0, 7) : null);

function ndviCercano(timeline, desdeMes, hastaMes, haciaAtras) {
  const meses = (timeline || []).filter((m) => m.year_month >= desdeMes && m.year_month <= hastaMes);
  const lista = haciaAtras ? [...meses].reverse() : meses;
  for (const m of lista) {
    const t = m.terrestre;
    if (t?.ndvi !== null && t?.ndvi !== undefined && t.confianza !== 'BAJA') {
      return { ndvi: t.ndvi, mes: m.year_month, condicion: t.indice_condicion ?? null };
    }
  }
  return null;
}

function restarMeses(mes, n) {
  const [y, m] = mes.split('-').map(Number);
  const t = y * 12 + (m - 1) - n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}

function nodoEvento(ep, indice, timeline, fechaQuiebre) {
  const tipo = TIPOS_EVENTO[ep.tipo] || { titulo: ep.tipo, icono: '⚠️' };
  const mes = mesDe(ep.fecha_pico || ep.fecha_inicio);
  const fila = indice.get(mes);
  const ejesRespuesta = ep.respuesta_al_evento?.ejes || ep.ejes;
  const severidad = ep.estado_observacion === 'OBSERVADO' ? severidadMaxima(ejesRespuesta) : 'SIN_DATO';
  const pct = fila?.fraccionRelleno !== null && fila?.fraccionRelleno !== undefined ? fila.fraccionRelleno * 100 : null;

  const mesInicio = mesDe(ep.fecha_inicio);
  const antes = ndviCercano(timeline, restarMeses(mesInicio, 6), restarMeses(mesInicio, 1), true);
  const despues = ep.ventana_busqueda
    ? ndviCercano(timeline, mesDe(ep.ventana_busqueda[0]), mesDe(ep.ventana_busqueda[1]), false)
    : null;

  const sinManglar = ep.regimen_evento === 'SIN_MANGLAR' || (fechaQuiebre && ep.fecha_inicio >= fechaQuiebre);
  const textoRegimen = {
    CON_MANGLAR: 'Manglar en pie',
    TRANSICION: 'Manglar en transición (pérdida en curso)',
    SIN_MANGLAR: 'Zona ya sin manglar (después del quiebre)',
  }[ep.regimen_evento] || 'Régimen sin clasificar';

  return {
    clave: ep.event_id,
    tipoNodo: 'evento',
    fecha: mes,
    etiqueta_mes: etiquetaMes(mes),
    severidad,
    estilo: SEVERIDADES[severidad],
    cobertura_manglar_pct: pct,
    sinManglar,
    titulo: tipo.titulo,
    icono: tipo.icono,
    resumen: ep.diagnostico_global,
    evento: ep,
    variables_nasa: {
      temperatura_max_c: fila?.clima?.temp_aire_max_c ?? null,
      precipitacion_mm: fila?.clima?.precip_mm_mes ?? null,
      viento_max_ms: fila?.clima?.viento_max_ms ?? null,
      radiacion_mj: fila?.clima?.radiacion_media_mj ?? null,
    },
    antes_vs_despues: {
      antes: {
        descripcion: `${textoRegimen} antes del evento${pct !== null ? ` · ${formatoNumero(pct, 1)}% del núcleo de manglar en pie` : ''}.`,
        ndvi: antes?.ndvi ?? null,
        mes: antes?.mes ?? null,
        condicion: antes?.condicion ?? null,
      },
      despues: {
        descripcion: ep.diagnostico_global,
        ndvi: despues?.ndvi ?? null,
        mes: despues?.mes ?? null,
        condicion: despues?.condicion ?? null,
        ejes: ejesRespuesta,
        ejesVsSano: ep.ejes,
      },
    },
  };
}

function nodoQuiebre(resultado, indice, timeline) {
  const nq = resultado.naturaleza_quiebre || {};
  const fecha = resultado.fecha_quiebre;
  const mes = mesDe(fecha);
  const fila = indice.get(mes);
  const antesMes = mesDe(nq.ultima_observacion_intacta) || restarMeses(mes, 1);
  const antes = ndviCercano(timeline, restarMeses(antesMes, 2), antesMes, true);
  const despues = ndviCercano(timeline, mes, restarMeses(mes, -6), false);
  const consecuencias = [
    textoTipoQuiebre(nq.tipo),
    nq.ha_perdidas_estimadas !== undefined && `≈ ${formatoNumero(nq.ha_perdidas_estimadas, 2)} ha de manglar perdidas`,
    nq.destino_cobertura && `La superficie perdida ahora es ${textoDestino(nq.destino_cobertura)}`,
    nq.evento_climatico_asociado
      ? `Coincide con un evento extremo: ${TIPOS_EVENTO[nq.evento_climatico_asociado.tipo]?.titulo || nq.evento_climatico_asociado.tipo} (${etiquetaFecha(nq.evento_climatico_asociado.fecha_inicio)})`
      : 'Ningún evento climático extremo explica la pérdida: apunta a una causa humana o local',
  ].filter(Boolean);

  return {
    clave: `quiebre-${fecha}`,
    tipoNodo: 'quiebre',
    fecha: mes,
    etiqueta_mes: etiquetaMes(mes),
    severidad: 'QUIEBRE',
    estilo: QUIEBRE_ESTILO,
    cobertura_manglar_pct: fila?.fraccionRelleno !== null && fila?.fraccionRelleno !== undefined ? fila.fraccionRelleno * 100 : null,
    titulo: 'Quiebre estructural del manglar',
    icono: '🪓',
    resumen: `Pérdida de extensión confirmada desde el ${etiquetaFecha(fecha)}.`,
    naturaleza: nq,
    variables_nasa: {
      temperatura_max_c: fila?.clima?.temp_aire_max_c ?? null,
      precipitacion_mm: fila?.clima?.precip_mm_mes ?? null,
      viento_max_ms: fila?.clima?.viento_max_ms ?? null,
      radiacion_mj: fila?.clima?.radiacion_media_mj ?? null,
    },
    antes_vs_despues: {
      antes: {
        descripcion: `Última observación con el manglar intacto: ${etiquetaFecha(nq.ultima_observacion_intacta)}.`,
        ndvi: antes?.ndvi ?? null,
        mes: antes?.mes ?? null,
        condicion: antes?.condicion ?? null,
      },
      despues: {
        descripcion: `Primera observación con pérdida: ${etiquetaFecha(nq.primera_observacion_con_perdida || fecha)}.`,
        ndvi: despues?.ndvi ?? null,
        mes: despues?.mes ?? null,
        condicion: despues?.condicion ?? null,
        consecuencias,
      },
    },
  };
}

/** Modelo de vista completo para un resultado COMPLETED. */
export function construirVista(resultado) {
  if (!resultado || resultado.status !== 'COMPLETED') return null;
  const timeline = resultado.timeline_mensual || [];
  const indice = indexarTimeline(timeline);
  const nodos = (resultado.episodios_evaluados || []).map((ep) =>
    nodoEvento(ep, indice, timeline, resultado.fecha_quiebre)
  );
  if (resultado.fecha_quiebre) nodos.push(nodoQuiebre(resultado, indice, timeline));
  nodos.sort((a, b) => (a.fecha === b.fecha ? (a.tipoNodo === 'quiebre' ? -1 : 1) : a.fecha < b.fecha ? -1 : 1));

  const serie = timeline.map((m) => ({
    mes: m.year_month,
    ha: m.terrestre?.ha_manglar_estimada ?? null,
    fraccion: m.terrestre?.fraccion_manglar_en_pie ?? null,
    ndvi: m.terrestre?.ndvi ?? null,
    mvi: m.terrestre?.mvi ?? null,
    mndwi: m.terrestre?.mndwi ?? null,
    condicion: m.terrestre?.indice_condicion ?? null,
    confianza: m.terrestre?.confianza ?? null,
    provisional: Boolean(m.terrestre?.extension_provisional),
    descartadas: m.terrestre?.escenas_descartadas ?? 0,
    regimen: m.terrestre?.regimen ?? null,
    escenas: m.terrestre?.escenas_validas ?? 0,
    precip: m.clima?.precip_mm_mes ?? null,
    tmax: m.clima?.temp_aire_max_c ?? null,
    viento: m.clima?.viento_max_ms ?? null,
  }));

  const haNucleo = resultado.mascaras_espaciales?.mangrove_core_ha ?? null;
  const ultimaHa = [...serie].reverse().find((p) => p.ha !== null)?.ha ?? null;
  const conteoSeveridad = { CRITICO: 0, ALTO: 0, MODERADO: 0, NORMAL: 0, SIN_DATO: 0 };
  for (const n of nodos) if (n.tipoNodo === 'evento') conteoSeveridad[n.severidad] += 1;

  return { nodos, serie, haNucleo, ultimaHa, conteoSeveridad };
}

export const esRelevante = (nodo) =>
  nodo.tipoNodo === 'quiebre' || ['MODERADO', 'ALTO', 'CRITICO'].includes(nodo.severidad);
