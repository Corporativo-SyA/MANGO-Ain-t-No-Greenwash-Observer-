// Paso 3: gatillo de la consulta, progreso, resultado, línea del tiempo y desglose Antes vs. Después.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import Progreso from './Progreso';
import ResumenResultado from './ResumenResultado';
import GraficaManglar from './GraficaManglar';
import { enCurso } from '../lib/api';
import { cargarMexico, dentroDeMexico, serieGmwAproximada } from '../lib/gmw';
import { etiquetaFecha, formatoNumero } from '../lib/reglas';
import { construirVista, esRelevante, TIPOS_EVENTO, SEVERIDADES, ORDEN_EJES, ICONO_EJE, UNIDADES } from '../lib/transformar';

const NODOS_POR_DEFINICION = [
  ['#162B4D', 'linear-gradient(135deg, #162B4D 0%, #244375 100%)'],
  ['#1A824A', 'linear-gradient(135deg, #1A824A 0%, #27A862 100%)'],
  ['#E09F1F', 'linear-gradient(135deg, #E09F1F 0%, #FBB03B 100%)'],
  ['#F37021', 'linear-gradient(135deg, #F37021 0%, #FF8C42 100%)'],
  ['#D94136', 'linear-gradient(135deg, #D94136 0%, #E85D52 100%)'],
].map(([color, gradiente], i) => ({
  clave: `init-${i + 1}`,
  mes: `MES 0${i + 1}`,
  fecha: 'AAAA-MM',
  cinta: 'AQUÍ VA DATO',
  titulo: 'EVENTO POR DETECTAR',
  descripcion: 'Aquí se mostrará el evento climático extremo y el impacto detectado en el manglar.',
  colorPrincipal: color,
  gradiente,
}));

const PROB_EJE = { 'Cobertura y Hábitat': 'ndvi', Suelo: 'ndmi', Agua: 'mndwi', Microclima: 'lst_minus_t2m' };

function NodoLinea({ nodo, esPrimero, seleccionado, onClick, refNodo }) {
  return (
    <div
      ref={refNodo}
      onClick={onClick}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick?.()}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-pressed={onClick ? seleccionado : undefined}
      className="mango-nodo"
      style={{ cursor: onClick ? 'pointer' : 'default', transform: seleccionado ? 'translateY(-4px)' : 'none' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: '8px' }}>
        <div
          className="mango-pin"
          style={{
            background: nodo.gradiente,
            boxShadow: seleccionado ? '0 8px 20px rgba(243, 112, 33, 0.38)' : '0 6px 14px rgba(22, 43, 77, 0.16)',
            border: seleccionado ? '3px solid #FBB03B' : 'none',
          }}
        >
          <div className="mango-pin-interior">
            {nodo.icono && <span style={{ fontSize: '18px', lineHeight: 1 }}>{nodo.icono}</span>}
            <span style={{ fontSize: '12px', fontWeight: 800, color: nodo.colorPrincipal, letterSpacing: '0.4px', lineHeight: 1.15 }}>
              {nodo.mes}
            </span>
            <span style={{ fontSize: '11px', fontFamily: 'monospace', fontWeight: 700, color: '#475569', marginTop: '3px' }}>
              {nodo.fecha}
            </span>
          </div>
        </div>
        <div style={{ width: 0, height: 0, borderLeft: '10px solid transparent', borderRight: '10px solid transparent', borderTop: `12px solid ${nodo.colorPrincipal}`, marginTop: '-1px' }} />
      </div>

      <div
        className="mango-cinta"
        style={{
          background: nodo.gradiente,
          paddingLeft: esPrimero ? '10px' : '20px',
          clipPath: esPrimero
            ? 'polygon(0% 0%, calc(100% - 14px) 0%, 100% 50%, calc(100% - 14px) 100%, 0% 100%)'
            : 'polygon(0% 0%, calc(100% - 14px) 0%, 100% 50%, calc(100% - 14px) 100%, 0% 100%, 14px 50%)',
        }}
      >
        {nodo.cinta}
      </div>

      <div style={{ width: '100%', display: 'flex', alignItems: 'flex-start', paddingLeft: esPrimero ? '16px' : '24px', paddingRight: '12px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginRight: '12px', flexShrink: 0 }}>
          <div style={{ width: '2px', height: '78px', backgroundColor: nodo.colorPrincipal }} />
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: nodo.colorPrincipal }} />
        </div>
        <div style={{ paddingTop: '12px', textAlign: 'left' }}>
          <h4 style={{ margin: '0 0 5px 0', fontSize: '12px', fontWeight: 800, color: nodo.colorPrincipal, lineHeight: 1.25 }}>
            {nodo.titulo}
          </h4>
          <p style={{ margin: 0, fontSize: '11.5px', color: '#64748B', lineHeight: 1.4 }}>{nodo.descripcion}</p>
        </div>
      </div>
    </div>
  );
}

