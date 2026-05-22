"""Tests para src/parquet_exporter.py.

Cubre: creacion de archivos Parquet por subdir, omision de DFs vacios,
timestamp en el nombre, round-trip de datos, sanitizacion de tipos object,
y retorno de lista de paths.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.parquet_exporter import exportar_parquet


# ==================================================================
# Fixtures y helpers
# ==================================================================


@pytest.fixture()
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "powerbi"


def _df_simple() -> pd.DataFrame:
    return pd.DataFrame({
        "NOMBRE": ["A", "B"],
        "SALDO": [1000.0, 2000.0],
    })


def _df_mixto_tipos() -> pd.DataFrame:
    """Simula la fila TOTAL con tipos mezclados en columna numerica."""
    return pd.DataFrame({
        "CLIENTE": ["CLI_A", "TOTAL"],
        "SALDO": [5000.0, 5000.0],
        "PCT": [0.5, ""],          # mezcla float/str como en concentracion ABC
        "CLASIF": ["A", ""],
    })


# ==================================================================
# Creacion de archivos y subdirectorios
# ==================================================================


def test_exportar_parquet_crea_archivo(tmp_output: Path) -> None:
    df = _df_simple()
    exportar_parquet({"reporte/movimientos_abiertos": df}, tmp_output, "20250601_120000")
    archivo = tmp_output / "reporte" / "movimientos_abiertos_20250601_120000.parquet"
    assert archivo.exists()


def test_exportar_parquet_crea_subdirectorio(tmp_output: Path) -> None:
    df = _df_simple()
    exportar_parquet({"analytics/antiguedad_cartera_mxn": df}, tmp_output, "ts")
    assert (tmp_output / "analytics").is_dir()


def test_exportar_parquet_multiples_subdirs(tmp_output: Path) -> None:
    vistas = {
        "reporte/movimientos_abiertos": _df_simple(),
        "analytics/cartera_vencida_mxn": _df_simple(),
        "kpis/kpis_resumen_mxn": _df_simple(),
        "auditoria/importes_atipicos": _df_simple(),
    }
    exportar_parquet(vistas, tmp_output, "ts")
    for subdir in ("reporte", "analytics", "kpis", "auditoria"):
        assert (tmp_output / subdir).is_dir()


# ==================================================================
# Omision de DataFrames vacios
# ==================================================================


def test_exportar_parquet_omite_df_vacio(tmp_output: Path) -> None:
    vistas = {
        "reporte/lleno": _df_simple(),
        "reporte/vacio": pd.DataFrame(),
    }
    resultado = exportar_parquet(vistas, tmp_output, "ts")
    archivos = list((tmp_output / "reporte").glob("*.parquet"))
    assert len(archivos) == 1
    assert len(resultado) == 1


def test_exportar_parquet_todo_vacio_retorna_lista_vacia(tmp_output: Path) -> None:
    vistas = {"reporte/vacio": pd.DataFrame()}
    resultado = exportar_parquet(vistas, tmp_output, "ts")
    assert resultado == []


# ==================================================================
# Nombre de archivo con timestamp
# ==================================================================


def test_exportar_parquet_timestamp_en_nombre(tmp_output: Path) -> None:
    ts = "20250601_093045"
    exportar_parquet({"reporte/ventas": _df_simple()}, tmp_output, ts)
    archivos = list((tmp_output / "reporte").glob("*.parquet"))
    assert any(ts in a.name for a in archivos)


def test_exportar_parquet_nombre_incluye_vista(tmp_output: Path) -> None:
    exportar_parquet({"reporte/movimientos_totales": _df_simple()}, tmp_output, "ts")
    archivos = list((tmp_output / "reporte").glob("*.parquet"))
    assert any("movimientos_totales" in a.name for a in archivos)


# ==================================================================
# Retorno de paths
# ==================================================================


def test_exportar_parquet_retorna_lista_de_paths(tmp_output: Path) -> None:
    resultado = exportar_parquet({"reporte/a": _df_simple()}, tmp_output, "ts")
    assert isinstance(resultado, list)
    assert all(isinstance(p, Path) for p in resultado)


def test_exportar_parquet_retorna_path_existente(tmp_output: Path) -> None:
    resultado = exportar_parquet({"reporte/a": _df_simple()}, tmp_output, "ts")
    assert all(p.exists() for p in resultado)


def test_exportar_parquet_cuenta_correcta(tmp_output: Path) -> None:
    vistas = {
        "reporte/a": _df_simple(),
        "reporte/b": _df_simple(),
        "reporte/c": pd.DataFrame(),
    }
    resultado = exportar_parquet(vistas, tmp_output, "ts")
    assert len(resultado) == 2


# ==================================================================
# Round-trip de datos
# ==================================================================


def test_exportar_parquet_contenido_correcto(tmp_output: Path) -> None:
    df_orig = pd.DataFrame({
        "NOMBRE_CLIENTE": ["ACME", "GLOBEX"],
        "SALDO_PENDIENTE": [10_000.0, 5_000.0],
    })
    resultado = exportar_parquet({"reporte/clientes": df_orig}, tmp_output, "ts")
    df_leido = pd.read_parquet(resultado[0])
    assert list(df_leido.columns) == list(df_orig.columns)
    assert len(df_leido) == len(df_orig)
    assert df_leido["SALDO_PENDIENTE"].sum() == pytest.approx(15_000.0)


def test_exportar_parquet_sin_indice(tmp_output: Path) -> None:
    """El Parquet no debe escribir el indice de pandas."""
    df = _df_simple()
    resultado = exportar_parquet({"reporte/test": df}, tmp_output, "ts")
    tabla = pq.read_table(resultado[0])
    assert "__index_level_0__" not in tabla.column_names


# ==================================================================
# Sanitizacion de tipos object
# ==================================================================


def test_exportar_parquet_sanitiza_tipos_mixtos(tmp_output: Path) -> None:
    """Columnas con tipos mezclados (float + str) deben convertirse a str."""
    df = _df_mixto_tipos()
    resultado = exportar_parquet({"reporte/concentracion": df}, tmp_output, "ts")
    df_leido = pd.read_parquet(resultado[0])
    assert pd.api.types.is_string_dtype(df_leido["PCT"])


def test_exportar_parquet_columnas_numericas_intactas(tmp_output: Path) -> None:
    """Columnas float/int no deben ser convertidas a string."""
    df = _df_simple()
    resultado = exportar_parquet({"reporte/test": df}, tmp_output, "ts")
    df_leido = pd.read_parquet(resultado[0])
    assert pd.api.types.is_float_dtype(df_leido["SALDO"])
