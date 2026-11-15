// Paso 1: rango de la consulta. El análisis de MANGO es mensual, así que el rango se captura en meses
// (AAAA-MM), que es exactamente lo que recibe el backend (contrato v2).
import React, { useState } from 'react';
import {
  MES_MINIMO, MESES_MINIMOS, PATRON_MES, mesActual, mesesEntre, sumarMeses, validarRango, etiquetaMes,
} from '../lib/reglas';

const ATAJOS = [
  { etiqueta: 'Últimos 5 años', meses: 60 },
  { etiqueta: 'Últimos 10 años', meses: 120 },
  { etiqueta: 'Últimos 20 años', meses: 240 },
];

function CampoMes({ etiqueta, valor, onCambio, guardado, ejemplo }) {
  const valido = PATRON_MES.test(valor);
  return (
    <div>
      <label className="mango-label">{etiqueta}</label>
      <div style={{ display: 'flex', alignItems: 'center', position: 'relative', width: '100%' }}>
        <input
          type="text"
          inputMode="numeric"
          placeholder={`AAAA-MM (Ej. ${ejemplo})`}
          value={valor}
          onChange={(e) => onCambio(e.target.value.replace(/[^0-9-]/g, '').slice(0, 7))}
          aria-label={etiqueta}
          style={{
            width: '100%',
            padding: '12px 48px 12px 14px',
            borderRadius: '10px',
            border: `1.5px solid ${guardado ? '#1A824A' : '#CBD5E1'}`,
            fontFamily: 'monospace',
            fontSize: '15px',
            color: '#162B4D',
            backgroundColor: '#FFFDF9',
          }}
        />
        <div className="mango-calendario" title="Abrir calendario (año / mes)">
          <span style={{ fontSize: '17px', pointerEvents: 'none' }}>📅</span>
          <input
            type="month"
            min={MES_MINIMO}
            max={mesActual()}
            value={valido ? valor : ''}
            onChange={(e) => onCambio(e.target.value)}
            aria-label={`${etiqueta} (calendario)`}
          />
        </div>
      </div>
      <div style={{ fontSize: '12px', color: '#64748B', marginTop: '6px', minHeight: '16px' }}>
        {valido ? etiquetaMes(valor) : ''}
      </div>
    </div>
  );
}

export default function FiltrosFechas({ fechaInicio, setFechaInicio, fechaFin, setFechaFin, fechasGuardadas, onGuardarFechas }) {
  const [errorValidacion, setErrorValidacion] = useState('');

  const cambiar = (setter) => (valor) => {
    setter(valor);
    if (errorValidacion) setErrorValidacion('');
    // Si el usuario modifica un rango ya guardado, debe volver a guardarlo.
    if (fechasGuardadas) onGuardarFechas(null);
  };

  const aplicarAtajo = (meses) => {
    const fin = sumarMeses(mesActual(), -1); // último mes completo
    cambiar(setFechaInicio)(sumarMeses(fin, -(meses - 1)));
    setFechaFin(fin);
  };

  const manejarGuardar = (e) => {
    e.preventDefault();
    const error = validarRango(fechaInicio, fechaFin);
    if (error) {
      setErrorValidacion(error);
      return;
    }
    setErrorValidacion('');
    onGuardarFechas({ fecha_inicio: fechaInicio, fecha_fin: fechaFin });
  };

  const ambosValidos = PATRON_MES.test(fechaInicio) && PATRON_MES.test(fechaFin) && fechaInicio <= fechaFin;
  const nMeses = ambosValidos ? mesesEntre(fechaInicio, fechaFin) : null;

  return (
    <section className="mango-card" style={{ borderTop: '4px solid #F37021', padding: '24px 28px' }}>
      <div className="mango-card-head">
        <div>
          <h3 className="mango-h3">
            <span className="mango-paso">1</span> Rango de la consulta
          </h3>
          <p className="mango-sub">
            MANGO analiza mes a mes. Escribe el mes o elígelo en el calendario. Formato:{' '}
            <strong style={{ color: '#1A824A', fontFamily: 'monospace' }}>AAAA-MM</strong> · desde {MES_MINIMO}.
            Puede ser un solo mes: el análisis usa todo el histórico de la zona y aquí solo eliges qué ver.
          </p>
        </div>
        <span className={`mango-pill ${fechasGuardadas ? 'ok' : ''}`} style={{ fontFamily: fechasGuardadas ? 'monospace' : 'inherit' }}>
          {fechasGuardadas ? `Guardado: ${fechasGuardadas.fecha_inicio} → ${fechasGuardadas.fecha_fin}` : 'Fechas sin guardar'}
        </span>
      </div>

      <form onSubmit={manejarGuardar} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', fontWeight: 700, color: '#475569' }}>Atajos:</span>
          {ATAJOS.map((a) => (
            <button key={a.meses} type="button" className="mango-chip" onClick={() => aplicarAtajo(a.meses)}>
              {a.etiqueta}
            </button>
          ))}
          <span style={{ fontSize: '12px', color: '#64748B' }}>
            La línea base siempre es el histórico completo de la zona.
          </span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '24px' }}>
          <CampoMes etiqueta="Mes de inicio" valor={fechaInicio} onCambio={cambiar(setFechaInicio)} guardado={!!fechasGuardadas} ejemplo="2005-01" />
          <CampoMes etiqueta="Mes de fin" valor={fechaFin} onCambio={cambiar(setFechaFin)} guardado={!!fechasGuardadas} ejemplo="2024-12" />
        </div>

        {nMeses !== null && (
          <div style={{ fontSize: '13px', color: nMeses >= MESES_MINIMOS ? '#1A824A' : '#D94136', fontWeight: 600 }}>
            {nMeses} meses ({(nMeses / 12).toLocaleString('es-MX', { maximumFractionDigits: 1 })} años)
            {nMeses < MESES_MINIMOS ? ` · faltan ${MESES_MINIMOS - nMeses} para el mínimo` : ' · rango válido'}
          </div>
        )}

        {errorValidacion && <div className="mango-alerta error">{errorValidacion}</div>}

        <button
          type="submit"
          className="mango-btn-ancho"
          style={{
            background: fechasGuardadas
              ? 'linear-gradient(135deg, #1A824A 0%, #156B3D 100%)'
              : 'linear-gradient(135deg, #F37021 0%, #E85D1A 100%)',
            boxShadow: fechasGuardadas ? '0 4px 12px rgba(26, 130, 74, 0.25)' : '0 4px 12px rgba(243, 112, 33, 0.25)',
          }}
        >
          {fechasGuardadas ? '✓ Fechas seleccionadas correctamente' : 'Seleccionar fechas'}
        </button>
      </form>
    </section>
  );
}
