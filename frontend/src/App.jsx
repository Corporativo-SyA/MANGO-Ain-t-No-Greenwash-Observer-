import React, { useCallback, useEffect, useRef, useState } from 'react';
import logoMango from './assets/logoMango.png';
import MapaZona from './components/MapaZona';
import FiltrosFechas from './components/FiltrosFechas';
import LineaTiempo from './components/LineaTiempo';
import Historial from './components/Historial';
import { api, enCurso, descargarJson, ErrorApi } from './lib/api';
import { bboxDe, coordsDeBbox, validarArea, validarRango } from './lib/reglas';
import './App.css';

// Ondas amplias en tonos naranja, ámbar y coral mango para el banner superior
const ONDAS_SUPERIORES_A = Array.from({ length: 22 }, (_, i) => ({
  d: `M -50 ${25 + i * 7.5} C 360 ${275 - i * 9.5}, 890 ${-75 + i * 9.5}, 1490 ${195 - i * 6.5}`,
  color: i % 2 === 0 ? '#F37021' : '#FBB03B',
  opacity: 0.22 + (i % 5) * 0.035,
}));

const ONDAS_SUPERIORES_B = Array.from({ length: 18 }, (_, i) => ({
  d: `M -50 ${210 - i * 6.5} C 470 ${-55 + i * 8.5}, 1030 ${265 - i * 8.5}, 1490 ${25 + i * 7.5}`,
  color: i % 3 === 0 ? '#D94136' : '#F37021',
  opacity: 0.18 + (i % 4) * 0.035,
}));

const CLAVE_ACTIVA = 'mango.consultaActiva';
const INTERVALO_MS = 2000;

const leerActiva = () => {
  try {
    return localStorage.getItem(CLAVE_ACTIVA);
  } catch {
    return null;
  }
};
const guardarActiva = (id) => {
  try {
    if (id) localStorage.setItem(CLAVE_ACTIVA, id);
    else localStorage.removeItem(CLAVE_ACTIVA);
  } catch {
    /* almacenamiento no disponible */
  }
};

const errorDeConsulta = (c) =>
  c?.estado === 'error' && c.error ? new ErrorApi(c.error.codigo, c.error.mensaje, c.error.detalles) : null;

function nombreAutomatico(coords) {
  const lat = (coords.lat_min + coords.lat_max) / 2;
  const lon = (coords.lon_min + coords.lon_max) / 2;
  return `Zona ${lat.toFixed(3)}, ${lon.toFixed(3)}`;
}

