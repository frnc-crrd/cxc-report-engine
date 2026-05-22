"""Tests unitarios para src/db_connector.py.

No requiere Firebird ni PostgreSQL. Usa mocks del context manager connect().
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.db_connector import FirebirdConnector


# ======================================================================
# FIXTURES
# ======================================================================

@pytest.fixture
def connector() -> FirebirdConnector:
    """FirebirdConnector sin driver real para pruebas unitarias."""
    obj = FirebirdConnector.__new__(FirebirdConnector)
    obj.config = {"database": "test.fdb", "password": "test"}
    obj._driver = "fdb"
    return obj


def _mock_connect(filas: list[tuple], columnas: list[tuple[str]]):
    """Fabrica de context-manager mock para simular una conexion Firebird.

    Args:
        filas: Rows que devolvera cursor.fetchall().
        columnas: Tuples (nombre,) que simulan cursor.description.

    Returns:
        Funcion decorada con @contextmanager que yielda una conexion mock.
    """
    @contextmanager
    def _connect():
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = columnas
        mock_cursor.fetchall.return_value = filas
        mock_conn.cursor.return_value = mock_cursor
        yield mock_conn

    return _connect


# ======================================================================
# TESTS — extract_tables_batch (TDD S2-B)
# ======================================================================

def test_extract_tables_batch_usa_exactamente_una_conexion(connector: FirebirdConnector) -> None:
    """TDD S2-B: extract_tables_batch debe abrir una sola conexion para N tablas.

    Con el enfoque actual (10 llamadas a extract_table), se abren 10 conexiones.
    Tras implementar extract_tables_batch, debe usarse exactamente 1.
    """
    llamadas_connect: list[int] = []

    @contextmanager
    def connect_spy():
        llamadas_connect.append(1)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("COL1",), ("COL2",)]
        mock_cursor.fetchall.return_value = [(1, "A"), (2, "B")]
        mock_conn.cursor.return_value = mock_cursor
        yield mock_conn

    connector.connect = connect_spy

    tablas = {
        "TABLA_A": ["COL1", "COL2"],
        "TABLA_B": ["COL1", "COL2"],
        "TABLA_C": ["COL1", "COL2"],
    }

    result = connector.extract_tables_batch(tablas)

    assert len(llamadas_connect) == 1, (
        f"Se esperaba exactamente 1 conexion, se abrieron {len(llamadas_connect)}"
    )
    assert set(result.keys()) == {"TABLA_A", "TABLA_B", "TABLA_C"}


def test_extract_tables_batch_retorna_dataframes_con_columnas_correctas(connector: FirebirdConnector) -> None:
    """extract_tables_batch retorna un DataFrame por tabla con las columnas correctas."""
    connector.connect = _mock_connect(
        filas=[(101, "EMPRESA A")],
        columnas=[("CLIENTE_ID",), ("NOMBRE",)],
    )

    tablas = {"CLIENTES": ["CLIENTE_ID", "NOMBRE"], "VENDEDORES": ["CLIENTE_ID", "NOMBRE"]}
    result = connector.extract_tables_batch(tablas)

    assert "CLIENTES" in result
    assert "VENDEDORES" in result
    assert list(result["CLIENTES"].columns) == ["CLIENTE_ID", "NOMBRE"]
    assert len(result["CLIENTES"]) == 1
    assert result["CLIENTES"]["CLIENTE_ID"].iloc[0] == 101


def test_extract_tables_batch_dict_vacio_retorna_dict_vacio(connector: FirebirdConnector) -> None:
    """extract_tables_batch con un dict vacio abre conexion y retorna dict vacio."""
    llamadas: list[int] = []

    @contextmanager
    def connect_spy():
        llamadas.append(1)
        yield MagicMock()

    connector.connect = connect_spy

    result = connector.extract_tables_batch({})

    assert result == {}
    assert len(llamadas) == 1, "Debe abrir conexion incluso con dict vacio"


# ======================================================================
# TESTS — retries con tenacity (TDD S4-B)
# ======================================================================

def test_connect_reintenta_3_veces_si_siempre_falla(
    connector: FirebirdConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4-B: connect() debe reintentar la conexion 3 veces antes de lanzar excepcion."""
    monkeypatch.setattr(time, "sleep", lambda *args: None)

    intentos = [0]

    def siempre_falla() -> None:
        intentos[0] += 1
        raise ConnectionRefusedError("conexion rechazada en prueba")

    monkeypatch.setattr(connector, "_establecer_conexion", siempre_falla)

    with pytest.raises(Exception):
        with connector.connect():
            pass

    assert intentos[0] == 3, f"Se esperaban 3 intentos, se hicieron {intentos[0]}"


def test_connect_tiene_exito_en_segundo_intento(
    connector: FirebirdConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4-B: connect() debe retornar la conexion si tiene exito antes del tercer intento."""
    monkeypatch.setattr(time, "sleep", lambda *args: None)

    intentos = [0]
    mock_conn = MagicMock()

    def falla_una_vez():
        intentos[0] += 1
        if intentos[0] == 1:
            raise ConnectionRefusedError("fallo transitorio")
        return mock_conn

    monkeypatch.setattr(connector, "_establecer_conexion", falla_una_vez)

    with connector.connect() as conn:
        assert conn is mock_conn

    assert intentos[0] == 2, f"Se esperaban 2 intentos, se hicieron {intentos[0]}"


def test_connect_sin_reintentos_si_exitoso(
    connector: FirebirdConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4-B: connect() no debe reintentar si la conexion tiene exito al primer intento."""
    monkeypatch.setattr(time, "sleep", lambda *args: None)

    intentos = [0]
    mock_conn = MagicMock()

    def conectar():
        intentos[0] += 1
        return mock_conn

    monkeypatch.setattr(connector, "_establecer_conexion", conectar)

    with connector.connect() as conn:
        assert conn is mock_conn

    assert intentos[0] == 1, f"Se esperaba 1 intento, se hicieron {intentos[0]}"


def test_extract_table_sigue_funcionando_tras_refactor(connector: FirebirdConnector) -> None:
    """Regresion S2-B: extract_table (API existente) no debe romperse."""
    connector.connect = _mock_connect(
        filas=[(1, "TIPO1")],
        columnas=[("TIPO_CLIENTE_ID",), ("NOMBRE",)],
    )
    df = connector.extract_table("TIPOS_CLIENTES", ["TIPO_CLIENTE_ID", "NOMBRE"])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
