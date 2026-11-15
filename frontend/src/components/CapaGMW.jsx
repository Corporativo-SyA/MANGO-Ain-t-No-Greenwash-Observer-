// Capa de manglares GMW dibujada en un canvas propio (22-24 mil celdas sin crear miles de capas Leaflet).
// Zoom < 8: mundo 2025 (celdas de 0.1°). Zoom >= 8 dentro de México: 1985-2025 (celdas de 0.01°).
import { useEffect, useMemo, useRef } from 'react';
import L from 'leaflet';
import { useMap } from 'react-leaflet';
import { ATRIBUCION_GMW, LIMITES_MEXICO } from '../lib/gmw';

export const ZOOM_MEXICO = 8;

function colorExtension(pm) {
  const a = 0.18 + 0.72 * Math.min(1, pm / 500);
  return `rgba(26,130,74,${a.toFixed(3)})`;
}

function colorCambio(delta) {
  // delta en por mil del área de la celda: pérdida = coral, ganancia = verde; saturación a ±200 ‰
  const a = 0.15 + 0.75 * Math.min(1, Math.abs(delta) / 200);
  return delta < 0 ? `rgba(217,65,54,${a.toFixed(3)})` : `rgba(26,130,74,${a.toFixed(3)})`;
}

export default function CapaGMW({ mundo, mexico, pmAnio, pmBase, modo, onCeldaMexico, onCeldaMundo, activo, bloqueado }) {
  const map = useMap();
  const canvasRef = useRef(null);

  const indiceMexico = useMemo(() => {
    if (!mexico) return null;
    const m = new Map();
    for (let c = 0; c < mexico.lat.length; c += 1) m.set(`${mexico.lat[c]},${mexico.lon[c]}`, c);
    return m;
  }, [mexico]);

  useEffect(() => {
    if (!activo) return undefined;
    const canvas = L.DomUtil.create('canvas', 'mango-capa-gmw');
    canvas.style.pointerEvents = 'none';
    map.getPanes().overlayPane.appendChild(canvas);
    canvasRef.current = canvas;
    map.attributionControl.addAttribution(ATRIBUCION_GMW);

    const usarMexico = () => {
      const b = map.getBounds();
      return mexico && pmAnio && map.getZoom() >= ZOOM_MEXICO
        && b.getEast() > LIMITES_MEXICO.oeste && b.getWest() < LIMITES_MEXICO.este
        && b.getNorth() > LIMITES_MEXICO.sur && b.getSouth() < LIMITES_MEXICO.norte;
    };

    const dibujar = () => {
      const size = map.getSize();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = size.x * dpr;
      canvas.height = size.y * dpr;
      canvas.style.width = `${size.x}px`;
      canvas.style.height = `${size.y}px`;
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.x, size.y);
      const b = map.getBounds().pad(0.05);
      const esMx = usarMexico();
      const datos = esMx ? mexico : mundo;
      if (!datos) return;
      const e = datos.escala;
      const cel = datos.celda;
      for (let c = 0; c < datos.lat.length; c += 1) {
        const la = datos.lat[c] / e;
        const lo = datos.lon[c] / e;
        if (la > b.getNorth() || la + cel < b.getSouth() || lo > b.getEast() || lo + cel < b.getWest()) continue;
        let color;
        if (esMx) {
          const v = pmAnio[c];
          if (modo === 'cambio') {
            const d = v - pmBase[c];
            if (d === 0) continue;
            color = colorCambio(d);
          } else {
            if (v === 0) continue;
            color = colorExtension(v);
          }
        } else {
          const pct = datos.pct[c] / 10;
          if (pct <= 0) continue;
          color = colorExtension(pct * 10);
        }
        const p0 = map.latLngToContainerPoint([la + cel, lo]);
        const p1 = map.latLngToContainerPoint([la, lo + cel]);
        ctx.fillStyle = color;
        ctx.fillRect(p0.x, p0.y, Math.max(1, p1.x - p0.x), Math.max(1, p1.y - p0.y));
      }
    };

    const ocultar = () => { canvas.style.visibility = 'hidden'; };
    const mostrar = () => { canvas.style.visibility = 'visible'; dibujar(); };
    const clic = (ev) => {
      if (bloqueado) return; // el usuario está dibujando un rectángulo
      if (usarMexico()) {
        const k = `${Math.floor(ev.latlng.lat * 100)},${Math.floor(ev.latlng.lng * 100)}`;
        const c = indiceMexico?.get(k);
        if (c !== undefined) onCeldaMexico?.(mexico.lat[c] / 100, mexico.lon[c] / 100);
      } else if (mundo) {
        const la = Math.floor(ev.latlng.lat * 10) / 10;
        const lo = Math.floor(ev.latlng.lng * 10) / 10;
        onCeldaMundo?.(la, lo);
      }
    };
    dibujar();
    map.on('moveend zoomend resize', mostrar);
    map.on('zoomstart', ocultar);
    map.on('click', clic);
    return () => {
      map.off('moveend zoomend resize', mostrar);
      map.off('zoomstart', ocultar);
      map.off('click', clic);
      map.attributionControl.removeAttribution(ATRIBUCION_GMW);
      canvas.remove();
    };
  }, [map, activo, bloqueado, mundo, mexico, pmAnio, pmBase, modo, indiceMexico, onCeldaMexico, onCeldaMundo]);

  return null;
}