function aNodoVisual(n) {
  const pct = n.cobertura_manglar_pct;
  return {
    clave: n.clave,
    mes: n.etiqueta_mes.split(' ')[0].toUpperCase(),
    fecha: n.fecha,
    icono: n.icono,
    cinta: `${n.estilo.etiqueta} · ${pct !== null && pct !== undefined ? `${formatoNumero(pct, 1)}%` : '—'}`,
    titulo: n.titulo.toUpperCase(),
    descripcion: n.resumen,
    colorPrincipal: n.estilo.color,
    gradiente: n.estilo.gradiente,
  };
}

function ChipSeveridad({ severidad }) {
  const s = SEVERIDADES[severidad] || SEVERIDADES.SIN_DATO;
  return (
    <span className="mango-chip-sev" style={{ color: s.color, background: s.fondo, borderColor: s.borde }}>
      {s.etiqueta}
    </span>
  );
}

function Desglose({ nodo }) {
  const [referencia, setReferencia] = useState('evento');
  useEffect(() => setReferencia('evento'), [nodo?.clave]);
  if (!nodo) return null;

  const ep = nodo.evento;
  const avd = nodo.antes_vs_despues;
  const nasa = nodo.variables_nasa;
  const esQuiebre = nodo.tipoNodo === 'quiebre';
  const hayDosVistas = ep?.respuesta_al_evento && ep?.ejes && ep.regimen_evento !== 'CON_MANGLAR';
  const ejes = referencia === 'sano' ? avd.despues.ejesVsSano : avd.despues.ejes;
  const mostrarProb = ep && (referencia === 'sano' || ep.regimen_evento === 'CON_MANGLAR') && ep.probabilidad_natural;
  const deltaNdvi = avd.antes.ndvi !== null && avd.despues.ndvi !== null ? avd.despues.ndvi - avd.antes.ndvi : null;

  return (
    <div className="mango-desglose">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', marginBottom: '14px' }}>
        <h4 style={{ margin: 0, color: '#162B4D', fontSize: '1.05rem' }}>
          {nodo.icono} Desglose ecológico: <span style={{ color: '#F37021' }}>{nodo.etiqueta_mes}</span> — {nodo.titulo}
        </h4>
        <span className="mango-chip-sev" style={{ color: nodo.estilo.color, background: nodo.estilo.fondo, borderColor: nodo.estilo.borde }}>
          {nodo.estilo.etiqueta}
        </span>
      </div>

      {ep && (
        <div className="mango-ficha">
          <span><strong>Fechas:</strong> {etiquetaFecha(ep.fecha_inicio)}{ep.fecha_fin !== ep.fecha_inicio ? ` → ${etiquetaFecha(ep.fecha_fin)}` : ''} (pico {etiquetaFecha(ep.fecha_pico)})</span>
          <span>
            <strong>Intensidad:</strong> {formatoNumero(ep.magnitud_maxima, 2)} {UNIDADES[ep.unidad] || ep.unidad} · umbral extremo de esta zona{' '}
            {formatoNumero(ep.umbral_local, 2)}
          </span>
          <span><strong>Imágenes posteriores:</strong> {ep.escenas_post_evento}{ep.ventana_busqueda ? ` (${ep.ventana_busqueda[0]} → ${ep.ventana_busqueda[1]})` : ''}</span>
          {ep.confundido_con?.length > 0 && (
            <span style={{ color: '#B7791F' }}>
              ⚠ En la misma ventana hubo {ep.confundido_con.length} evento(s) más: el efecto no se puede atribuir solo a este.
            </span>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px', marginBottom: '16px' }}>
        <div className="mango-tarjeta" style={{ borderLeft: '5px solid #1A824A' }}>
          <strong style={{ color: '#1A824A', fontSize: '14px' }}>Estado "Antes" del evento</strong>
          <p className="mango-parrafo">{avd.antes.descripcion}</p>
          <span className="mango-dato" style={{ color: '#1A824A', background: '#ECFDF5' }}>
            NDVI previo: {formatoNumero(avd.antes.ndvi, 3)}{avd.antes.mes ? ` (${avd.antes.mes})` : ''}
          </span>
          {avd.antes.condicion !== null && avd.antes.condicion !== undefined && (
            <span className="mango-dato" style={{ color: '#162B4D', background: '#F1F5F9', marginLeft: '6px' }}>
              Condición: z {formatoNumero(avd.antes.condicion, 1)}
            </span>
          )}
        </div>

        <div className="mango-tarjeta" style={{ borderLeft: `5px solid ${nodo.estilo.color}` }}>
          <strong style={{ color: nodo.estilo.color, fontSize: '14px' }}>Consecuencias "Después"</strong>
          <p className="mango-parrafo">{avd.despues.descripcion}</p>
          <span className="mango-dato" style={{ color: nodo.estilo.color, background: nodo.estilo.fondo }}>
            NDVI posterior: {formatoNumero(avd.despues.ndvi, 3)}{avd.despues.mes ? ` (${avd.despues.mes})` : ''}
            {deltaNdvi !== null ? ` · Δ ${deltaNdvi > 0 ? '+' : ''}${formatoNumero(deltaNdvi, 3)}` : ''}
          </span>
          {avd.despues.condicion !== null && avd.despues.condicion !== undefined && (
            <span className="mango-dato" style={{
              color: avd.despues.condicion < -2 ? '#D94136' : '#162B4D',
              background: avd.despues.condicion < -2 ? '#FEF2F2' : '#F1F5F9',
              marginLeft: '6px',
            }}>
              Condición: z {formatoNumero(avd.despues.condicion, 1)}{avd.despues.condicion < -2 ? ' · estrés fuerte' : ''}
            </span>
          )}
          {esQuiebre && (
            <ul style={{ margin: '10px 0 0 0', paddingLeft: '18px', fontSize: '12.5px', color: nodo.estilo.color, fontWeight: 600 }}>
              {avd.despues.consecuencias.map((c) => <li key={c} style={{ marginBottom: '4px' }}>{c}</li>)}
            </ul>
          )}
        </div>
      </div>

      {ejes && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px', marginBottom: '10px' }}>
            <span className="mango-subtitulo" style={{ margin: 0 }}>Secuelas en los 4 ejes del ecosistema</span>
            {hayDosVistas && (
              <div className="mango-segmentado pequeno">
                <button type="button" className={referencia === 'evento' ? 'activo-navy' : ''} onClick={() => setReferencia('evento')}>
                  Efecto del evento
                </button>
                <button type="button" className={referencia === 'sano' ? 'activo-verde' : ''} onClick={() => setReferencia('sano')}>
                  Frente al manglar sano
                </button>
              </div>
            )}
          </div>
          {hayDosVistas && (
            <p style={{ margin: '0 0 10px 0', fontSize: '12px', color: '#64748B' }}>
              {referencia === 'evento'
                ? 'Cuánto cambió la zona por el evento, comparada con su propio estado en ese momento.'
                : 'Cómo está el terreno comparado con el manglar sano original (muestra lo que se perdió).'}
            </p>
          )}
          <div className="mango-ejes">
            {ORDEN_EJES.filter((k) => ejes[k]).map((k) => {
              const p = mostrarProb ? ep.probabilidad_natural[PROB_EJE[k]] : null;
              return (
                <div key={k} className="mango-eje">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                    <strong style={{ fontSize: '13px', color: '#162B4D' }}>{ICONO_EJE[k]} {k}</strong>
                    <ChipSeveridad severidad={ejes[k].severidad} />
                  </div>
                  <p style={{ margin: '6px 0 0 0', fontSize: '12.5px', color: '#334155', lineHeight: 1.4 }}>{ejes[k].descripcion}</p>
                  {p !== null && p !== undefined && (
                    <div style={{ fontSize: '11px', color: '#64748B', marginTop: '6px' }}>
                      Probabilidad de que sea variación natural: <strong>{p < 0.001 ? '< 0.1%' : `${formatoNumero(p * 100, 1)}%`}</strong>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      <div className="mango-nasa">
        <span style={{ color: '#FBB03B', fontWeight: 700 }}>NASA POWER · {nodo.etiqueta_mes}:</span>
        <span>
          Temp. máx: <strong>{formatoNumero(nasa.temperatura_max_c, 1)} °C</strong> &nbsp;|&nbsp; Precipitación del mes:{' '}
          <strong>{formatoNumero(nasa.precipitacion_mm, 1)} mm</strong> &nbsp;|&nbsp; Viento máx: <strong>{formatoNumero(nasa.viento_max_ms, 1)} m/s</strong>
          &nbsp;|&nbsp; Radiación: <strong>{formatoNumero(nasa.radiacion_mj, 1)} MJ/m²</strong>
        </span>
      </div>
    </div>
  );
}

export default function LineaTiempo({ consulta, enviando, error, fechasGuardadas, coordenadas, bloqueo, onBuscarEventos, onNuevaConsulta }) {
  const [seleccion, setSeleccion] = useState(null);
  const [filtro, setFiltro] = useState('relevantes');
  const [tipoFiltro, setTipoFiltro] = useState('todos');
  const [avisoRequisitos, setAvisoRequisitos] = useState('');
  const carruselRef = useRef(null);
  const refsNodos = useRef(new Map());

  const resultado = consulta?.estado === 'completada' ? consulta.resultado : null;
  const vista = useMemo(() => construirVista(resultado), [resultado]);

  // Sin GMW_RUTA el backend no trae la serie anual de GMW; para México se aproxima con la cuadrícula
  // de 1 km (supone manglar uniforme dentro de cada celda: solo orientativo).
  const [gmwAprox, setGmwAprox] = useState(null);
  const bboxConsulta = consulta?.ejecucion?.bbox || consulta?.parametros?.bbox;
  useEffect(() => {
    setGmwAprox(null);
    if (!resultado || resultado.extension_gmw_anual?.length || !bboxConsulta || !dentroDeMexico(bboxConsulta)) return undefined;
    let vigente = true;
    cargarMexico()
      .then((mx) => { if (vigente) setGmwAprox(serieGmwAproximada(mx, bboxConsulta)); })
      .catch(() => {});
    return () => { vigente = false; };
  }, [resultado, bboxConsulta]);
  const corriendo = enCurso(consulta);

  const relevantes = vista ? vista.nodos.filter(esRelevante) : [];
  const nodosFiltrados = vista
    ? vista.nodos
        .filter((n) => filtro === 'todos' || esRelevante(n))
        .filter((n) => tipoFiltro === 'todos' || n.tipoNodo === 'quiebre' || n.evento?.tipo === tipoFiltro)
    : [];
  const tiposPresentes = vista ? [...new Set(vista.nodos.filter((n) => n.evento).map((n) => n.evento.tipo))] : [];
  const tieneEventosReales = Boolean(vista && vista.nodos.length > 0);
  const nodoSeleccionado = vista?.nodos.find((n) => n.clave === seleccion) || null;

  // Al llegar un resultado: filtro por defecto y primer nodo relevante (o el quiebre) seleccionado.
  useEffect(() => {
    if (!vista) {
      setSeleccion(null);
      return;
    }
    const hayRelevantes = vista.nodos.some(esRelevante);
    setFiltro(hayRelevantes ? 'relevantes' : 'todos');
    setTipoFiltro('todos');
    const quiebre = vista.nodos.find((n) => n.tipoNodo === 'quiebre');
    const peor = [...vista.nodos].sort((a, b) => (SEVERIDADES[b.severidad]?.rango ?? 0) - (SEVERIDADES[a.severidad]?.rango ?? 0))[0];
    setSeleccion((quiebre || peor || vista.nodos[0])?.clave ?? null);
  }, [vista]);

  useEffect(() => {
    if (fechasGuardadas && coordenadas) setAvisoRequisitos('');
  }, [fechasGuardadas, coordenadas]);

  useEffect(() => {
    const el = seleccion && refsNodos.current.get(seleccion);
    if (el && carruselRef.current) {
      const c = carruselRef.current;
      c.scrollTo({ left: el.offsetLeft - c.offsetLeft - c.clientWidth / 2 + el.clientWidth / 2, behavior: 'smooth' });
    }
  }, [seleccion, filtro, tipoFiltro]);

  const seleccionarDesdeGrafica = (nodo) => {
    if (!esRelevante(nodo)) setFiltro('todos');
    if (tipoFiltro !== 'todos' && nodo.evento?.tipo !== tipoFiltro) setTipoFiltro('todos');
    setSeleccion(nodo.clave);
  };

  const moverCarrusel = (dir) => carruselRef.current?.scrollBy({ left: dir * 280, behavior: 'smooth' });

  const manejarClickBuscar = () => {
    if (!fechasGuardadas && !coordenadas) return setAvisoRequisitos('Primero guarda las fechas (Paso 1) y marca un territorio en el mapa (Paso 2).');
    if (!fechasGuardadas) return setAvisoRequisitos('Falta presionar "Seleccionar fechas" en el Paso 1.');
    if (!coordenadas) return setAvisoRequisitos('Falta marcar el territorio en el mapa (dibuja un cuadrado o elige un piloto).');
    if (bloqueo) return setAvisoRequisitos(bloqueo);
    setAvisoRequisitos('');
    return onBuscarEventos();
  };

  const listoParaBuscar = Boolean(fechasGuardadas && coordenadas && !bloqueo);
  const esPiloto = coordenadas?.tipo_seleccion === 'punto';
  const textoBoton = corriendo
    ? `Analizando… ${Math.round(consulta?.progreso?.porcentaje ?? 0)}%`
    : enviando
      ? 'Enviando…'
      : esPiloto
        ? 'Abrir piloto'
        : 'Buscar eventos extremos';

  const nodosVisuales = tieneEventosReales ? nodosFiltrados.map(aNodoVisual) : NODOS_POR_DEFINICION;

  return (
    <section className="mango-card" style={{ borderTop: '4px solid #FBB03B', padding: '26px 28px' }}>
      <div className="mango-card-head" style={{ paddingBottom: '14px', borderBottom: '1px solid #F1F5F9' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <h3 className="mango-h3">
              <span className="mango-paso" style={{ background: '#FBB03B' }}>3</span> Línea del tiempo de eventos climáticos
            </h3>
            <span className={`mango-pill ${tieneEventosReales ? 'ok' : 'naranja'}`}>
              {tieneEventosReales
                ? `${vista.nodos.filter((n) => n.tipoNodo === 'evento').length} eventos · ${relevantes.length} con impacto`
                : corriendo
                  ? 'Analizando…'
                  : resultado
                    ? 'Resultado sin línea del tiempo'
                    : 'Sin datos del backend todavía'}
            </span>
          </div>
          <p className="mango-sub" style={{ marginTop: '5px' }}>
            {tieneEventosReales
              ? 'Haz clic en un nodo o en una marca de la gráfica para ver la comparativa Antes vs. Después.'
              : corriendo
                ? 'MANGO está descargando y analizando los datos satelitales y climáticos de tu zona.'
                : 'Verifica que las fechas y el mapa estén listos y presiona el botón para generar la línea del tiempo.'}
          </p>
        </div>
        {tieneEventosReales && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button type="button" onClick={() => moverCarrusel(-1)} title="Desplazar a la izquierda" className="mango-flecha">◀</button>
            <button type="button" onClick={() => moverCarrusel(1)} title="Desplazar a la derecha" className="mango-flecha naranja">▶</button>
          </div>
        )}
      </div>

      <div className="mango-toolbar" style={{ padding: '14px 18px', margin: '18px 0 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <span className={`mango-pill cuadrado ${fechasGuardadas ? 'ok' : 'error'}`}>
            {fechasGuardadas ? `Fechas: ${fechasGuardadas.fecha_inicio} a ${fechasGuardadas.fecha_fin}` : 'Fechas sin guardar'}
          </span>
          <span className={`mango-pill cuadrado ${coordenadas && !bloqueo ? 'ok' : 'error'}`}>
            {coordenadas ? (bloqueo ? 'Territorio fuera de límites' : esPiloto ? `Piloto: ${coordenadas.zona_manglar.split(',')[0]}` : 'Territorio marcado') : 'Territorio sin marcar'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {resultado && !corriendo && (
            <button type="button" className="mango-btn-sec" onClick={onNuevaConsulta}>Limpiar resultado</button>
          )}
          <button
            type="button"
            onClick={manejarClickBuscar}
            disabled={enviando || corriendo}
            className="mango-btn-buscar"
            style={{
              background: listoParaBuscar && !corriendo ? 'linear-gradient(135deg, #F37021 0%, #E85D1A 100%)' : '#94A3B8',
              boxShadow: listoParaBuscar && !corriendo ? '0 4px 14px rgba(243, 112, 33, 0.32)' : 'none',
            }}
          >
            {textoBoton}
          </button>
        </div>
      </div>

      {esPiloto && coordenadas.piloto && fechasGuardadas && !resultado && !corriendo && (
        <div className="mango-alerta info">
          Los pilotos están precalculados para {coordenadas.piloto.fecha_inicio} → {coordenadas.piloto.fecha_fin} y se abren al instante;
          tu rango de fechas no se aplica. Para otro rango, dibuja un área con el modo Por Área.
        </div>
      )}
      {avisoRequisitos && <div className="mango-alerta error">{avisoRequisitos}</div>}
      {error && (
        <div className={`mango-alerta ${error.codigo === 'BACKEND_APAGADO' || error.codigo === 'FUENTE_EXTERNA_NO_DISPONIBLE' ? 'naranja' : 'error'}`}>
          <strong>{error.codigo === 'BACKEND_APAGADO' ? 'Backend apagado. ' : ''}</strong>
          {error.message || error.mensaje}
        </div>
      )}

      {corriendo && <Progreso consulta={consulta} />}

      {resultado && <ResumenResultado consulta={consulta} vista={vista} />}

      {vista && vista.serie.length > 0 && (
        <div className="mango-panel">
          <div className="mango-subtitulo">Extensión de manglar en pie, mes a mes</div>
          <GraficaManglar
            serie={vista.serie}
            gmw={resultado.extension_gmw_anual?.length ? resultado.extension_gmw_anual : gmwAprox || []}
            gmwAproximado={!resultado.extension_gmw_anual?.length && Boolean(gmwAprox?.length)}
            fechaQuiebre={resultado.fecha_quiebre}
            nodos={vista.nodos}
            seleccionado={seleccion}
            onSeleccionar={seleccionarDesdeGrafica}
          />
        </div>
      )}

      {resultado && !tieneEventosReales && resultado.status === 'COMPLETED' && (
        <div className="mango-alerta ok">El backend no reportó eventos climáticos extremos en ese rango de fechas y territorio.</div>
      )}

      {tieneEventosReales && (
        <div className="mango-filtros">
          <div className="mango-segmentado pequeno">
            <button type="button" className={filtro === 'relevantes' ? 'activo-naranja' : ''} onClick={() => setFiltro('relevantes')}>
              Con impacto ({relevantes.length})
            </button>
            <button type="button" className={filtro === 'todos' ? 'activo-navy' : ''} onClick={() => setFiltro('todos')}>
              Todos ({vista.nodos.length})
            </button>
          </div>
          <select value={tipoFiltro} onChange={(e) => setTipoFiltro(e.target.value)} className="mango-select" aria-label="Filtrar por tipo de evento">
            <option value="todos">Todos los tipos</option>
            {tiposPresentes.map((t) => <option key={t} value={t}>{TIPOS_EVENTO[t]?.titulo || t}</option>)}
          </select>
          {filtro === 'relevantes' && relevantes.length === 0 && (
            <span style={{ fontSize: '12.5px', color: '#1A824A', fontWeight: 600 }}>
              Ningún evento alteró el manglar más allá de su variación natural. 🌿
            </span>
          )}
        </div>
      )}

      {(!resultado || tieneEventosReales) && (
        <div ref={carruselRef} className="mango-carrusel">
          {nodosVisuales.map((nodo, index) => (
            <NodoLinea
              key={nodo.clave}
              nodo={nodo}
              esPrimero={index === 0}
              seleccionado={tieneEventosReales && seleccion === nodo.clave}
              onClick={tieneEventosReales ? () => setSeleccion(nodo.clave) : undefined}
              refNodo={(el) => (el ? refsNodos.current.set(nodo.clave, el) : refsNodos.current.delete(nodo.clave))}
            />
          ))}
          {tieneEventosReales && nodosVisuales.length === 0 && (
            <div style={{ padding: '30px', color: '#64748B', fontSize: '13px' }}>No hay eventos con este filtro.</div>
          )}
        </div>
      )}

      {tieneEventosReales && nodoSeleccionado && <Desglose nodo={nodoSeleccionado} />}
    </section>
  );
}
