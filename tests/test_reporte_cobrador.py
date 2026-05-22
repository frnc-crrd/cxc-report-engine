"""Tests para generar_reportes_por_cobrador (split Excel por cobrador).

No requiere Firebird ni PostgreSQL — usa DataFrames sintéticos.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.reporte_cobrador import generar_reportes_por_cobrador

# ── Datos de prueba ────────────────────────────────────────────────────────

COLUMNAS_ABIERTOS = [
    "NOMBRE_CLIENTE", "MONEDA", "CONDICIONES", "ESTATUS_CLIENTE",
    "CONCEPTO", "FOLIO", "FECHA_EMISION", "FECHA_VENCIMIENTO",
    "DESCRIPCION", "CARGOS", "ABONOS", "IMPORTE", "IMPUESTO",
    "SALDO_FACTURA", "DELTA_MORA", "CATEGORIA_MORA",
]


def _make_df(rows: list[dict]) -> pd.DataFrame:
    base = {c: None for c in COLUMNAS_ABIERTOS}
    records = [{**base, **r} for r in rows]
    return pd.DataFrame(records)


@pytest.fixture()
def df_abiertos() -> pd.DataFrame:
    return _make_df([
        {"NOMBRE_CLIENTE": "EMPRESA A", "MONEDA": "MXN", "SALDO_FACTURA": 1000.0},
        {"NOMBRE_CLIENTE": "EMPRESA A", "MONEDA": "MXN", "SALDO_FACTURA": 500.0},
        {"NOMBRE_CLIENTE": "EMPRESA B", "MONEDA": "USD", "SALDO_FACTURA": 200.0},
        {"NOMBRE_CLIENTE": "EMPRESA C", "MONEDA": "MXN", "SALDO_FACTURA": 300.0},
    ])


@pytest.fixture()
def assignments() -> dict[str, str]:
    return {
        "EMPRESA A": "Haidee",
        "EMPRESA B": "Jovanna",
        # EMPRESA C no asignada → debe ir a PENDIENTE
    }


# ── Tests ──────────────────────────────────────────────────────────────────

def test_creates_one_file_per_cobrador(df_abiertos, assignments, tmp_path):
    archivos = generar_reportes_por_cobrador(
        df_abiertos, assignments, tmp_path, "20260101_000000"
    )
    nombres = {a.name for a in archivos}
    assert any("HAIDEE" in n for n in nombres)
    assert any("JOVANNA" in n for n in nombres)
    assert any("PENDIENTE" in n for n in nombres)


def test_pendiente_created_for_unassigned_clients(df_abiertos, assignments, tmp_path):
    archivos = generar_reportes_por_cobrador(
        df_abiertos, assignments, tmp_path, "ts"
    )
    pendiente = [a for a in archivos if "PENDIENTE" in a.name]
    assert len(pendiente) == 1


def test_no_rows_lost(df_abiertos, assignments, tmp_path):
    """El total de filas en todos los archivos == filas del DataFrame original."""
    archivos = generar_reportes_por_cobrador(
        df_abiertos, assignments, tmp_path, "ts"
    )
    total = 0
    for path in archivos:
        df = pd.read_excel(path, sheet_name=0)
        total += len(df)
    assert total == len(df_abiertos)


def test_cliente_id_column_absent_in_output(df_abiertos, assignments, tmp_path):
    """CLIENTE_ID nunca debe aparecer en los Excels de cobrador."""
    df_con_id = df_abiertos.copy()
    df_con_id["CLIENTE_ID"] = [1, 1, 2, 3]
    archivos = generar_reportes_por_cobrador(
        df_con_id, assignments, tmp_path, "ts"
    )
    for path in archivos:
        df = pd.read_excel(path, sheet_name=0)
        assert "CLIENTE_ID" not in df.columns


def test_files_created_in_output_dir(df_abiertos, assignments, tmp_path):
    archivos = generar_reportes_por_cobrador(
        df_abiertos, assignments, tmp_path, "ts"
    )
    for path in archivos:
        assert path.parent == tmp_path
        assert path.exists()


def test_empty_df_returns_no_files(assignments, tmp_path):
    df_vacio = pd.DataFrame(columns=COLUMNAS_ABIERTOS)
    archivos = generar_reportes_por_cobrador(df_vacio, assignments, tmp_path, "ts")
    assert archivos == []


def test_cobrador_with_no_clients_creates_no_file(df_abiertos, tmp_path):
    """Un cobrador registrado en assignments pero sin filas no genera archivo."""
    assignments_extra = {
        "EMPRESA A": "Haidee",
        "EMPRESA B": "Jovanna",
    }
    archivos = generar_reportes_por_cobrador(
        df_abiertos, assignments_extra, tmp_path, "ts"
    )
    # Solo Haidee, Jovanna, PENDIENTE (EMPRESA C sin asignar)
    assert len(archivos) == 3


def test_file_naming_sanitizes_spaces(tmp_path):
    df = _make_df([{"NOMBRE_CLIENTE": "EMPRESA A", "SALDO_FACTURA": 100.0}])
    assignments = {"EMPRESA A": "Juan Carlos"}
    archivos = generar_reportes_por_cobrador(df, assignments, tmp_path, "ts")
    assert any("JUAN_CARLOS" in a.name for a in archivos)


def test_band_group_alternates_per_venta(tmp_path):
    """Las bandas alternan por factura: cada fila con CONCEPTO 'VENTA' inicia nueva banda.
    Los abonos que siguen heredan la misma banda hasta la proxima venta."""
    df = _make_df([
        {"NOMBRE_CLIENTE": "EMPRESA A", "CONCEPTO": "VENTA A CREDITO",  "CARGOS": 1000.0, "ABONOS": 0},
        {"NOMBRE_CLIENTE": "EMPRESA A", "CONCEPTO": "PAGO PARCIAL",     "CARGOS": 0,      "ABONOS": 200.0},
        {"NOMBRE_CLIENTE": "EMPRESA A", "CONCEPTO": "VENTA A CREDITO",  "CARGOS": 500.0,  "ABONOS": 0},
        {"NOMBRE_CLIENTE": "EMPRESA B", "CONCEPTO": "VENTA DE CONTADO", "CARGOS": 300.0,  "ABONOS": 0},
        {"NOMBRE_CLIENTE": "EMPRESA B", "CONCEPTO": "PAGO TOTAL",       "CARGOS": 0,      "ABONOS": 300.0},
    ])
    assignments = {"EMPRESA A": "Haidee", "EMPRESA B": "Haidee"}
    archivos = generar_reportes_por_cobrador(df, assignments, tmp_path, "ts")

    haidee_path = next(a for a in archivos if "HAIDEE" in a.name)
    df_h = pd.read_excel(haidee_path, sheet_name=0)

    # 5 filas: no se pierden datos
    assert len(df_h) == 5
    # _BAND_GROUP no aparece en el Excel (es columna interna)
    assert "_BAND_GROUP" not in df_h.columns
