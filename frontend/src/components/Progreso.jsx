// Barra de progreso de una consulta en curso (el backend reporta porcentaje y etapa).
import React, { useEffect, useState } from 'react';

const ETAPAS = [
  { desde: 0, hasta: 6, nombre: 'Clima NASA POWER' },
  { desde: 6, hasta: 9, nombre: 'Global Mangrove Watch' },
  { desde: 9, hasta: 84, nombre: 'Escenas Landsat' },
  { desde: 84, hasta: 90, nombre: 'Zona y quiebres' },
  { desde: 90, hasta: 96, nombre: 'Eventos extremos' },
  { desde: 96, hasta: 100, nombre: 'Secuelas' },
];

const fmt = (s) => {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return m ? `${m} min ${String(r).padStart(2, '0')} s` : `${r} s`;
};

export default function Progreso({ consulta }) {
  const [ahora, setAhora] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setAhora(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const pct = Math.max(0, Math.min(100, consulta?.progreso?.porcentaje ?? 0));
  const etapa = consulta?.progreso?.etapa || (consulta?.estado === 'en_cola' ? 'En cola: esperando a que termine otra consulta' : 'Iniciando…');
  const inicio = Date.parse(consulta?.creada_en || '') || ahora;
  const transcurrido = Math.max(0, (ahora - inicio) / 1000);
  const restante = pct > 9 && pct < 100 ? (transcurrido * (100 - pct)) / pct : null;

  return (
    <div className="mango-progreso" role="status" aria-live="polite">
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px', marginBottom: '8px' }}>
        <strong style={{ color: '#162B4D' }}>🛰️ {etapa}</strong>
        <span style={{ fontFamily: 'monospace', color: '#475569', fontSize: '13px' }}>
          {pct.toFixed(0)}% · {fmt(transcurrido)}
          {restante !== null ? ` · ≈ ${fmt(restante)} restantes` : ''}
        </span>
      </div>
      <div className="mango-barra">
        <div className="mango-barra-relleno" style={{ width: `${pct}%` }} />
      </div>
      <div className="mango-etapas">
        {ETAPAS.map((e) => {
          const estado = pct >= e.hasta ? 'hecha' : pct >= e.desde ? 'activa' : '';
          return (
            <span key={e.nombre} className={`mango-etapa ${estado}`} style={{ flex: Math.max(4, e.hasta - e.desde) }}>
              {estado === 'hecha' ? '✓ ' : ''}{e.nombre}
            </span>
          );
        })}
      </div>
      <p style={{ margin: '10px 0 0 0', fontSize: '12.5px', color: '#64748B', lineHeight: 1.45 }}>
        La primera consulta de una zona descarga su histórico completo (Landsat desde 1984 y clima desde 1981) y es la más
        lenta; las siguientes dentro de la misma zona reutilizan la caché local y solo recalculan el análisis.
        Puedes cerrar esta pestaña; la consulta sigue en tu computadora y queda en <strong>Consultas guardadas</strong>.
      </p>
    </div>
  );
}
