// Serie mensual del manglar: extensión (ha), condición del dosel o índices espectrales.
// La línea se CORTA donde no hay dato: nunca se une un mes con otro a través de un hueco.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { etiquetaMes, formatoNumero } from '../lib/reglas';
import { SEVERIDADES, QUIEBRE_ESTILO } from '../lib/transformar';

const ALTO = 230;
const M = { izq: 52, der: 16, arr: 16, aba: 46 };
const Z_TOPE = 8; // la condición se recorta a ±8σ para que un evento extremo no aplaste el resto

const INDICES = {
  ha: { etiqueta: 'Hectáreas', eje: 'ha de manglar', campo: 'ha', dec: 1 },
  condicion: { etiqueta: 'Condición', eje: 'condición (z)', campo: 'condicion', dec: 1 },
  ndvi: { etiqueta: 'NDVI', eje: 'NDVI', campo: 'ndvi', dec: 2 },
  mvi: { etiqueta: 'MVI', eje: 'MVI', campo: 'mvi', dec: 1 },
  mndwi: { etiqueta: 'MNDWI', eje: 'MNDWI', campo: 'mndwi', dec: 2 },
};

/** Tramos de meses CONSECUTIVOS con dato; un tramo de 1 mes se dibuja como punto. */
function tramos(serie, campo) {
  const out = [];
  let actual = [];
  serie.forEach((p, i) => {
    const v = p[campo];
    if (v === null || v === undefined || Number.isNaN(v)) {
      if (actual.length) out.push(actual);
      actual = [];
      return;
    }
    actual.push({ ...p, i, v });
  });
  if (actual.length) out.push(actual);
  return out;
}

function rangoY(indice, valores, gmw) {
  if (indice === 'ha') return [0, Math.max(1, ...valores, ...gmw) * 1.08];
  if (indice === 'condicion') {
    const l = Math.min(Z_TOPE, Math.max(3, ...valores.map((v) => Math.abs(v)))) * 1.1;
    return [-l, l];
  }
  if (indice === 'mvi') return [0, Math.max(6, ...valores) * 1.08];
  if (!valores.length) return [0, 1];
  const lo = Math.min(...valores);
  const hi = Math.max(...valores);
  const pad = (hi - lo || 0.1) * 0.1;
  return [Math.max(-1, lo - pad), Math.min(1, hi + pad)];
}

