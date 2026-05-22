"""Tests para CobradorManager (PostgreSQL via Podman).

Requiere el contenedor activo:
    podman compose up -d

Si el contenedor no está disponible los tests se saltan automáticamente
(pytest.mark.skip). Usar la variable de entorno TEST_COBRADOR_DB_URL
para apuntar a una base de datos de prueba independiente.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from config.settings import COBRADOR_DB_URL
from src.cobrador_manager import CobradorManager


# ── Tests unitarios (sin PostgreSQL) ──────────────────────────────────────────

def test_init_schema_lee_archivo_init_sql() -> None:
    """S3-B: _init_schema debe leer db/init.sql como unica fuente del DDL."""
    leidos: list[Path] = []
    real_read = Path.read_text

    def spy_read_text(self: Path, **kwargs: object) -> str:
        leidos.append(self)
        return real_read(self, **kwargs)

    mock_engine = MagicMock()
    with patch("src.cobrador_manager.create_engine", return_value=mock_engine):
        with patch.object(Path, "read_text", spy_read_text):
            CobradorManager("postgresql://test")

    assert any(str(p).endswith("init.sql") for p in leidos), (
        f"_init_schema debe leer db/init.sql. Paths leidos: {leidos}"
    )

# ── Fixture de conexión ────────────────────────────────────────────────────

# TEST_COBRADOR_DB_URL permite apuntar a una BD separada. Si no está definida,
# se usa la misma URL del proyecto (lee COBRADOR_DB_PASSWORD de .env).
TEST_DB_URL = os.getenv("TEST_COBRADOR_DB_URL", COBRADOR_DB_URL)


def _db_available() -> bool:
    try:
        eng = create_engine(TEST_DB_URL, pool_pre_ping=True)
        with eng.connect():
            pass
        eng.dispose()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _db_available(),
    reason="PostgreSQL no disponible — ejecuta: podman compose up -d",
)


@pytest.fixture()
def mgr() -> CobradorManager:
    """CobradorManager limpio para cada test (trunca tablas antes)."""
    m = CobradorManager(TEST_DB_URL)
    with m._engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE asignaciones, cobradores RESTART IDENTITY CASCADE"))
    yield m
    m.dispose()


# ── Creación de schema ─────────────────────────────────────────────────────

@requires_pg
def test_schema_created_on_init(mgr: CobradorManager) -> None:
    with mgr._engine.connect() as conn:
        tablas = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
        }
    assert "asignaciones" in tablas
    assert "cobradores" in tablas


# ── sync_clientes ──────────────────────────────────────────────────────────

@requires_pg
def test_new_client_gets_pendiente(mgr: CobradorManager) -> None:
    mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "CLIENTE A"}])
    assignments = mgr.get_assignments()
    assert assignments["CLIENTE A"] == "PENDIENTE"


@requires_pg
def test_sync_stores_both_id_and_name(mgr: CobradorManager) -> None:
    mgr.sync_clientes([{"cliente_id": 42, "nombre_cliente": "EMPRESA XYZ"}])
    with mgr._engine.connect() as conn:
        row = conn.execute(
            text("SELECT cliente_id, nombre_cliente FROM asignaciones WHERE cliente_id = 42")
        ).fetchone()
    assert row is not None
    assert row[0] == 42
    assert row[1] == "EMPRESA XYZ"


@requires_pg
def test_sync_returns_count(mgr: CobradorManager) -> None:
    clientes = [
        {"cliente_id": 1, "nombre_cliente": "A"},
        {"cliente_id": 2, "nombre_cliente": "B"},
    ]
    result = mgr.sync_clientes(clientes)
    assert result["nuevos"] == 2
    assert result["total"] == 2


@requires_pg
def test_existing_cobrador_assignment_preserved(mgr: CobradorManager) -> None:
    mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "CLIENTE A"}])
    with mgr._engine.begin() as conn:
        conn.execute(
            text("UPDATE asignaciones SET cobrador = 'Haidee' WHERE cliente_id = 1")
        )
    # Segundo sync no debe sobreescribir la asignación
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "CLIENTE A"},
        {"cliente_id": 2, "nombre_cliente": "CLIENTE B"},
    ])
    assignments = mgr.get_assignments()
    assert assignments["CLIENTE A"] == "Haidee"
    assert assignments["CLIENTE B"] == "PENDIENTE"


@requires_pg
def test_sync_updates_nombre_if_changed_in_firebird(mgr: CobradorManager) -> None:
    """Si el nombre del cliente cambia en Firebird, se actualiza en PostgreSQL."""
    mgr.sync_clientes([{"cliente_id": 5, "nombre_cliente": "NOMBRE VIEJO"}])
    mgr.sync_clientes([{"cliente_id": 5, "nombre_cliente": "NOMBRE NUEVO"}])
    with mgr._engine.connect() as conn:
        row = conn.execute(
            text("SELECT nombre_cliente FROM asignaciones WHERE cliente_id = 5")
        ).fetchone()
    assert row[0] == "NOMBRE NUEVO"


@requires_pg
def test_sync_is_idempotent(mgr: CobradorManager) -> None:
    clientes = [{"cliente_id": 1, "nombre_cliente": "A"}]
    mgr.sync_clientes(clientes)
    result = mgr.sync_clientes(clientes)
    assert result["nuevos"] == 0
    assert result["total"] == 1


@requires_pg
def test_sync_empty_list(mgr: CobradorManager) -> None:
    result = mgr.sync_clientes([])
    assert result["nuevos"] == 0
    assert result["total"] == 0


@requires_pg
def test_sync_elimina_clientes_obsoletos(mgr: CobradorManager) -> None:
    """Clientes que desaparecen de Firebird deben eliminarse de la DB local."""
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "CLIENTE A"},
        {"cliente_id": 2, "nombre_cliente": "CLIENTE B"},
    ])
    # Segunda sincronizacion: CLIENTE B ya no existe en Firebird
    result = mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "CLIENTE A"}])
    assert result["eliminados"] == 1
    assert result["total"] == 1
    assignments = mgr.get_assignments()
    assert "CLIENTE A" in assignments
    assert "CLIENTE B" not in assignments


@requires_pg
def test_sync_preserva_cobrador_en_clientes_que_quedan(mgr: CobradorManager) -> None:
    """La asignacion de cobrador debe conservarse en clientes que siguen en Firebird."""
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "CLIENTE A"},
        {"cliente_id": 2, "nombre_cliente": "CLIENTE B"},
    ])
    mgr.update_cobrador("CLIENTE A", "HAIDEE")
    # Re-sync con ambos clientes: cobrador de A debe preservarse
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "CLIENTE A"},
        {"cliente_id": 2, "nombre_cliente": "CLIENTE B"},
    ])
    assignments = mgr.get_assignments()
    assert assignments["CLIENTE A"] == "HAIDEE"
    assert assignments["CLIENTE B"] == "PENDIENTE"


@requires_pg
def test_sync_retorna_clave_eliminados(mgr: CobradorManager) -> None:
    """sync_clientes siempre debe retornar la clave 'eliminados' en el resultado."""
    result = mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "CLIENTE A"}])
    assert "eliminados" in result


# ── get_assignments ────────────────────────────────────────────────────────

@requires_pg
def test_get_assignments_returns_nombre_cobrador_map(mgr: CobradorManager) -> None:
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "A"},
        {"cliente_id": 2, "nombre_cliente": "B"},
        {"cliente_id": 3, "nombre_cliente": "C"},
    ])
    assignments = mgr.get_assignments()
    assert len(assignments) == 3
    assert all(v == "PENDIENTE" for v in assignments.values())
    assert set(assignments.keys()) == {"A", "B", "C"}


@requires_pg
def test_get_assignments_by_id(mgr: CobradorManager) -> None:
    mgr.sync_clientes([{"cliente_id": 10, "nombre_cliente": "EMPRESA"}])
    by_id = mgr.get_assignments_by_id()
    assert by_id[10] == "PENDIENTE"


# ── update_cobrador ───────────────────────────────────────────────────────

@requires_pg
def test_update_cobrador_retorna_true_si_existe(mgr: CobradorManager) -> None:
    """update_cobrador devuelve True cuando el cliente existe y fue actualizado."""
    mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "EMPRESA A"}])
    resultado = mgr.update_cobrador("EMPRESA A", "Haidee")
    assert resultado is True
    assert mgr.get_assignments()["EMPRESA A"] == "Haidee"


@requires_pg
def test_update_cobrador_retorna_false_si_no_existe(mgr: CobradorManager) -> None:
    """update_cobrador devuelve False cuando el cliente no existe en la DB."""
    resultado = mgr.update_cobrador("NO_EXISTE", "Haidee")
    assert resultado is False


@requires_pg
def test_update_cobrador_normaliza_nombre(mgr: CobradorManager) -> None:
    """update_cobrador normaliza nombre a mayusculas y elimina espacios extremos."""
    mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "EMPRESA B"}])
    mgr.update_cobrador("  empresa b  ", "Jovanna")
    assert mgr.get_assignments()["EMPRESA B"] == "Jovanna"


# ── bulk_update ────────────────────────────────────────────────────────────

@requires_pg
def test_bulk_update_retorna_conteos(mgr: CobradorManager) -> None:
    """bulk_update devuelve actualizados y omitidos correctamente."""
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "A"},
        {"cliente_id": 2, "nombre_cliente": "B"},
    ])
    resultado = mgr.bulk_update([
        {"nombre_cliente": "A", "cobrador": "Haidee"},
        {"nombre_cliente": "NO_EXISTE", "cobrador": "Jovanna"},
    ])
    assert resultado["actualizados"] == 1
    assert resultado["omitidos"] == 1


@requires_pg
def test_bulk_update_aplica_todos_los_cambios(mgr: CobradorManager) -> None:
    """bulk_update actualiza todos los clientes encontrados en una sola transaccion."""
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "A"},
        {"cliente_id": 2, "nombre_cliente": "B"},
    ])
    mgr.bulk_update([
        {"nombre_cliente": "A", "cobrador": "Haidee"},
        {"nombre_cliente": "B", "cobrador": "Jovanna"},
    ])
    assignments = mgr.get_assignments()
    assert assignments["A"] == "Haidee"
    assert assignments["B"] == "Jovanna"


@requires_pg
def test_bulk_update_vacio_retorna_ceros(mgr: CobradorManager) -> None:
    """bulk_update con lista vacia devuelve ceros sin error."""
    resultado = mgr.bulk_update([])
    assert resultado == {"actualizados": 0, "omitidos": 0}


# ── list_cobradores ────────────────────────────────────────────────────────

@requires_pg
def test_list_cobradores_includes_pendiente(mgr: CobradorManager) -> None:
    mgr.sync_clientes([{"cliente_id": 1, "nombre_cliente": "A"}])
    assert "PENDIENTE" in mgr.list_cobradores()


@requires_pg
def test_list_cobradores_after_real_assignment(mgr: CobradorManager) -> None:
    mgr.sync_clientes([
        {"cliente_id": 1, "nombre_cliente": "A"},
        {"cliente_id": 2, "nombre_cliente": "B"},
    ])
    with mgr._engine.begin() as conn:
        conn.execute(
            text("UPDATE asignaciones SET cobrador = 'Jovanna' WHERE cliente_id = 1")
        )
    cobradores = mgr.list_cobradores()
    assert "Jovanna" in cobradores
    assert "PENDIENTE" in cobradores
