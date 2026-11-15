// Encabezado del resultado: veredicto del caso, indicadores clave y advertencias.
import React, { useState } from 'react';
import { etiquetaFecha, formatoNumero } from '../lib/reglas';
import { TIPOS_EVENTO, textoTipoQuiebre, textoDestino, textoCaso } from '../lib/transformar';

const VEREDICTO = {
  A: {
    titulo: 'Caso A · Manglar conservado',
    texto: 'No se detectó pérdida estructural de extensión en el periodo. Abajo: cómo respondió el manglar a cada evento extremo.',
    color: '#1A824A', fondo: '#ECFDF5', borde: '#A7F3D0', icono: '🌿',
  },
  B: {
    titulo: 'Caso B · Quiebre estructural detectado',
    texto: 'El manglar perdió extensión de forma persistente. La línea del tiempo separa el antes y el después del quiebre.',
    color: '#D94136', fondo: '#FEF2F2', borde: '#FECACA', icono: '🪓',
  },
  C: {
    titulo: 'Caso C · La zona no contiene manglar',
    texto: '',
    color: '#162B4D', fondo: '#F1F5F9', borde: '#CBD5E1', icono: '🏙️',
  },
  DATOS: {
    titulo: 'Faltan datos para decidir',
    texto: '',
    color: '#B7791F', fondo: '#FFFBEB', borde: '#FDE68A', icono: '☁️',
  },
};

function Kpi({ etiqueta, valor, nota, color = '#162B4D' }) {
  return (
    <div className="mango-kpi">
      <span className="mango-kpi-etq">{etiqueta}</span>
      <span className="mango-kpi-val" style={{ color }}>{valor}</span>
      {nota && <span className="mango-kpi-nota">{nota}</span>}
    </div>
  );
}

