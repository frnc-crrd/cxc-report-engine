"""Funciones utilitarias compartidas entre los módulos de análisis CxC."""

from __future__ import annotations

import pandas as pd


def es_venta_mask(df: pd.DataFrame) -> pd.Series:
    """Máscara para aislar facturas de venta (TIPO_IMPTE=='C' y CONCEPTO contiene 'VENTA').

    Versión defensiva: retorna Serie de False si faltan las columnas necesarias.
    """
    if "TIPO_IMPTE" not in df.columns or "CONCEPTO" not in df.columns:
        return pd.Series(False, index=df.index)
    return (df["TIPO_IMPTE"] == "C") & df["CONCEPTO"].str.contains("VENTA", na=False)
