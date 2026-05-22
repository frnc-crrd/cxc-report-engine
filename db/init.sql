-- Schema de asignaciones cobrador <-> cliente para CxC Report Engine.
-- Ejecutado automáticamente por PostgreSQL al inicializar el contenedor.
-- PK: cliente_id (INTEGER proveniente de Microsip/Firebird).

CREATE TABLE IF NOT EXISTS cobradores (
    id     SERIAL PRIMARY KEY,
    nombre TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS asignaciones (
    cliente_id     INTEGER     PRIMARY KEY,           -- ID de Firebird (estable)
    nombre_cliente TEXT        NOT NULL,              -- Nombre legible (puede cambiar)
    cobrador       TEXT        NOT NULL DEFAULT 'PENDIENTE',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para filtros frecuentes
CREATE INDEX IF NOT EXISTS idx_asignaciones_cobrador      ON asignaciones (cobrador);
CREATE INDEX IF NOT EXISTS idx_asignaciones_nombre_cliente ON asignaciones (nombre_cliente);