export default function ResumenResultado({ consulta, vista }) {
  const [verDetalles, setVerDetalles] = useState(false);
  const r = consulta.resultado;
  const caso = textoCaso(r);
  const v = VEREDICTO[caso] || VEREDICTO.DATOS;
  const advertencias = [...(consulta.advertencias || []), ...(r.advertencias || [])];
  const est = r.estadisticos_tendencia;
  const nq = r.naturaleza_quiebre;
  const sat = r.ingesta_satelital || {};
  const ejec = consulta.ejecucion;

  let texto = v.texto;
  if (caso === 'C') texto = r.motivo_rechazo || 'No se encontró manglar en la zona.';
  if (caso === 'DATOS') texto = `${r.motivo || 'No hubo suficientes imágenes limpias.'}${r.sugerencia ? ` Sugerencia: ${r.sugerencia}` : ''} Esto no significa que no haya manglar.`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginBottom: '18px' }}>
      <div className="mango-veredicto" style={{ background: v.fondo, borderColor: v.borde }}>
        <span style={{ fontSize: '30px' }}>{v.icono}</span>
        <div style={{ flex: 1, minWidth: '240px' }}>
          <div style={{ fontWeight: 800, fontSize: '1.1rem', color: v.color }}>{v.titulo}</div>
          <div style={{ fontSize: '13.5px', color: '#334155', marginTop: '4px', lineHeight: 1.45 }}>{texto}</div>
          {caso === 'B' && nq && (
            <div style={{ fontSize: '13px', color: '#7F1D1D', marginTop: '6px', lineHeight: 1.5 }}>
              <strong>{etiquetaFecha(r.fecha_quiebre)}</strong> · ≈ {formatoNumero(nq.ha_perdidas_estimadas, 2)} ha perdidas ·{' '}
              {textoTipoQuiebre(nq.tipo)}
              {nq.destino_cobertura ? ` · ahora es ${textoDestino(nq.destino_cobertura)}` : ''}
              {nq.evento_climatico_asociado
                ? ` · coincide con: ${TIPOS_EVENTO[nq.evento_climatico_asociado.tipo]?.titulo || nq.evento_climatico_asociado.tipo}`
                : ' · sin evento climático que lo explique'}
            </div>
          )}
        </div>
        <div style={{ fontSize: '12px', color: '#475569', textAlign: 'right', fontFamily: 'monospace' }}>
          {consulta.origen === 'piloto' && <div className="mango-pill ok" style={{ marginBottom: '4px' }}>Piloto precalculado</div>}
          {ejec && <div>{ejec.start_date} → {ejec.end_date}</div>}
          {ejec && <div>{formatoNumero(ejec.area_ha, 1)} ha analizadas</div>}
        </div>
      </div>

      {vista && (
        <div className="mango-kpis">
          <Kpi etiqueta="Núcleo de manglar inicial" valor={`${formatoNumero(vista.haNucleo, 1)} ha`} nota={`máscara: ${r.fuente_mascara === 'GMW' ? 'Global Mangrove Watch' : 'firma espectral (MVI)'}`} />
          <Kpi
            etiqueta="Manglar en pie al final"
            valor={`${formatoNumero(vista.ultimaHa, 1)} ha`}
            nota={vista.haNucleo ? `${formatoNumero((vista.ultimaHa / vista.haNucleo) * 100, 0)}% del inicial` : ''}
            color={vista.haNucleo && vista.ultimaHa / vista.haNucleo < 0.9 ? '#D94136' : '#1A824A'}
          />
          <Kpi
            etiqueta="Tendencia de vigor (NDVI)"
            valor={est?.sen_slope_por_anio !== undefined && est?.sen_slope_por_anio !== null ? `${est.sen_slope_por_anio > 0 ? '+' : ''}${formatoNumero(est.sen_slope_por_anio * 10, 3)}/década` : '—'}
            nota={est?.mann_kendall_p_mensual_desestacionalizado !== undefined && est?.mann_kendall_p_mensual_desestacionalizado !== null
              ? est.mann_kendall_p_mensual_desestacionalizado < 0.05 ? 'tendencia significativa (p < 0.05)' : 'sin tendencia significativa'
              : ''}
            color={est?.sen_slope_por_anio < 0 && est?.mann_kendall_p_mensual_desestacionalizado < 0.05 ? '#D94136' : '#162B4D'}
          />
          <Kpi
            etiqueta="Escenas Landsat en el rango"
            valor={formatoNumero(sat.escenas_en_rango ?? sat.valid_scenes_retained, 0)}
            nota={sat.historia_landsat
              ? `${formatoNumero(sat.valid_scenes_retained, 0)} en el histórico ${sat.historia_landsat[0].slice(0, 4)}–${sat.historia_landsat[1].slice(0, 4)}${sat.escenas_desde_cache ? ` · ${formatoNumero(sat.escenas_desde_cache, 0)} desde caché` : ''}`
              : (sat.plataformas || []).join(', ')}
          />
          <Kpi
            etiqueta="Eventos extremos"
            valor={formatoNumero(r.resumen_eventos?.total_detonantes_detectados, 0)}
            nota={`${vista.conteoSeveridad.CRITICO} críticos · ${vista.conteoSeveridad.ALTO} altos · ${vista.conteoSeveridad.MODERADO} moderados`}
            color={vista.conteoSeveridad.CRITICO ? '#D94136' : '#162B4D'}
          />
        </div>
      )}

      {advertencias.length > 0 && (
        <div className="mango-alerta aviso">
          <strong>Advertencias del análisis:</strong>
          <ul style={{ margin: '6px 0 0 0', paddingLeft: '18px' }}>
            {advertencias.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </div>
      )}

      {r.comparativa_recurrencia_top3?.length > 0 && (
        <div>
          <div className="mango-subtitulo">Amenazas más recurrentes en la zona</div>
          <div className="mango-kpis">
            {r.comparativa_recurrencia_top3.map((g) => {
              const t = TIPOS_EVENTO[g.tipo_amenaza] || { titulo: g.tipo_amenaza, icono: '⚠️' };
              return (
                <Kpi
                  key={g.tipo_amenaza}
                  etiqueta={`${t.icono} ${t.titulo}`}
                  valor={`${g.total_episodios_observados} episodios`}
                  nota={g.compara_antes_y_despues_quiebre ? 'ocurrió antes y después del quiebre' : 'observados con imagen posterior'}
                />
              );
            })}
          </div>
        </div>
      )}

      <button type="button" className="mango-enlace" onClick={() => setVerDetalles(!verDetalles)}>
        {verDetalles ? '▾ Ocultar detalles técnicos' : '▸ Ver detalles técnicos (umbrales y fuentes)'}
      </button>
      {verDetalles && (
        <div className="mango-detalles">
          <div><strong>Versión del pipeline:</strong> {r.version_pipeline}</div>
          {r.mascaras_espaciales && (
            <div>
              <strong>Píxeles:</strong> {r.mascaras_espaciales.total_polygon_pixels} totales · {r.mascaras_espaciales.permanent_water_pixels} agua
              permanente · {r.mascaras_espaciales.mangrove_core_pixels} núcleo de manglar
            </div>
          )}
          {r.ingesta_climatica && (
            <div>
              <strong>Clima:</strong> {r.ingesta_climatica.source} · histórico desde {r.ingesta_climatica.historico_desde}
            </div>
          )}
          {r.umbrales_aplicados && (
            <details>
              <summary>Umbrales aplicados (calculados para esta zona)</summary>
              <pre>{JSON.stringify(r.umbrales_aplicados, null, 2)}</pre>
            </details>
          )}
          {r.referencias && (
            <details>
              <summary>Referencias científicas ({Object.keys(r.referencias).length})</summary>
              <ul>{Object.entries(r.referencias).map(([k, t]) => <li key={k}>{t}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
