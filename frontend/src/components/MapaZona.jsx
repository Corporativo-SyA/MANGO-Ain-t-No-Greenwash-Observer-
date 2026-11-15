// Paso 2: selección geográfica. "Por Área" dibuja un rectángulo (2 clics); "Por Puntos" usa los
// casos piloto precalculados del backend (GET /api/pilotos), que se abren al instante.
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { MapContainer, TileLayer, Rectangle, Marker, Popup, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { AREA_MAX_KM2, areaKm2, validarArea, formatoNumero } from '../lib/reglas';
import CapaGMW, { ZOOM_MEXICO } from './CapaGMW';
import { cargarMundo, cargarMexico, porMilDelAnio, LIMITES_MEXICO } from '../lib/gmw';

const ZOOM_DIBUJO = 11;

const crearIconoManglar = (seleccionado = false) =>
  L.divIcon({
    className: 'mango-marker-custom',
    html: `
      <div style="
        background-color: ${seleccionado ? '#F37021' : '#1A824A'};
        border: 3px solid #FFFFFF;
        box-shadow: 0 3px 10px rgba(22, 43, 77, 0.45);
        width: ${seleccionado ? '34px' : '28px'};
        height: ${seleccionado ? '34px' : '28px'};
        border-radius: 50% 50% 50% 0;
        transform: rotate(-45deg);
        display: flex; align-items: center; justify-content: center;
        transition: all 0.2s ease;">
        <span style="transform: rotate(45deg); font-size: ${seleccionado ? '15px' : '13px'};">🌿</span>
      </div>`,
    iconSize: seleccionado ? [34, 34] : [28, 28],
    iconAnchor: seleccionado ? [17, 34] : [14, 28],
    popupAnchor: [0, -30],
  });

const CASO_PILOTO = {
  CASO_A: 'Caso A · manglar conservado',
  CASO_B: 'Caso B · quiebre estructural',
  CASO_C: 'Caso C · sin manglar',
};

function AjustadorMapa({ destino }) {
  const map = useMap();
  useEffect(() => {
    const t = setTimeout(() => map.invalidateSize(), 150);
    return () => clearTimeout(t);
  }, [map]);
  useEffect(() => {
    if (!destino) return;
    if (destino.bounds) map.flyToBounds(destino.bounds, { padding: [40, 40], duration: 1.1, maxZoom: 15 });
    else map.flyTo(destino.centro, destino.zoom, { duration: 1.1 });
  }, [destino, map]);
  return null;
}

function ObservadorZoom({ onZoom, onCentro }) {
  const map = useMapEvents({
    zoomend: () => onZoom(map.getZoom()),
    moveend: () => onCentro?.(map.getCenter()),
  });
  useEffect(() => {
    onZoom(map.getZoom());
    onCentro?.(map.getCenter());
  }, [map, onZoom, onCentro]);
  return null;
}

function DibujadorCuadrado({ dibujando, onAreaCompletada }) {
  const [primerPunto, setPrimerPunto] = useState(null);
  const [puntoTemporal, setPuntoTemporal] = useState(null);

  useEffect(() => {
    if (!dibujando) {
      setPrimerPunto(null);
      setPuntoTemporal(null);
    }
  }, [dibujando]);

  useMapEvents({
    click(e) {
      if (!dibujando) return;
      if (!primerPunto) {
        setPrimerPunto([e.latlng.lat, e.latlng.lng]);
        setPuntoTemporal([e.latlng.lat, e.latlng.lng]);
        return;
      }
      const r4 = (v) => Number(v.toFixed(4));
      onAreaCompletada({
        tipo_seleccion: 'area',
        lat_min: r4(Math.min(primerPunto[0], e.latlng.lat)),
        lat_max: r4(Math.max(primerPunto[0], e.latlng.lat)),
        lon_min: r4(Math.min(primerPunto[1], e.latlng.lng)),
        lon_max: r4(Math.max(primerPunto[1], e.latlng.lng)),
      });
      setPrimerPunto(null);
      setPuntoTemporal(null);
    },
    mousemove(e) {
      if (dibujando && primerPunto) setPuntoTemporal([e.latlng.lat, e.latlng.lng]);
    },
  });

  if (!(dibujando && primerPunto && puntoTemporal)) return null;
  const preview = {
    lat_min: Math.min(primerPunto[0], puntoTemporal[0]),
    lat_max: Math.max(primerPunto[0], puntoTemporal[0]),
    lon_min: Math.min(primerPunto[1], puntoTemporal[1]),
    lon_max: Math.max(primerPunto[1], puntoTemporal[1]),
  };
  const km2 = areaKm2(preview);
  const excedida = km2 > AREA_MAX_KM2;
  return (
    <Rectangle
      bounds={[primerPunto, puntoTemporal]}
      pathOptions={{
        color: excedida ? '#D94136' : '#F37021',
        weight: 2,
        dashArray: '6, 6',
        fillColor: excedida ? '#D94136' : '#FBB03B',
        fillOpacity: 0.25,
        interactive: false,
      }}
    >
      <Tooltip permanent direction="top" className="mango-tooltip-area">
        {formatoNumero(km2, 2)} km² {excedida ? `· máx. ${AREA_MAX_KM2}` : ''}
      </Tooltip>
    </Rectangle>
  );
}

/** Buscador de lugares (OpenStreetMap Nominatim) o de coordenadas "lat, lon". */
function Buscador({ onIr }) {
  const [texto, setTexto] = useState('');
  const [estado, setEstado] = useState('');

  const buscar = async (e) => {
    e.preventDefault();
    const q = texto.trim();
    if (!q) return;
    const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (m) {
      const lat = Number(m[1]);
      const lon = Number(m[2]);
      if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
        onIr({ centro: [lat, lon], zoom: 13 });
        setEstado('');
        return;
      }
    }
    setEstado('Buscando…');
    try {
      const r = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`,
        { headers: { 'Accept-Language': 'es' } }
      );
      const datos = await r.json();
      if (!datos.length) {
        setEstado('Sin resultados. Prueba con "lat, lon" (ej. 19.05, -96.09).');
        return;
      }
      const [s, n, o, e2] = datos[0].boundingbox.map(Number);
      onIr({ bounds: [[s, o], [n, e2]] });
      setEstado('');
    } catch {
      setEstado('El buscador de lugares no responde; escribe coordenadas "lat, lon".');
    }
  };

  return (
    <form onSubmit={buscar} style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
      <input
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        placeholder="Buscar lugar o 'lat, lon'"
        aria-label="Buscar lugar o coordenadas"
        className="mango-input-mini"
      />
      <button type="submit" className="mango-btn-sec">Ir</button>
      {estado && <span style={{ fontSize: '12px', color: '#64748B' }}>{estado}</span>}
    </form>
  );
}

export default function MapaZona({ coordenadas, onCoordenadasChange, pilotos = [], cargandoPilotos, enfoque }) {
  const [modoSeleccion, setModoSeleccion] = useState('area');
  const [dibujando, setDibujando] = useState(false);
  const [tipoMapa, setTipoMapa] = useState('satelite');
  const [zoom, setZoom] = useState(5);
  const [destino, setDestino] = useState(null);
  const [capaGmw, setCapaGmw] = useState(false);
  const [gmwMundo, setGmwMundo] = useState(null);
  const [gmwMexico, setGmwMexico] = useState(null);
  const [gmwEstado, setGmwEstado] = useState('');
  const [gmwAnio, setGmwAnio] = useState(2025);
  const [gmwModo, setGmwModo] = useState('extension');
  const [centroMapa, setCentroMapa] = useState({ lat: 21.5, lng: -99.0 });

  const alternarCapa = async () => {
    const activar = !capaGmw;
    setCapaGmw(activar);
    if (!activar || (gmwMundo && gmwMexico)) return;
    setGmwEstado('Cargando manglares de Global Mangrove Watch…');
    try {
      const [mundo, mexico] = await Promise.all([cargarMundo(), cargarMexico()]);
      setGmwMundo(mundo);
      setGmwMexico(mexico);
      setGmwEstado('');
    } catch {
      setGmwEstado('No se encontraron las capas GMW (frontend/public/capas). Ver frontend/README.md.');
    }
  };

  const pmAnio = useMemo(() => (gmwMexico ? porMilDelAnio(gmwMexico, gmwAnio) : null), [gmwMexico, gmwAnio]);
  const pmBase = useMemo(() => (gmwMexico ? porMilDelAnio(gmwMexico, gmwMexico.anios[0]) : null), [gmwMexico]);
  const vistaMexico = zoom >= ZOOM_MEXICO && centroMapa.lng > LIMITES_MEXICO.oeste && centroMapa.lng < LIMITES_MEXICO.este
    && centroMapa.lat > LIMITES_MEXICO.sur && centroMapa.lat < LIMITES_MEXICO.norte;

  // Clic en una celda de 1 km de México: se propone un bloque de 3×3 celdas (~11 km², dentro del límite).
  const elegirCeldaMexico = useCallback((lat, lon) => {
    const r4 = (v) => Number(v.toFixed(4));
    setModoSeleccion('area');
    onCoordenadasChange({
      tipo_seleccion: 'area',
      lat_min: r4(lat - 0.01), lat_max: r4(lat + 0.02),
      lon_min: r4(lon - 0.01), lon_max: r4(lon + 0.02),
    });
  }, [onCoordenadasChange]);
  // Clic en una celda del mapa mundial (0.1°, ~120 km²): demasiado grande para analizar; se acerca.
  const elegirCeldaMundo = useCallback((lat, lon) => {
    setDestino({ bounds: [[lat, lon], [lat + 0.1, lon + 0.1]] });
  }, []);

  // Enfoque pedido desde fuera (p. ej. al abrir una consulta guardada).
  useEffect(() => {
    if (enfoque) setDestino(enfoque);
  }, [enfoque]);

  const puntoActivoId = coordenadas?.tipo_seleccion === 'punto' ? coordenadas.punto_id : null;
  const errorArea = coordenadas?.tipo_seleccion === 'area' ? validarArea(coordenadas) : '';

  const ubicaciones = (Array.isArray(pilotos) ? pilotos : [])
    .filter((p) => Array.isArray(p.parametros?.bbox))
    .map((p) => {
      const [lon_min, lat_min, lon_max, lat_max] = p.parametros.bbox;
      return { ...p, lat: (lat_min + lat_max) / 2, lon: (lon_min + lon_max) / 2, bbox: p.parametros.bbox };
    });

  const manejarAreaCompletada = (nuevas) => {
    setDibujando(false);
    onCoordenadasChange(nuevas);
  };

  const limpiarSeleccion = () => {
    setDibujando(false);
    onCoordenadasChange(null);
  };

  const manejarSeleccionPunto = (p) => {
    const [lon_min, lat_min, lon_max, lat_max] = p.bbox;
    setDestino({ bounds: [[lat_min, lon_min], [lat_max, lon_max]] });
    onCoordenadasChange({
      tipo_seleccion: 'punto',
      punto_id: p.id,
      zona_manglar: p.nombre,
      lat: p.lat,
      lon: p.lon,
      lat_min,
      lat_max,
      lon_min,
      lon_max,
      piloto: { fecha_inicio: p.parametros.fecha_inicio, fecha_fin: p.parametros.fecha_fin, caso: p.caso },
    });
  };

  const colorSeleccion = coordenadas?.tipo_seleccion === 'punto' ? '#1A824A' : errorArea ? '#D94136' : '#F37021';

  return (
    <section className="mango-card" style={{ borderTop: '4px solid #1A824A', padding: '24px 28px' }}>
      <div className="mango-card-head">
        <div>
          <h3 className="mango-h3">
            <span className="mango-paso" style={{ background: '#1A824A' }}>2</span> Ubicación geográfica del manglar
          </h3>
          <p className="mango-sub">
            {modoSeleccion === 'area'
              ? `Acércate a la costa y dibuja un rectángulo de hasta ${AREA_MAX_KM2} km² (≈ 5 × 5 km) sobre el manglar.`
              : 'Casos piloto ya calculados por el backend: se abren al instante, sin esperar la descarga satelital.'}
          </p>
        </div>

        <div className="mango-segmentado">
          <button
            type="button"
            onClick={() => { setModoSeleccion('area'); setDibujando(false); }}
            className={modoSeleccion === 'area' ? 'activo-navy' : ''}
          >
            Por Área (Cuadrado)
          </button>
          <button
            type="button"
            onClick={() => { setModoSeleccion('puntos'); setDibujando(false); }}
            className={modoSeleccion === 'puntos' ? 'activo-verde' : ''}
          >
            Por Puntos (Pilotos){ubicaciones.length ? ` · ${ubicaciones.length}` : ''}
          </button>
        </div>
      </div>

      <div className="mango-toolbar">
        {modoSeleccion === 'area' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={() => setDibujando(!dibujando)}
              className="mango-btn-accion"
              style={{ backgroundColor: dibujando ? '#D94136' : '#F37021' }}
            >
              {dibujando ? '✖ Cancelar dibujo' : '▭ Seleccionar sección con cuadrado'}
            </button>
            {coordenadas && (
              <button type="button" onClick={limpiarSeleccion} className="mango-btn-quitar">Quitar selección</button>
            )}
            <span style={{ fontSize: '12px', color: dibujando ? '#D94136' : '#475569', fontWeight: dibujando ? 700 : 400 }}>
              {dibujando
                ? zoom < ZOOM_DIBUJO
                  ? `Acércate más (zoom ${zoom}; recomendado ≥ ${ZOOM_DIBUJO}) y haz clic en dos esquinas opuestas.`
                  : 'Clic en una esquina y luego en la esquina opuesta.'
                : 'Busca tu zona, acércate y presiona el botón naranja.'}
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            {cargandoPilotos ? (
              <span style={{ fontSize: '13px', color: '#64748B' }}>Cargando pilotos…</span>
            ) : ubicaciones.length === 0 ? (
              <span style={{ fontSize: '13px', fontWeight: 600, color: '#64748B' }}>
                El backend aún no tiene pilotos generados (scripts/generar_pilotos.py). Usa <strong>Por Área</strong>.
              </span>
            ) : (
              <>
                <span style={{ fontSize: '13px', fontWeight: 700, color: '#1A824A' }}>{ubicaciones.length} zonas:</span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {ubicaciones.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => manejarSeleccionPunto(p)}
                      className={`mango-pildora ${puntoActivoId === p.id ? 'activa' : ''}`}
                      title={p.descripcion}
                    >
                      {p.nombre.split(',')[0]}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <button type="button" onClick={alternarCapa} className="mango-btn-sec"
            style={capaGmw ? { background: '#1A824A', color: '#FFFFFF', borderColor: '#1A824A' } : undefined}
            aria-pressed={capaGmw} title="Extensión de manglar de Global Mangrove Watch">
            🌿 Manglares GMW
          </button>
          <Buscador onIr={setDestino} />
          <button type="button" className="mango-btn-sec" onClick={() => setTipoMapa(tipoMapa === 'satelite' ? 'calles' : 'satelite')}>
            {tipoMapa === 'satelite' ? 'Vista calles' : 'Vista satelital'}
          </button>
        </div>
      </div>

      {capaGmw && (
        <div className="mango-gmw-barra">
          {gmwEstado ? (
            <span>{gmwEstado}</span>
          ) : vistaMexico ? (
            <>
              <span className="mango-gmw-leyenda">
                {gmwModo === 'cambio'
                  ? <><i style={{ background: '#D94136' }} /> pérdida <i style={{ background: '#1A824A' }} /> ganancia vs. 1985</>
                  : <><i style={{ background: 'rgba(26,130,74,0.25)' }} /><i style={{ background: 'rgba(26,130,74,0.9)' }} /> % de manglar por celda de 1 km</>}
              </span>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                Año <strong style={{ fontFamily: 'monospace' }}>{gmwAnio}</strong>
                <input type="range" className="mango-slider" min={1985} max={2025} step={1} value={gmwAnio}
                  onChange={(e) => setGmwAnio(Number(e.target.value))} aria-label="Año de la capa GMW" />
              </label>
              <select className="mango-select" value={gmwModo} onChange={(e) => setGmwModo(e.target.value)} aria-label="Modo de la capa GMW">
                <option value="extension">Extensión</option>
                <option value="cambio">Cambio desde 1985</option>
              </select>
              <span style={{ color: '#475569' }}>Clic en una celda para proponer un área de ~11 km².</span>
            </>
          ) : (
            <span>
              Manglar mundial 2025 (celdas de ~11 km). Acércate a México (zoom ≥ {ZOOM_MEXICO}) para ver 1985–2025
              y elegir celdas de 1 km; en el resto del mundo, un clic acerca la celda.
            </span>
          )}
        </div>
      )}

      <div
        style={{
          height: '440px',
          width: '100%',
          borderRadius: '12px',
          overflow: 'hidden',
          border: '2px solid #162B4D',
          cursor: dibujando ? 'crosshair' : 'grab',
          position: 'relative',
        }}
      >
        <MapContainer
          center={[21.5, -99.0]}
          zoom={5}
          minZoom={3}
          maxBounds={[[-85, -180], [85, 180]]}
          maxBoundsViscosity={1.0}
          style={{ height: '100%', width: '100%' }}
        >
          <AjustadorMapa destino={destino} />
          <ObservadorZoom onZoom={setZoom} onCentro={setCentroMapa} />
          <CapaGMW activo={capaGmw} bloqueado={dibujando} mundo={gmwMundo} mexico={gmwMexico}
            pmAnio={pmAnio} pmBase={pmBase} modo={gmwModo}
            onCeldaMexico={elegirCeldaMexico} onCeldaMundo={elegirCeldaMundo} />

          {tipoMapa === 'satelite' ? (
            <TileLayer
              noWrap
              attribution="Tiles &copy; Esri"
              url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            />
          ) : (
            <TileLayer
              noWrap
              attribution="&copy; OpenStreetMap"
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
          )}

          <DibujadorCuadrado dibujando={dibujando} onAreaCompletada={manejarAreaCompletada} />

          {coordenadas && (
            <Rectangle
              bounds={[[coordenadas.lat_min, coordenadas.lon_min], [coordenadas.lat_max, coordenadas.lon_max]]}
              pathOptions={{ color: colorSeleccion, weight: 3, fillColor: colorSeleccion, fillOpacity: 0.18, interactive: false }}
            />
          )}

          {modoSeleccion === 'puntos' &&
            ubicaciones.map((p) => (
              <Marker
                key={p.id}
                position={[p.lat, p.lon]}
                icon={crearIconoManglar(puntoActivoId === p.id)}
                eventHandlers={{ click: () => manejarSeleccionPunto(p) }}
              >
                <Popup>
                  <div style={{ fontFamily: 'Segoe UI, sans-serif', minWidth: '200px' }}>
                    <strong style={{ color: '#162B4D', fontSize: '13px', display: 'block', marginBottom: '4px' }}>
                      🌿 {p.nombre}
                    </strong>
                    <span style={{ fontSize: '12px', color: '#475569', display: 'block' }}>{p.descripcion}</span>
                    {p.caso && (
                      <span style={{ fontSize: '12px', color: '#1A824A', display: 'block', marginTop: '4px' }}>
                        <strong>{CASO_PILOTO[p.caso] || p.caso}</strong>
                      </span>
                    )}
                    <span style={{ fontSize: '11px', color: '#64748B', display: 'block', marginTop: '2px', fontFamily: 'monospace' }}>
                      {p.parametros.fecha_inicio} → {p.parametros.fecha_fin}
                    </span>
                  </div>
                </Popup>
              </Marker>
            ))}
        </MapContainer>
      </div>

      {coordenadas && (
        <div style={{ marginTop: '12px', display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className={`mango-pill ${errorArea ? 'error' : 'ok'}`} style={{ fontFamily: 'monospace' }}>
            [{coordenadas.lon_min}, {coordenadas.lat_min}, {coordenadas.lon_max}, {coordenadas.lat_max}]
          </span>
          <span className={`mango-pill ${errorArea ? 'error' : 'ok'}`}>
            {formatoNumero(areaKm2(coordenadas), 2)} km² · {formatoNumero(areaKm2(coordenadas) * 100, 0)} ha
          </span>
          {coordenadas.tipo_seleccion === 'punto' && (
            <span className="mango-pill ok">Piloto: {coordenadas.zona_manglar}</span>
          )}
        </div>
      )}
      {errorArea && <div className="mango-alerta error" style={{ marginTop: '10px' }}>{errorArea}</div>}
    </section>
  );
}