export default function GraficaManglar({ serie, gmw, gmwAproximado, fechaQuiebre, nodos, seleccionado, onSeleccionar }) {
  const ref = useRef(null);
  const [ancho, setAncho] = useState(800);
  const [hover, setHover] = useState(null);
  const [indice, setIndice] = useState('ha');

  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setAncho(Math.max(320, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const conf = INDICES[indice];
  const datos = useMemo(() => {
    const n = serie.length;
    const recortar = (v) => (indice === 'condicion' ? Math.max(-Z_TOPE, Math.min(Z_TOPE, v)) : v);
    const segs = tramos(serie, conf.campo).map((t) => t.map((p) => ({ ...p, v: recortar(p.v) })));
    const anios = new Set(serie.map((p) => p.mes.slice(0, 4)));
    const gmwRango = indice === 'ha' ? (gmw || []).filter((g) => anios.has(String(g.anio))) : [];
    const [minY, maxY] = rangoY(indice, segs.flat().map((p) => p.v), gmwRango.map((g) => g.ha));
    return { n, segs, gmwRango, minY, maxY };
  }, [serie, gmw, indice, conf.campo]);

  if (!serie.length) return null;
  const { n, segs, gmwRango, minY, maxY } = datos;
  const w = ancho - M.izq - M.der;
  const h = ALTO - M.arr - M.aba;
  const x = (i) => M.izq + (n <= 1 ? w / 2 : (i / (n - 1)) * w);
  const y = (v) => M.arr + h - ((v - minY) / (maxY - minY)) * h;
  const base = indice === 'ha' ? y(0) : null;
  const indiceMes = new Map(serie.map((p, i) => [p.mes, i]));
  const linea = (seg) => seg.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');

  const caminoGmw = gmwRango
    .map((g) => {
      const i = indiceMes.get(`${g.anio}-07`) ?? indiceMes.get(`${g.anio}-01`);
      return i === undefined ? null : `${x(i).toFixed(1)},${y(g.ha).toFixed(1)}`;
    })
    .filter(Boolean)
    .map((pt, k) => `${k === 0 ? 'M' : 'L'}${pt}`)
    .join(' ');

  const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => minY + f * (maxY - minY));
  const pasoAnios = Math.max(1, Math.ceil(n / 12 / Math.max(1, Math.floor(w / 70))));
  const ticksX = serie
    .map((p, i) => ({ ...p, i }))
    .filter((p) => p.mes.endsWith('-01') && Number(p.mes.slice(0, 4)) % pasoAnios === 0);
  const iQuiebre = fechaQuiebre ? indiceMes.get(fechaQuiebre.slice(0, 7)) : undefined;
  const eventos = nodos.filter((nd) => nd.tipoNodo === 'evento' && indiceMes.has(nd.fecha));
  const hayBaja = serie.some((p) => p.confianza === 'BAJA');
  const hayProvisional = indice === 'ha' && serie.some((p) => p.provisional && p.ha !== null);
  const hayDescartes = serie.some((p) => p.descartadas > 0);

  const moverRaton = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - rect.left - M.izq) / w) * (n - 1));
    setHover(i < 0 || i >= n ? null : { i, p: serie[i] });
  };

  return (
    <div ref={ref} style={{ position: 'relative', width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px', marginBottom: '6px' }}>
        <div className="mango-segmentado pequeno" role="tablist" aria-label="Índice a graficar">
          {Object.entries(INDICES).map(([k, v]) => (
            <button key={k} type="button" role="tab" aria-selected={indice === k}
              className={indice === k ? (k === 'ha' ? 'activo-verde' : 'activo-navy') : ''}
              onClick={() => setIndice(k)}>
              {v.etiqueta}
            </button>
          ))}
        </div>
        <span style={{ fontSize: '11.5px', color: '#64748B' }}>
          {indice === 'ha' && 'Solo pérdida persistente (12–18 meses). El estrés temporal va en Condición.'}
          {indice === 'condicion' && 'z robusto vs. el mismo mes en años sanos; < −2 = estrés fuerte.'}
          {indice === 'mvi' && 'Índice de manglar (Baloloy 2020); umbral publicado 4.6.'}
          {indice === 'ndvi' && 'Vigor del dosel del núcleo de manglar (mediana).'}
          {indice === 'mndwi' && 'Agua superficial; > 0 sugiere inundación.'}
        </span>
      </div>

      <svg width={ancho} height={ALTO} role="img" aria-label={`${conf.etiqueta} del manglar por mes`}
        onMouseMove={moverRaton} onMouseLeave={() => setHover(null)} style={{ display: 'block' }}>
        <defs>
          <linearGradient id="relleno-manglar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1A824A" stopOpacity="0.32" />
            <stop offset="100%" stopColor="#1A824A" stopOpacity="0.03" />
          </linearGradient>
        </defs>

        {indice === 'condicion' && (
          <rect x={M.izq} width={w} y={y(2)} height={Math.max(0, y(-2) - y(2))} fill="#1A824A" opacity="0.07" />
        )}
        {ticksY.map((v) => (
          <g key={v}>
            <line x1={M.izq} x2={M.izq + w} y1={y(v)} y2={y(v)} stroke="#E2E8F0" />
            <text x={M.izq - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="#64748B">{formatoNumero(v, conf.dec)}</text>
          </g>
        ))}
        {indice === 'condicion' && <line x1={M.izq} x2={M.izq + w} y1={y(0)} y2={y(0)} stroke="#162B4D" strokeOpacity="0.4" />}
        {indice === 'mvi' && (
          <>
            <line x1={M.izq} x2={M.izq + w} y1={y(4.6)} y2={y(4.6)} stroke="#F37021" strokeDasharray="5 4" />
            <text x={M.izq + w - 4} y={y(4.6) - 4} textAnchor="end" fontSize="10.5" fill="#D95B12">4.6</text>
          </>
        )}
        <text x={14} y={M.arr + h / 2} transform={`rotate(-90 14 ${M.arr + h / 2})`} textAnchor="middle" fontSize="11" fill="#475569">
          {conf.eje}
        </text>
        {ticksX.map((t) => (
          <g key={t.mes}>
            <line x1={x(t.i)} x2={x(t.i)} y1={M.arr + h} y2={M.arr + h + 4} stroke="#94A3B8" />
            <text x={x(t.i)} y={M.arr + h + 16} textAnchor="middle" fontSize="11" fill="#64748B">{t.mes.slice(0, 4)}</text>
          </g>
        ))}

        {iQuiebre !== undefined && (
          <>
            <rect x={x(iQuiebre)} y={M.arr} width={Math.max(0, M.izq + w - x(iQuiebre))} height={h} fill="#D94136" opacity="0.05" />
            <line x1={x(iQuiebre)} x2={x(iQuiebre)} y1={M.arr} y2={M.arr + h} stroke={QUIEBRE_ESTILO.color} strokeWidth="2" strokeDasharray="5 4" />
            <text x={x(iQuiebre) + 6} y={M.arr + 12} fontSize="11" fontWeight="700" fill={QUIEBRE_ESTILO.color}>Quiebre</text>
          </>
        )}

        {base !== null && segs.filter((s) => s.length > 1).map((s) => (
          <path key={`a${s[0].i}`} d={`${linea(s)} L${x(s[s.length - 1].i).toFixed(1)},${base} L${x(s[0].i).toFixed(1)},${base} Z`} fill="url(#relleno-manglar)" />
        ))}
        {caminoGmw && <path d={caminoGmw} fill="none" stroke="#162B4D" strokeWidth="1.5" strokeDasharray="4 4" opacity="0.6" />}
        {segs.filter((s) => s.length > 1).flatMap((s) => {
          // dentro de un tramo, los meses provisionales (sin 18 meses de seguimiento) van punteados
          const prov = (p) => indice === 'ha' && p.provisional;
          const partes = [];
          let actual = { prov: prov(s[0]), pts: [s[0]] };
          for (let k = 1; k < s.length; k += 1) {
            if (prov(s[k]) !== actual.prov) {
              partes.push(actual);
              actual = { prov: prov(s[k]), pts: [s[k - 1], s[k]] };
            } else {
              actual.pts.push(s[k]);
            }
          }
          partes.push(actual);
          return partes.filter((pt) => pt.pts.length > 1).map((pt) => (
            <path key={`l${pt.pts[0].i}-${pt.pts[pt.pts.length - 1].i}`} d={linea(pt.pts)} fill="none"
              stroke="#1A824A" strokeWidth="2" strokeDasharray={pt.prov ? '4 3' : undefined} />
          ));
        })}
        {segs.flat().map((p) => {
          const aislado = segs.some((s) => s.length === 1 && s[0].i === p.i);
          const baja = p.confianza === 'BAJA';
          if (!aislado && !baja) return null;
          return (
            <circle key={`p${p.i}`} cx={x(p.i)} cy={y(p.v)} r={baja ? 3.5 : 3}
              fill={baja ? '#FFFFFF' : '#1A824A'} stroke="#1A824A" strokeWidth={baja ? 1.6 : 0} />
          );
        })}

        {eventos.map((nd) => {
          const est = SEVERIDADES[nd.severidad] || SEVERIDADES.NORMAL;
          const sel = seleccionado === nd.clave;
          const relevante = nd.severidad !== 'NORMAL' && nd.severidad !== 'SIN_DATO';
          return (
            <rect key={nd.clave} x={x(indiceMes.get(nd.fecha)) - (sel ? 3 : 1.5)} y={M.arr + h + 22}
              width={sel ? 6 : 3} height={relevante ? 14 : 7} rx="1" fill={est.color}
              opacity={relevante || sel ? 1 : 0.35} style={{ cursor: 'pointer' }} onClick={() => onSeleccionar?.(nd)}>
              <title>{`${nd.titulo} · ${nd.etiqueta_mes} · ${est.etiqueta}`}</title>
            </rect>
          );
        })}

        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.i)} x2={x(hover.i)} y1={M.arr} y2={M.arr + h} stroke="#162B4D" strokeOpacity="0.35" />
            {hover.p[conf.campo] !== null && hover.p[conf.campo] !== undefined && (
              <circle cx={x(hover.i)} cy={y(indice === 'condicion' ? Math.max(-Z_TOPE, Math.min(Z_TOPE, hover.p.condicion)) : hover.p[conf.campo])}
                r="4" fill="#1A824A" stroke="#fff" strokeWidth="2" />
            )}
          </g>
        )}
      </svg>

      {hover && (
        <div className="mango-tooltip" style={{ left: Math.min(Math.max(x(hover.i) + 12, 0), ancho - 210), top: 40 }}>
          <strong>{etiquetaMes(hover.p.mes)}</strong>
          {hover.p.escenas === 0 && hover.p.ha === null ? (
            <div>Sin imagen limpia este mes{hover.p.descartadas ? ` (${hover.p.descartadas} descartada/s por nube)` : ''}</div>
          ) : (
            <>
              <div>Manglar: {hover.p.ha !== null ? `${formatoNumero(hover.p.ha, 2)} ha (${formatoNumero(hover.p.fraccion * 100, 1)}%)` : '—'}{hover.p.provisional ? ' · provisional' : ''}</div>
              <div>Condición: {hover.p.condicion !== null ? `z ${formatoNumero(hover.p.condicion, 1)}` : '—'}</div>
              <div>NDVI {formatoNumero(hover.p.ndvi, 2)} · MVI {formatoNumero(hover.p.mvi, 1)} · MNDWI {formatoNumero(hover.p.mndwi, 2)}</div>
              <div>Escenas: {hover.p.escenas}{hover.p.descartadas ? ` (+${hover.p.descartadas} descartadas)` : ''} · confianza {hover.p.confianza ? hover.p.confianza.toLowerCase() : '—'}</div>
            </>
          )}
          <div>Lluvia {formatoNumero(hover.p.precip, 0)} mm · T máx {formatoNumero(hover.p.tmax, 1)} °C · viento {formatoNumero(hover.p.viento, 1)} m/s</div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '12px', color: '#475569', marginTop: '4px' }}>
        <span><span className="mango-leyenda" style={{ background: '#1A824A' }} /> {indice === 'ha' ? 'Manglar en pie (Landsat, 30 m)' : conf.etiqueta}</span>
        {caminoGmw && (
          <span title={gmwAproximado ? 'Ponderado por área de intersección con celdas de ~1 km; supone manglar uniforme dentro de cada celda.' : undefined}>
            <span className="mango-leyenda guion" />{' '}
            {gmwAproximado ? 'GMW aprox. (celdas de 1 km, orientativo)' : 'Global Mangrove Watch (anual)'}
          </span>
        )}
        {iQuiebre !== undefined && <span><span className="mango-leyenda" style={{ background: '#D94136' }} /> Quiebre estructural</span>}
        <span>Huecos = meses sin imagen limpia</span>
        {hayBaja && <span>○ mes de baja confianza</span>}
        {hayProvisional && <span>┄ provisional (sin 18 meses de seguimiento)</span>}
        {hayDescartes && <span>Escenas con nube omitida por la QA: descartadas</span>}
        <span>Marcas inferiores: eventos (clic para el desglose)</span>
      </div>
    </div>
  );
}
