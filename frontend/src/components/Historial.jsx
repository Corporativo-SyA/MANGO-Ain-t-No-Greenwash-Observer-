// Consultas guardadas por el backend (GET /api/consultas): abrir, renombrar, descargar y borrar.
import React, { useState } from 'react';

const ESTADO = {
  en_cola: { texto: 'En cola', clase: 'naranja' },
  en_proceso: { texto: 'Analizando', clase: 'naranja' },
  error: { texto: 'Error', clase: 'error' },
};

function etiquetaResultado(c) {
  if (c.estado !== 'completada') return ESTADO[c.estado] || { texto: c.estado, clase: '' };
  if (c.status === 'INSUFFICIENT_DATA') return { texto: 'Faltan datos', clase: 'naranja' };
  if (c.caso === 'CASO_A') return { texto: 'Caso A · conservado', clase: 'ok' };
  if (c.caso === 'CASO_B') return { texto: 'Caso B · quiebre', clase: 'error' };
  if (c.caso === 'CASO_C') return { texto: 'Caso C · sin manglar', clase: '' };
  return { texto: 'Completada', clase: 'ok' };
}

export default function Historial({ consultas, cargando, idActiva, onAbrir, onRenombrar, onEliminar, onDescargar, onRecargar }) {
  const [abierto, setAbierto] = useState(true);
  const [editando, setEditando] = useState(null);
  const [nombre, setNombre] = useState('');
  const [confirmarBorrar, setConfirmarBorrar] = useState(null);

  const guardarNombre = async (id) => {
    const limpio = nombre.trim();
    if (limpio) await onRenombrar(id, limpio);
    setEditando(null);
  };

  return (
    <section className="mango-card" style={{ borderTop: '4px solid #162B4D', padding: '22px 28px' }}>
      <div className="mango-card-head" style={{ marginBottom: abierto ? '14px' : 0 }}>
        <div>
          <h3 className="mango-h3">🗂️ Consultas guardadas</h3>
          <p className="mango-sub">Se guardan en tu computadora. Vuelve a abrirlas al instante, sin descargar nada otra vez.</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button type="button" className="mango-btn-sec" onClick={onRecargar} disabled={cargando}>
            {cargando ? 'Actualizando…' : '↻ Actualizar'}
          </button>
          <button type="button" className="mango-btn-sec" onClick={() => setAbierto(!abierto)}>
            {abierto ? 'Ocultar' : `Mostrar (${consultas.length})`}
          </button>
        </div>
      </div>

      {abierto && (
        consultas.length === 0 ? (
          <p style={{ margin: 0, fontSize: '13px', color: '#64748B' }}>Aún no hay consultas guardadas.</p>
        ) : (
          <div className="mango-historial">
            {consultas.map((c) => {
              const et = etiquetaResultado(c);
              const activa = c.id_consulta === idActiva;
              return (
                <div key={c.id_consulta} className={`mango-fila ${activa ? 'activa' : ''}`}>
                  <div style={{ flex: 1, minWidth: '200px' }}>
                    {editando === c.id_consulta ? (
                      <form onSubmit={(e) => { e.preventDefault(); guardarNombre(c.id_consulta); }} style={{ display: 'flex', gap: '6px' }}>
                        <input
                          autoFocus
                          value={nombre}
                          maxLength={120}
                          onChange={(e) => setNombre(e.target.value)}
                          onKeyDown={(e) => e.key === 'Escape' && setEditando(null)}
                          className="mango-input-mini"
                          aria-label="Nuevo nombre"
                        />
                        <button type="submit" className="mango-btn-sec">Guardar</button>
                      </form>
                    ) : (
                      <strong style={{ color: '#162B4D', fontSize: '14px' }}>{c.nombre}</strong>
                    )}
                    <div style={{ fontSize: '12px', color: '#64748B', fontFamily: 'monospace', marginTop: '2px' }}>
                      {c.fecha_inicio} → {c.fecha_fin} · creada {new Date(c.creada_en).toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' })}
                    </div>
                  </div>
                  <span className={`mango-pill ${et.clase}`}>{et.texto}</span>
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                    <button type="button" className="mango-btn-sec" onClick={() => onAbrir(c.id_consulta)} disabled={activa}>
                      {activa ? 'Abierta' : 'Abrir'}
                    </button>
                    <button type="button" className="mango-btn-sec" onClick={() => { setEditando(c.id_consulta); setNombre(c.nombre); }} title="Renombrar">✎</button>
                    <button type="button" className="mango-btn-sec" onClick={() => onDescargar(c.id_consulta)} disabled={c.estado !== 'completada'} title="Descargar JSON">⬇</button>
                    {confirmarBorrar === c.id_consulta ? (
                      <>
                        <button type="button" className="mango-btn-quitar" onClick={() => { onEliminar(c.id_consulta); setConfirmarBorrar(null); }}>
                          Sí, borrar
                        </button>
                        <button type="button" className="mango-btn-sec" onClick={() => setConfirmarBorrar(null)}>No</button>
                      </>
                    ) : (
                      <button type="button" className="mango-btn-quitar" onClick={() => setConfirmarBorrar(c.id_consulta)} title="Borrar" disabled={c.estado === 'en_proceso' || c.estado === 'en_cola'}>
                        🗑
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}
    </section>
  );
}
