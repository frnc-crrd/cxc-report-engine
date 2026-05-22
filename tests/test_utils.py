"""Tests para src/utils.py.

No requiere Firebird ni PostgreSQL.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.utils import es_venta_mask


def test_es_venta_mask_sin_columnas_retorna_serie_false() -> None:
    """DataFrame sin TIPO_IMPTE ni CONCEPTO debe devolver una Serie de False."""
    df = pd.DataFrame({"NOMBRE_CLIENTE": ["A", "B"]})
    result = es_venta_mask(df)
    assert len(result) == len(df)
    assert not result.any()


def test_es_venta_mask_sin_tipo_impte_retorna_false() -> None:
    """Falta TIPO_IMPTE: retorna False aunque CONCEPTO tenga 'VENTA'."""
    df = pd.DataFrame({"CONCEPTO": ["VENTA CONTADO", "OTRO"]})
    result = es_venta_mask(df)
    assert not result.any()


def test_es_venta_mask_sin_concepto_retorna_false() -> None:
    """Falta CONCEPTO: retorna False aunque TIPO_IMPTE sea 'C'."""
    df = pd.DataFrame({"TIPO_IMPTE": ["C", "C"]})
    result = es_venta_mask(df)
    assert not result.any()


def test_es_venta_mask_filtra_tipo_c_con_venta() -> None:
    """Solo las filas con TIPO_IMPTE=='C' y CONCEPTO contiene 'VENTA' quedan True."""
    df = pd.DataFrame({
        "TIPO_IMPTE": ["C", "C", "R", "C"],
        "CONCEPTO":   ["VENTA CONTADO", "AJUSTE", "VENTA DEVOLUCION", "VENTA A CREDITO"],
    })
    result = es_venta_mask(df)
    assert list(result) == [True, False, False, True]


def test_es_venta_mask_nan_en_concepto_no_lanza() -> None:
    """NaN en CONCEPTO debe tratarse como False sin lanzar excepcion."""
    df = pd.DataFrame({
        "TIPO_IMPTE": ["C", "C"],
        "CONCEPTO":   [None, "VENTA ESPECIAL"],
    })
    result = es_venta_mask(df)
    assert list(result) == [False, True]


def test_es_venta_mask_df_vacio_retorna_serie_vacia() -> None:
    """DataFrame vacio produce Serie vacia sin errores."""
    df = pd.DataFrame({
        "TIPO_IMPTE": pd.Series([], dtype=str),
        "CONCEPTO":   pd.Series([], dtype=str),
    })
    result = es_venta_mask(df)
    assert len(result) == 0
    assert not result.any()


def test_es_venta_mask_venta_no_C_no_pasa() -> None:
    """CONCEPTO contiene 'VENTA' pero TIPO_IMPTE != 'C': debe ser False."""
    df = pd.DataFrame({
        "TIPO_IMPTE": ["R", "A", "T"],
        "CONCEPTO":   ["VENTA 1", "VENTA 2", "VENTA 3"],
    })
    result = es_venta_mask(df)
    assert not result.any()


def test_es_venta_mask_retorna_pandas_series() -> None:
    """El valor de retorno siempre es pd.Series."""
    df = pd.DataFrame({"TIPO_IMPTE": ["C"], "CONCEPTO": ["VENTA"]})
    result = es_venta_mask(df)
    assert isinstance(result, pd.Series)
