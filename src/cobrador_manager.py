"""Gestor de asignaciones cobrador <-> cliente sobre PostgreSQL (Podman).

La base de datos se levanta con:
    podman compose up -d        (o docker compose up -d)

El esquema se inicializa automáticamente via db/init.sql al crear el contenedor.
Configurar la URL de conexión en .env → COBRADOR_DB_URL.

Flujo de datos:
    Firebird  →  df maestro (CLIENTE_ID + NOMBRE_CLIENTE)
              →  sync_clientes()  →  PostgreSQL (PK = cliente_id)
              →  get_assignments()  →  {nombre_cliente: cobrador}
              →  split por cobrador en reporte_cobrador.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class CobradorManager:
    """Gestiona la tabla de asignaciones cliente → cobrador en PostgreSQL."""

    def __init__(self, db_url: str) -> None:
        """Inicializa el gestor y crea el esquema si no existe.

        Args:
            db_url: URL de conexion SQLAlchemy a PostgreSQL.
                Formato: ``postgresql://usuario:password@host:puerto/db``.
        """
        self.db_url = db_url
        self._engine: Engine = create_engine(
            db_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        self._init_schema()

    def _init_schema(self) -> None:
        """Crea las tablas si no existen; lee el DDL de db/init.sql (idempotente)."""
        sql_path = Path(__file__).parent.parent / "db" / "init.sql"
        ddl = sql_path.read_text(encoding="utf-8")
        sentencias = [s.strip() for s in ddl.split(";") if s.strip()]
        with self._engine.begin() as conn:
            for sentencia in sentencias:
                conn.execute(text(sentencia))

    # ------------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------------

    def sync_clientes(
        self, clientes: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Sincroniza la tabla con la lista exacta de clientes de Firebird.

        - Inserta clientes nuevos con cobrador='PENDIENTE'.
        - Actualiza el nombre si cambio en Firebird (preserva cobrador).
        - Elimina clientes que ya no existen en Firebird (cruce por cliente_id).

        Args:
            clientes: lista de dicts con claves ``cliente_id`` (int) y
                      ``nombre_cliente`` (str), provenientes del master DataFrame.

        Returns:
            ``{"nuevos": N, "eliminados": M, "total": T}``
        """
        if not clientes:
            return {"nuevos": 0, "eliminados": 0, "total": self._count_total()}

        now = datetime.now(tz=timezone.utc).isoformat()
        params = [
            {"cid": int(c["cliente_id"]), "nombre": str(c["nombre_cliente"]), "now": now}
            for c in clientes
        ]
        ids_validos = [p["cid"] for p in params]

        with self._engine.begin() as conn:
            before: int = conn.execute(text("SELECT COUNT(*) FROM asignaciones")).scalar() or 0

            conn.execute(
                text("""
                    INSERT INTO asignaciones (cliente_id, nombre_cliente, cobrador, updated_at)
                    VALUES (:cid, :nombre, 'PENDIENTE', :now)
                    ON CONFLICT (cliente_id) DO UPDATE
                        SET nombre_cliente = EXCLUDED.nombre_cliente,
                            updated_at     = EXCLUDED.updated_at
                """),
                params,
            )
            after_upsert: int = conn.execute(text("SELECT COUNT(*) FROM asignaciones")).scalar() or 0

            # Purga clientes que ya no existen en Firebird (cruce por cliente_id)
            pruned = conn.execute(
                text("DELETE FROM asignaciones WHERE NOT (cliente_id = ANY(:ids))"),
                {"ids": ids_validos},
            )

        nuevos = after_upsert - before
        eliminados = pruned.rowcount
        total = after_upsert - eliminados
        logger.debug(
            "sync_clientes: %d nuevos, %d eliminados / %d total", nuevos, eliminados, total
        )
        return {"nuevos": nuevos, "eliminados": eliminados, "total": total}

    def get_assignments(self) -> dict[str, str]:
        """Retorna ``{nombre_cliente: cobrador}`` para el join en el split de cobradores.

        Si un cliente tiene nombre duplicado en la tabla (edge case: cambio de
        nombre en Firebird) se usa la asignación con mayor cliente_id.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT DISTINCT ON (nombre_cliente)
                        nombre_cliente, cobrador
                    FROM asignaciones
                    ORDER BY nombre_cliente, cliente_id DESC
                """)
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_assignments_by_id(self) -> dict[int, str]:
        """Retorna ``{cliente_id: cobrador}`` para usos que dispongan del ID."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT cliente_id, cobrador FROM asignaciones")
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def update_cobrador(self, nombre_cliente: str, cobrador: str) -> bool:
        """Actualiza el cobrador de un cliente buscando por nombre.

        Args:
            nombre_cliente: Nombre del cliente; se normaliza a mayusculas.
            cobrador: Nuevo cobrador a asignar.

        Returns:
            True si el cliente fue encontrado y actualizado, False si no existe.
        """
        nombre = nombre_cliente.strip().upper()
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE asignaciones SET cobrador = :cobrador"
                    " WHERE nombre_cliente = :nombre"
                ),
                {"cobrador": cobrador.strip(), "nombre": nombre},
            )
        return result.rowcount > 0

    def bulk_update(self, rows: list[dict[str, str]]) -> dict[str, int]:
        """Actualiza multiples asignaciones en una sola transaccion.

        Args:
            rows: Lista de dicts con claves ``nombre_cliente`` y ``cobrador``.

        Returns:
            ``{"actualizados": N, "omitidos": M}`` donde omitidos son los
            clientes no encontrados en la tabla de asignaciones.
        """
        if not rows:
            return {"actualizados": 0, "omitidos": 0}

        actualizados = 0
        omitidos = 0
        with self._engine.begin() as conn:
            for row in rows:
                nombre = str(row["nombre_cliente"]).strip().upper()
                cobrador = str(row["cobrador"]).strip()
                if not nombre or not cobrador:
                    omitidos += 1
                    continue
                result = conn.execute(
                    text(
                        "UPDATE asignaciones SET cobrador = :cobrador"
                        " WHERE nombre_cliente = :nombre"
                    ),
                    {"cobrador": cobrador, "nombre": nombre},
                )
                if result.rowcount > 0:
                    actualizados += 1
                else:
                    omitidos += 1
        return {"actualizados": actualizados, "omitidos": omitidos}

    def list_cobradores(self) -> list[str]:
        """Lista de cobradores únicos presentes en asignaciones (incluye PENDIENTE)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT cobrador FROM asignaciones ORDER BY cobrador")
            ).fetchall()
        return [row[0] for row in rows]

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def delete_cliente(self, nombre_cliente: str) -> bool:
        """Elimina un cliente de la tabla de asignaciones.

        Args:
            nombre_cliente: Nombre del cliente; se normaliza a mayusculas.

        Returns:
            True si el cliente existia y fue eliminado, False si no se encontro.
        """
        nombre = nombre_cliente.strip().upper()
        with self._engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM asignaciones WHERE nombre_cliente = :nombre"),
                {"nombre": nombre},
            )
        return result.rowcount > 0

    def _count_total(self) -> int:
        with self._engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM asignaciones")).scalar() or 0

    def dispose(self) -> None:
        """Libera el pool de conexiones. Útil en tests y shutdown."""
        self._engine.dispose()