function App() {
  const [fechaInicio, setFechaInicio] = useState('');
  const [fechaFin, setFechaFin] = useState('');
  const [fechasGuardadas, setFechasGuardadas] = useState(null); // { fecha_inicio, fecha_fin } en AAAA-MM
  const [coordenadas, setCoordenadas] = useState(null);
  const [enfoqueMapa, setEnfoqueMapa] = useState(null);

  const [consulta, setConsulta] = useState(null); // sobre Consulta del contrato v2
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState(null);

  const [pilotos, setPilotos] = useState([]);
  const [cargandoPilotos, setCargandoPilotos] = useState(true);
  const [historial, setHistorial] = useState([]);
  const [cargandoHistorial, setCargandoHistorial] = useState(false);
  const fallosSeguidos = useRef(0);

  const recargarHistorial = useCallback(async () => {
    setCargandoHistorial(true);
    try {
      const h = await api.historial();
      setHistorial(h.consultas || []);
    } catch {
      /* el aviso de backend apagado lo da la consulta */
    } finally {
      setCargandoHistorial(false);
    }
  }, []);

  const mostrarConsulta = useCallback((c, { enfocar = false } = {}) => {
    setConsulta(c);
    setError(errorDeConsulta(c));
    guardarActiva(c?.origen === 'pipeline' ? c.id_consulta : null);
    if (enfocar && c?.parametros?.bbox) {
      const [lon_min, lat_min, lon_max, lat_max] = c.parametros.bbox;
      setCoordenadas(coordsDeBbox(c.parametros.bbox));
      setEnfoqueMapa({ bounds: [[lat_min, lon_min], [lat_max, lon_max]] });
      setFechaInicio(c.parametros.fecha_inicio);
      setFechaFin(c.parametros.fecha_fin);
      setFechasGuardadas({ fecha_inicio: c.parametros.fecha_inicio, fecha_fin: c.parametros.fecha_fin });
    }
  }, []);

  // Arranque: pilotos, historial y, si había una consulta en curso, retomarla.
  useEffect(() => {
    api.pilotos()
      .then((r) => setPilotos(r.pilotos || []))
      .catch(() => setPilotos([]))
      .finally(() => setCargandoPilotos(false));
    recargarHistorial();
    const activa = leerActiva();
    if (activa) {
      api.obtenerConsulta(activa)
        .then((c) => mostrarConsulta(c, { enfocar: true }))
        .catch(() => guardarActiva(null));
    }
  }, [recargarHistorial, mostrarConsulta]);

  // Sondeo mientras la consulta esté en cola o en proceso.
  useEffect(() => {
    if (!enCurso(consulta)) return undefined;
    const espera = INTERVALO_MS * Math.min(8, 2 ** fallosSeguidos.current);
    const t = setTimeout(async () => {
      try {
        const c = await api.obtenerConsulta(consulta.id_consulta);
        fallosSeguidos.current = 0;
        setConsulta(c);
        if (!enCurso(c)) {
          setError(errorDeConsulta(c));
          recargarHistorial();
        } else {
          setError(null);
        }
      } catch (e) {
        fallosSeguidos.current += 1;
        setError(e);
        setConsulta((prev) => ({ ...prev })); // reprograma el sondeo con espera creciente
      }
    }, espera);
    return () => clearTimeout(t);
  }, [consulta, recargarHistorial]);

  const bloqueo = coordenadas?.tipo_seleccion === 'area' ? validarArea(coordenadas) : '';

  const consultarBackend = async () => {
    if (!fechasGuardadas || !coordenadas || bloqueo) return;
    setEnviando(true);
    setError(null);
    try {
      if (coordenadas.tipo_seleccion === 'punto') {
        mostrarConsulta(await api.piloto(coordenadas.punto_id));
        return;
      }
      const errorFechas = validarRango(fechasGuardadas.fecha_inicio, fechasGuardadas.fecha_fin);
      if (errorFechas) throw new ErrorApi('RANGO_FECHAS_INVALIDO', errorFechas);
      const solicitud = {
        bbox: bboxDe(coordenadas),
        fecha_inicio: fechasGuardadas.fecha_inicio,
        fecha_fin: fechasGuardadas.fecha_fin,
        nombre: nombreAutomatico(coordenadas),
      };
      const c = await api.crearConsulta(solicitud);
      fallosSeguidos.current = 0;
      mostrarConsulta(c);
      recargarHistorial();
    } catch (e) {
      setError(e);
    } finally {
      setEnviando(false);
    }
  };

  const abrirGuardada = async (id) => {
    setError(null);
    try {
      mostrarConsulta(await api.obtenerConsulta(id), { enfocar: true });
      window.scrollTo({ top: document.body.scrollHeight * 0.45, behavior: 'smooth' });
    } catch (e) {
      setError(e);
    }
  };

  const renombrar = async (id, nombre) => {
    try {
      const c = await api.renombrar(id, nombre);
      if (consulta?.id_consulta === id) setConsulta(c);
      recargarHistorial();
    } catch (e) {
      setError(e);
    }
  };

  const eliminar = async (id) => {
    try {
      await api.eliminar(id);
      if (consulta?.id_consulta === id) mostrarConsulta(null);
      recargarHistorial();
    } catch (e) {
      setError(e);
    }
  };

  const descargar = async (id) => {
    try {
      const c = await api.obtenerConsulta(id);
      descargarJson(c, `mango_${c.nombre || id}`.replace(/[^\w.-]+/g, '_'));
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div style={{ minHeight: '100vh', width: '100%', position: 'relative', paddingBottom: '50px' }}>
      {/* FONDO DECORATIVO: Cristales geométricos transparentes naranjas */}
      <div
        aria-hidden="true"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 0,
          pointerEvents: 'none',
          overflow: 'hidden',
        }}
      >
        <svg width="100%" height="100%" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
          <defs>
            <linearGradient id="cristalNaranja1" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#F37021" stopOpacity="0.14" />
              <stop offset="100%" stopColor="#FBB03B" stopOpacity="0.04" />
            </linearGradient>
            <linearGradient id="cristalNaranja2" x1="100%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#FBB03B" stopOpacity="0.16" />
              <stop offset="100%" stopColor="#D94136" stopOpacity="0.05" />
            </linearGradient>
            <linearGradient id="cristalAmbar" x1="0%" y1="100%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#F37021" stopOpacity="0.11" />
              <stop offset="100%" stopColor="#FFFFFF" stopOpacity="0.02" />
            </linearGradient>
          </defs>

          <polygon points="0,140 190,260 65,420" fill="url(#cristalNaranja1)" stroke="#F37021" strokeOpacity="0.15" strokeWidth="1" />
          <polygon points="190,260 310,390 65,420" fill="url(#cristalNaranja2)" stroke="#FBB03B" strokeOpacity="0.18" strokeWidth="1" />
          <polygon points="0,420 65,420 140,610 0,560" fill="url(#cristalAmbar)" stroke="#F37021" strokeOpacity="0.12" strokeWidth="1" />
          <polygon points="65,420 310,390 230,580 140,610" fill="url(#cristalNaranja1)" stroke="#F37021" strokeOpacity="0.14" strokeWidth="1" />
          <polygon points="0,680 175,740 85,900 0,900" fill="url(#cristalNaranja2)" stroke="#FBB03B" strokeOpacity="0.18" strokeWidth="1" />
          <polygon points="175,740 340,860 220,900 85,900" fill="url(#cristalNaranja1)" stroke="#F37021" strokeOpacity="0.12" strokeWidth="1" />

          <polygon points="1440,160 1250,280 1375,450" fill="url(#cristalNaranja2)" stroke="#F37021" strokeOpacity="0.15" strokeWidth="1" />
          <polygon points="1250,280 1120,410 1375,450" fill="url(#cristalNaranja1)" stroke="#FBB03B" strokeOpacity="0.18" strokeWidth="1" />
          <polygon points="1440,450 1375,450 1290,640 1440,590" fill="url(#cristalAmbar)" stroke="#F37021" strokeOpacity="0.13" strokeWidth="1" />
          <polygon points="1375,450 1120,410 1200,600 1290,640" fill="url(#cristalNaranja2)" stroke="#D94136" strokeOpacity="0.12" strokeWidth="1" />
          <polygon points="1440,690 1260,760 1360,900 1440,900" fill="url(#cristalNaranja1)" stroke="#FBB03B" strokeOpacity="0.18" strokeWidth="1" />

          <polygon points="620,310 740,260 790,370 660,390" fill="url(#cristalAmbar)" stroke="#FBB03B" strokeOpacity="0.12" strokeWidth="1" />
          <polygon points="740,260 880,310 790,370" fill="url(#cristalNaranja1)" stroke="#F37021" strokeOpacity="0.10" strokeWidth="1" />
        </svg>
      </div>

      {/* BARRA SUPERIOR HASTA ARRIBA CON ONDAS NARANJAS GRANDES */}
      <header
        style={{
          position: 'relative',
          zIndex: 10,
          width: '100%',
          margin: 0,
          background: 'radial-gradient(circle at 50% 25%, #FFFFFF 0%, #FFF8EE 55%, #FFEDD5 100%)',
          borderBottom: '4px solid transparent',
          borderImage: 'linear-gradient(90deg, #162B4D 0%, #1A824A 35%, #FBB03B 70%, #F37021 100%) 1',
          boxShadow: '0 6px 24px rgba(243, 112, 33, 0.14)',
          overflow: 'hidden',
        }}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 1440 220"
          preserveAspectRatio="none"
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none',
          }}
        >
          {ONDAS_SUPERIORES_A.map((onda, idx) => (
            <path
              key={`a-${idx}`}
              d={onda.d}
              fill="none"
              stroke={onda.color}
              strokeWidth="1.75"
              strokeOpacity={onda.opacity}
            />
          ))}
          {ONDAS_SUPERIORES_B.map((onda, idx) => (
            <path
              key={`b-${idx}`}
              d={onda.d}
              fill="none"
              stroke={onda.color}
              strokeWidth="1.55"
              strokeOpacity={onda.opacity}
            />
          ))}
        </svg>

        <div
          style={{
            position: 'relative',
            zIndex: 2,
            width: '100%',
            padding: '22px 36px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-start',
            flexWrap: 'wrap',
            gap: '24px',
          }}
        >
          <div
            style={{
              backgroundColor: 'rgba(255, 255, 255, 0.96)',
              padding: '8px 12px',
              borderRadius: '20px',
              border: '1.5px solid rgba(243, 112, 33, 0.28)',
              boxShadow: '0 6px 18px rgba(22, 43, 77, 0.12)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <img
              src={logoMango}
              alt="Logo MANGO - Ain't No Greenwash Observer"
              style={{ height: '132px', width: 'auto', display: 'block', objectFit: 'contain' }}
            />
          </div>

          <div>
            <span
              style={{
                display: 'inline-block',
                backgroundColor: 'rgba(255, 247, 237, 0.95)',
                color: '#F37021',
                border: '1px solid #FED7AA',
                fontSize: '12px',
                fontWeight: '800',
                padding: '4px 13px',
                borderRadius: '999px',
                letterSpacing: '0.6px',
                marginBottom: '8px',
              }}
            >
              OBSERVATORIO SATELITAL DE MANGLARES
            </span>
            <h1
              style={{
                margin: 0,
                fontSize: '2rem',
                color: '#162B4D',
                fontWeight: '800',
                lineHeight: '1.2',
                textShadow: '0 1px 0 rgba(255,255,255,0.85)',
              }}
            >
              Monitoreo de Impacto Climático Costero
            </h1>
            <p style={{ margin: '6px 0 0 0', fontSize: '15px', color: '#334155', fontWeight: '500' }}>
              Detección de eventos extremos y análisis ecológico <strong>Antes vs. Después</strong> con NASA POWER, Landsat y Global Mangrove Watch
            </p>
          </div>
        </div>
      </header>

      {/* CONTENIDO PRINCIPAL */}
      <main
        style={{
          position: 'relative',
          zIndex: 5,
          width: '100%',
          marginTop: '28px',
          padding: '0 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '28px',
        }}
      >
        {/* PASO 1: rango de meses (solo valida y guarda; no llama al backend) */}
        <FiltrosFechas
          fechaInicio={fechaInicio}
          setFechaInicio={setFechaInicio}
          fechaFin={fechaFin}
          setFechaFin={setFechaFin}
          fechasGuardadas={fechasGuardadas}
          onGuardarFechas={(rango) => {
            setFechasGuardadas(rango);
            setError(null);
          }}
        />

        {/* PASO 2: mapa mundial */}
        <MapaZona
          coordenadas={coordenadas}
          onCoordenadasChange={(coords) => {
            setCoordenadas(coords);
            setError(null);
          }}
          pilotos={pilotos}
          cargandoPilotos={cargandoPilotos}
          enfoque={enfoqueMapa}
        />

        {/* PASO 3: consulta, resultado y línea del tiempo */}
        <LineaTiempo
          consulta={consulta}
          enviando={enviando}
          error={error}
          fechasGuardadas={fechasGuardadas}
          coordenadas={coordenadas}
          bloqueo={bloqueo}
          onBuscarEventos={consultarBackend}
          onNuevaConsulta={() => mostrarConsulta(null)}
        />

        <Historial
          consultas={historial}
          cargando={cargandoHistorial}
          idActiva={consulta?.id_consulta}
          onAbrir={abrirGuardada}
          onRenombrar={renombrar}
          onEliminar={eliminar}
          onDescargar={descargar}
          onRecargar={recargarHistorial}
        />

        <footer className="mango-pie">
          Datos: NASA POWER (MERRA-2) · Landsat Collection 2 (USGS/NASA) vía Microsoft Planetary Computer ·
          ©Global Mangrove Watch (CC BY 4.0) · Mapas: Esri, OpenStreetMap
        </footer>
      </main>
    </div>
  );
}

export default App;
