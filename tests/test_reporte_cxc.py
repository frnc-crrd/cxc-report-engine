"""Tests para src/reporte_cxc.py y src/data_transformer.py.

No requiere Firebird ni PostgreSQL.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.data_transformer import DataTransformer
from src.reporte_cxc import generar_reporte_cxc


# ======================================================================
# FIXTURES
# ======================================================================

@pytest.fixture
def df_transaccional() -> pd.DataFrame:
    """DataFrame sintetico minimo: un cargo abierto, uno pagado con abono, un anticipo."""
    return pd.DataFrame({
        "DOCTO_CC_ID":          [1,       2,       3,       4],
        "DOCTO_CC_ACR_ID":      [None,    None,    2,       None],
        "IMPTE_DOCTO_CC_ID":    [10,      20,      30,      40],
        "ANTICIPO_CC_ID":       [None,    None,    None,    None],
        "CLIENTE_ID":           [101,     101,     101,     101],
        "TIPO_CLIENTE_ID":      [1,       1,       1,       1],
        "VENDEDOR_ID":          [1,       1,       1,       1],
        "MONEDA_ID":            [1,       1,       1,       1],
        "COND_PAGO_ID":         [1,       1,       1,       None],
        "CONCEPTO_CC_ID":       [1,       1,       2,       3],
        "NOMBRE_CLIENTE":       ["EMPRESA A"] * 4,
        "TIPO_CLIENTE":         ["TIPO1"] * 4,
        "MONEDA":               ["MXN"] * 4,
        "CONDICIONES":          ["30D", "30D", "30D", None],
        "VENDEDOR":             ["VENDEDOR1"] * 4,
        "ESTATUS_CLIENTE":      ["A"] * 4,
        "LIMITE_CREDITO":       [50000.0] * 4,
        "CONCEPTO":             ["VENTA FACT", "VENTA FACT", "COBRO", "ANTICIPO"],
        "FOLIO":                ["F001", "F002", "R001", "A001"],
        "FECHA_EMISION":        pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-10", "2024-01-02"]),
        "FECHA_VENCIMIENTO":    pd.to_datetime(["2024-01-31", "2024-01-31", pd.NaT, pd.NaT]),
        "HORA":                 [None] * 4,
        "SISTEMA_ORIGEN":       [None] * 4,
        "NATURALEZA_CONCEPTO":  ["C", "C", "R", "C"],
        "CANCELADO":            ["N", "N", "N", "N"],
        "APLICADO":             ["N", "N", "N", "N"],
        "DESCRIPCION":          ["Factura 1", "Factura 2", "Cobro F2", "Anticipo"],
        "TIPO_USO_ANTICIPO":    [None] * 4,
        "CARGOS":               [1000.0, 500.0, 0.0,   0.0],
        "ABONOS":               [0.0,    0.0,   500.0, 0.0],
        "TIPO_IMPTE":           ["C",    "C",   "R",   "A"],
        "IMPORTE":              [1000.0, 500.0, 500.0, 200.0],
        "IMPUESTO":             [160.0,  80.0,  80.0,  32.0],
        "USUARIO_CREADOR":      [None] * 4,
        "FECHA_HORA_CREACION":  [None] * 4,
        "USUARIO_ULT_MODIF":    [None] * 4,
        "FECHA_HORA_ULT_MODIF": [None] * 4,
        "USUARIO_CANCELACION":  [None] * 4,
        "FECHA_HORA_CANCELACION": [None] * 4,
    })


# ======================================================================
# TESTS — ESTRUCTURA DE SALIDA
# ======================================================================

def test_generar_reporte_cxc_retorna_claves_esperadas(df_transaccional: pd.DataFrame) -> None:
    """generar_reporte_cxc debe retornar exactamente las 5 claves del contrato."""
    resultado = generar_reporte_cxc(df_transaccional)
    esperadas = {
        "reporte_cxc",
        "por_acreditar",
        "movimientos_abiertos_cxc",
        "movimientos_cerrados_cxc",
        "movimientos_totales_cxc",
    }
    assert set(resultado.keys()) == esperadas


def test_generar_reporte_cxc_retorna_dataframes(df_transaccional: pd.DataFrame) -> None:
    """Todos los valores del resultado deben ser DataFrames."""
    resultado = generar_reporte_cxc(df_transaccional)
    for nombre, df in resultado.items():
        assert isinstance(df, pd.DataFrame), f"'{nombre}' no es un DataFrame"


# ======================================================================
# TESTS — CORRECTITUD DE CALCULOS
# ======================================================================

def test_saldo_factura_cargo_abierto(df_transaccional: pd.DataFrame) -> None:
    """El cargo F001 (sin abono) debe tener SALDO_FACTURA = 1000 + 160 = 1160."""
    resultado = generar_reporte_cxc(df_transaccional)
    abiertos = resultado["movimientos_abiertos_cxc"]
    assert not abiertos.empty, "Debe haber al menos un cargo abierto"
    fila = abiertos[abiertos["FOLIO"] == "F001"]
    assert not fila.empty, "El cargo F001 debe aparecer en movimientos_abiertos_cxc"
    saldo = fila["SALDO_FACTURA"].iloc[0]
    assert abs(saldo - 1160.0) < 1e-6, f"SALDO_FACTURA esperado 1160.0, got {saldo}"


def test_saldo_factura_cargo_pagado(df_transaccional: pd.DataFrame) -> None:
    """El cargo F002 con abono de 580 debe aparecer en movimientos_cerrados_cxc."""
    resultado = generar_reporte_cxc(df_transaccional)
    cerrados = resultado["movimientos_cerrados_cxc"]
    assert not cerrados.empty, "Debe haber al menos un cargo cerrado"
    fila = cerrados[cerrados["FOLIO"] == "F002"]
    assert not fila.empty, "El cargo F002 debe aparecer en movimientos_cerrados_cxc"


def test_anticipo_va_a_por_acreditar(df_transaccional: pd.DataFrame) -> None:
    """Los movimientos de TIPO_IMPTE='A' deben ir a por_acreditar y no al reporte."""
    resultado = generar_reporte_cxc(df_transaccional)
    por_acreditar = resultado["por_acreditar"]
    reporte = resultado["reporte_cxc"]
    assert not por_acreditar.empty, "por_acreditar no debe estar vacio"
    if "FOLIO" in reporte.columns:
        assert "A001" not in reporte["FOLIO"].values, "A001 no debe estar en reporte_cxc"


def test_movimientos_totales_contiene_zscores(df_transaccional: pd.DataFrame) -> None:
    """movimientos_totales_cxc debe incluir columnas ZSCORE calculadas."""
    resultado = generar_reporte_cxc(df_transaccional)
    totales = resultado["movimientos_totales_cxc"]
    cols = totales.columns.tolist()
    zscore_cols = [c for c in cols if "ZSCORE" in c.upper() or "ATIPICO" in c.upper()]
    assert len(zscore_cols) > 0, "movimientos_totales_cxc debe tener columnas ZSCORE/ATIPICO"


# ======================================================================
# TESTS — INVARIANTES DE MUTACION (S2-A regresion)
# ======================================================================

def test_df_entrada_no_es_mutado(df_transaccional: pd.DataFrame) -> None:
    """TDD S2-A: generar_reporte_cxc no debe modificar el DataFrame de entrada.

    Este test verifica que la eliminacion de copias defensivas no introduce
    mutaciones sobre el df original.
    """
    columnas_originales = list(df_transaccional.columns)
    forma_original = df_transaccional.shape
    copia_referencia = df_transaccional.copy()

    generar_reporte_cxc(df_transaccional)

    assert list(df_transaccional.columns) == columnas_originales, (
        "Las columnas del df de entrada fueron modificadas"
    )
    assert df_transaccional.shape == forma_original, (
        "La forma del df de entrada fue modificada"
    )
    pd.testing.assert_frame_equal(
        df_transaccional.reset_index(drop=True),
        copia_referencia.reset_index(drop=True),
        check_like=False,
        obj="df_transaccional despues de generar_reporte_cxc",
    )


# ======================================================================
# TESTS — DataTransformer.get_all_clientes (S3-C regresion)
# ======================================================================

def test_get_all_clientes_retorna_lista_de_dicts() -> None:
    """get_all_clientes debe retornar lista de dicts con cliente_id int y nombre_cliente str."""
    df_clientes = pd.DataFrame({
        "CLIENTE_ID": [1.0, 2.0, None, 3.0],
        "NOMBRE":     ["  empresa a  ", "EMPRESA B", "SIN_ID", None],
    })
    mock_connector = MagicMock()
    mock_connector.extract_table.return_value = df_clientes

    resultado = DataTransformer(mock_connector).get_all_clientes()

    assert isinstance(resultado, list)
    assert len(resultado) == 2
    assert resultado[0] == {"cliente_id": 1, "nombre_cliente": "EMPRESA A"}
    assert resultado[1] == {"cliente_id": 2, "nombre_cliente": "EMPRESA B"}
    assert isinstance(resultado[0]["cliente_id"], int)
    assert isinstance(resultado[0]["nombre_cliente"], str)


def test_get_all_clientes_excluye_filas_con_nan() -> None:
    """Filas con CLIENTE_ID o NOMBRE nulos deben quedar excluidas del resultado."""
    df_clientes = pd.DataFrame({
        "CLIENTE_ID": [None, 5.0, "no_numerico"],
        "NOMBRE":     ["CLIENTE X", None, "CLIENTE Z"],
    })
    mock_connector = MagicMock()
    mock_connector.extract_table.return_value = df_clientes

    resultado = DataTransformer(mock_connector).get_all_clientes()

    assert resultado == [], f"Se esperaba lista vacia, se obtuvo {resultado}"


@pytest.fixture
def df_multiples_facturas_abiertas() -> pd.DataFrame:
    """6 facturas abiertas con vencimientos variados para disparar calculo de z-score."""
    hoy = pd.Timestamp.now().normalize()
    n = 6
    vencimientos = [hoy - pd.Timedelta(days=d) for d in [30, 60, 90, 120, 150, 180]]
    emisiones   = [hoy - pd.Timedelta(days=d) for d in [60, 90, 120, 150, 180, 210]]
    return pd.DataFrame({
        "DOCTO_CC_ID":            list(range(1, n + 1)),
        "DOCTO_CC_ACR_ID":        [None] * n,
        "IMPTE_DOCTO_CC_ID":      list(range(10, 10 * n + 1, 10)),
        "ANTICIPO_CC_ID":         [None] * n,
        "CLIENTE_ID":             [101] * n,
        "TIPO_CLIENTE_ID":        [1] * n,
        "VENDEDOR_ID":            [1] * n,
        "MONEDA_ID":              [1] * n,
        "COND_PAGO_ID":           [1] * n,
        "CONCEPTO_CC_ID":         [1] * n,
        "NOMBRE_CLIENTE":         ["EMPRESA A"] * n,
        "TIPO_CLIENTE":           ["TIPO1"] * n,
        "MONEDA":                 ["MXN"] * n,
        "CONDICIONES":            ["30D"] * n,
        "VENDEDOR":               ["VENDEDOR1"] * n,
        "ESTATUS_CLIENTE":        ["A"] * n,
        "LIMITE_CREDITO":         [50000.0] * n,
        "CONCEPTO":               ["VENTA FACT"] * n,
        "FOLIO":                  [f"F{i:03d}" for i in range(1, n + 1)],
        "FECHA_EMISION":          emisiones,
        "FECHA_VENCIMIENTO":      vencimientos,
        "HORA":                   [None] * n,
        "SISTEMA_ORIGEN":         [None] * n,
        "NATURALEZA_CONCEPTO":    ["C"] * n,
        "CANCELADO":              ["N"] * n,
        "APLICADO":               ["N"] * n,
        "DESCRIPCION":            [f"Factura {i}" for i in range(1, n + 1)],
        "TIPO_USO_ANTICIPO":      [None] * n,
        "CARGOS":                 [1000.0, 500.0, 2000.0, 300.0, 800.0, 1200.0],
        "ABONOS":                 [0.0] * n,
        "TIPO_IMPTE":             ["C"] * n,
        "IMPORTE":                [1000.0, 500.0, 2000.0, 300.0, 800.0, 1200.0],
        "IMPUESTO":               [160.0,  80.0,  320.0,  48.0,  128.0, 192.0],
        "USUARIO_CREADOR":        [None] * n,
        "FECHA_HORA_CREACION":    [None] * n,
        "USUARIO_ULT_MODIF":      [None] * n,
        "FECHA_HORA_ULT_MODIF":   [None] * n,
        "USUARIO_CANCELACION":    [None] * n,
        "FECHA_HORA_CANCELACION": [None] * n,
    })


def test_atipico_delta_mora_valores_son_python_bool(
    df_multiples_facturas_abiertas: pd.DataFrame,
) -> None:
    """ATIPICO_DELTA_MORA debe contener Python bool, no numpy.bool_ ni int.

    numpy.bool_ se serializa como 0/1 en Excel (openpyxl lo trata como int).
    Python bool se serializa como TRUE/FALSE (tipo celda 'b').
    """
    resultado = generar_reporte_cxc(df_multiples_facturas_abiertas)
    totales = resultado["movimientos_totales_cxc"]
    assert "ATIPICO_DELTA_MORA" in totales.columns, (
        "movimientos_totales_cxc debe tener columna ATIPICO_DELTA_MORA"
    )
    no_nulos = [v for v in totales["ATIPICO_DELTA_MORA"] if v is not None]
    assert len(no_nulos) > 0, "ATIPICO_DELTA_MORA debe tener valores no nulos con 6 facturas abiertas"
    for val in no_nulos:
        assert type(val) is bool, (
            f"ATIPICO_DELTA_MORA contiene {type(val)} en lugar de Python bool nativo: {val}"
        )


def test_movimientos_totales_es_independiente_de_reporte(df_transaccional: pd.DataFrame) -> None:
    """TDD S2-A: movimientos_totales_cxc debe ser un objeto distinto de reporte_cxc.

    Garantiza que _agregar_zscores recibe una copia y no el mismo df.
    """
    resultado = generar_reporte_cxc(df_transaccional)
    totales = resultado["movimientos_totales_cxc"]
    reporte = resultado["reporte_cxc"]
    assert totales is not reporte, "movimientos_totales_cxc y reporte_cxc deben ser objetos distintos"
    zscore_en_totales = any("ZSCORE" in c.upper() for c in totales.columns)
    zscore_en_reporte = any("ZSCORE" in c.upper() for c in reporte.columns)
    assert zscore_en_totales, "movimientos_totales_cxc debe tener ZSCORE"
    assert not zscore_en_reporte, "reporte_cxc NO debe tener columnas ZSCORE"
