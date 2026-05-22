"""Tests unitarios para src/analytics.py.

Cubre: buckets de antiguedad correctos, pivote por cliente con columnas
de rango, cartera vencida vs vigente, resumen por vendedor, tendencia
mensual, resumen por concepto, filtrado por moneda, y run_analytics.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.analytics import Analytics

# ==================================================================
# Configuracion de rangos para tests (3 buckets simples)
# ==================================================================

RANGOS = [
    (None, 0,  "VIGENTE"),
    (1,    30, "VENCIDO_LEVE"),
    (31,   None, "VENCIDO_GRAVE"),
]

# ==================================================================
# Helpers de datos sinteticos
# ==================================================================


def _venta(
    cliente: str,
    importe: float,
    saldo: float,
    delta_mora: float,
    moneda: str = "MXN",
    vendedor: str = "VENDEDOR_A",
    concepto: str = "VENTA CONTADO",
    fecha: str = "2025-01-15",
    estatus: str = "A",
) -> dict[str, object]:
    return {
        "NOMBRE_CLIENTE": cliente,
        "TIPO_IMPTE": "C",
        "CONCEPTO": concepto,
        "MONEDA": moneda,
        "IMPORTE": importe,
        "IMPUESTO": 0.0,
        "SALDO_FACTURA": saldo,
        "DELTA_MORA": delta_mora,
        "VENDEDOR": vendedor,
        "FECHA_EMISION": pd.Timestamp(fecha),
        "ESTATUS_CLIENTE": estatus,
    }


def _cobro(
    cliente: str,
    importe: float,
    moneda: str = "MXN",
    concepto: str = "COBRO",
    fecha: str = "2025-01-20",
) -> dict[str, object]:
    return {
        "NOMBRE_CLIENTE": cliente,
        "TIPO_IMPTE": "R",
        "CONCEPTO": concepto,
        "MONEDA": moneda,
        "IMPORTE": importe,
        "IMPUESTO": 0.0,
        "SALDO_FACTURA": 0.0,
        "DELTA_MORA": 0.0,
        "VENDEDOR": "",
        "FECHA_EMISION": pd.Timestamp(fecha),
        "ESTATUS_CLIENTE": "A",
    }


def _df_mixto() -> pd.DataFrame:
    """Dataset con ventas en 3 rangos de antiguedad y cobros en MXN."""
    return pd.DataFrame([
        _venta("CLI_A", 10_000, saldo=10_000, delta_mora=-5),   # VIGENTE
        _venta("CLI_B", 8_000,  saldo=8_000,  delta_mora=15),   # VENCIDO_LEVE
        _venta("CLI_C", 5_000,  saldo=5_000,  delta_mora=45),   # VENCIDO_GRAVE
        _cobro("CLI_A", 3_000),
    ])


# ==================================================================
# run_analytics — estructura
# ==================================================================


def test_run_analytics_claves_esperadas() -> None:
    analytics = Analytics(RANGOS)
    vistas = {"movimientos_totales_cxc": _df_mixto()}
    resultado = analytics.run_analytics(vistas)
    claves_esperadas = {
        "antiguedad_cartera_mxn", "antiguedad_cartera_usd",
        "antiguedad_por_cliente_mxn", "antiguedad_por_cliente_usd",
        "cartera_vencida_vs_vigente_mxn", "cartera_vencida_vs_vigente_usd",
        "resumen_por_vendedor_mxn", "resumen_por_vendedor_usd",
        "tendencia_mensual_mxn", "tendencia_mensual_usd",
        "resumen_concepto_cxc_mxn", "resumen_concepto_cxc_usd",
    }
    assert claves_esperadas <= set(resultado.keys())


def test_run_analytics_df_vacio_retorna_dataframes_vacios() -> None:
    analytics = Analytics(RANGOS)
    vistas = {"movimientos_totales_cxc": pd.DataFrame()}
    resultado = analytics.run_analytics(vistas)
    for df in resultado.values():
        assert isinstance(df, pd.DataFrame)


def test_run_analytics_acepta_vistas_faltantes() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics.run_analytics({})
    assert isinstance(resultado, dict)


# ==================================================================
# _antiguedad_cartera — buckets correctos
# ==================================================================


def test_antiguedad_cartera_rangos_correctos() -> None:
    analytics = Analytics(RANGOS)
    df = _df_mixto()
    resultado = analytics._antiguedad_cartera(df, "MXN")
    sin_total = resultado[resultado["RANGO_ANTIGUEDAD"] != "TOTAL"]
    rangos_presentes = set(sin_total["RANGO_ANTIGUEDAD"].tolist())
    assert rangos_presentes == {"VIGENTE", "VENCIDO_LEVE", "VENCIDO_GRAVE"}


def test_antiguedad_cartera_saldos_correctos() -> None:
    analytics = Analytics(RANGOS)
    df = _df_mixto()
    resultado = analytics._antiguedad_cartera(df, "MXN")
    sin_total = resultado[resultado["RANGO_ANTIGUEDAD"] != "TOTAL"]
    por_rango = sin_total.set_index("RANGO_ANTIGUEDAD")["SALDO_PENDIENTE"].to_dict()
    assert por_rango["VIGENTE"] == pytest.approx(10_000)
    assert por_rango["VENCIDO_LEVE"] == pytest.approx(8_000)
    assert por_rango["VENCIDO_GRAVE"] == pytest.approx(5_000)


def test_antiguedad_cartera_tiene_fila_total() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_cartera(_df_mixto(), "MXN")
    assert "TOTAL" in resultado["RANGO_ANTIGUEDAD"].values


def test_antiguedad_cartera_total_correcto() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_cartera(_df_mixto(), "MXN")
    fila_total = resultado[resultado["RANGO_ANTIGUEDAD"] == "TOTAL"].iloc[0]
    assert fila_total["SALDO_PENDIENTE"] == pytest.approx(23_000)


def test_antiguedad_cartera_pct_suma_uno() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_cartera(_df_mixto(), "MXN")
    sin_total = resultado[resultado["RANGO_ANTIGUEDAD"] != "TOTAL"]
    assert sin_total["PCT_DEL_TOTAL"].sum() == pytest.approx(1.0, abs=0.01)


def test_antiguedad_cartera_excluye_saldos_cero() -> None:
    """Facturas ya cobradas (saldo==0) no deben aparecer en antiguedad."""
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 5_000, saldo=5_000, delta_mora=10),
        _venta("B", 5_000, saldo=0.0,   delta_mora=10),
    ])
    resultado = analytics._antiguedad_cartera(df, "MXN")
    total_facturas = resultado[resultado["RANGO_ANTIGUEDAD"] == "TOTAL"]["NUM_FACTURAS_PENDIENTES"].iloc[0]
    assert total_facturas == 1


def test_antiguedad_cartera_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._antiguedad_cartera(pd.DataFrame(), "MXN").empty


def test_antiguedad_cartera_filtra_moneda() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("MXN_CLI", 10_000, saldo=10_000, delta_mora=5, moneda="MXN"),
        _venta("USD_CLI", 1_000,  saldo=1_000,  delta_mora=5, moneda="USD"),
    ])
    mxn = analytics._antiguedad_cartera(df, "MXN")
    usd = analytics._antiguedad_cartera(df, "USD")
    total_mxn = mxn[mxn["RANGO_ANTIGUEDAD"] == "TOTAL"]["SALDO_PENDIENTE"].iloc[0]
    total_usd = usd[usd["RANGO_ANTIGUEDAD"] == "TOTAL"]["SALDO_PENDIENTE"].iloc[0]
    assert total_mxn == pytest.approx(10_000)
    assert total_usd == pytest.approx(1_000)


# ==================================================================
# _antiguedad_por_cliente — pivote con columnas de rango
# ==================================================================


def test_antiguedad_por_cliente_columnas_de_rango() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_por_cliente(_df_mixto(), "MXN")
    for _, _, label in RANGOS:
        assert label in resultado.columns


def test_antiguedad_por_cliente_valores_por_rango() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_por_cliente(_df_mixto(), "MXN")
    sin_total = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"]
    cli_b = sin_total[sin_total["NOMBRE_CLIENTE"] == "CLI_B"].iloc[0]
    assert cli_b["VENCIDO_LEVE"] == pytest.approx(8_000)
    assert cli_b["VIGENTE"] == pytest.approx(0.0)


def test_antiguedad_por_cliente_tiene_fila_total() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._antiguedad_por_cliente(_df_mixto(), "MXN")
    assert "TOTAL" in resultado["NOMBRE_CLIENTE"].values


def test_antiguedad_por_cliente_orden_descendente_saldo() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("MENOR", 1_000, saldo=1_000, delta_mora=5),
        _venta("MAYOR", 9_000, saldo=9_000, delta_mora=5),
    ])
    resultado = analytics._antiguedad_por_cliente(df, "MXN")
    sin_total = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"]
    saldos = sin_total["SALDO_PENDIENTE"].tolist()
    assert saldos == sorted(saldos, reverse=True)


def test_antiguedad_por_cliente_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._antiguedad_por_cliente(pd.DataFrame(), "MXN").empty


# ==================================================================
# _cartera_vencida_vs_vigente — suma correcta
# ==================================================================


def test_cartera_vencida_vs_vigente_proporciones() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 6_000, saldo=6_000, delta_mora=-1),  # VIGENTE
        _venta("B", 4_000, saldo=4_000, delta_mora=20),  # VENCIDA
    ])
    resultado = analytics._cartera_vencida_vs_vigente(df, "MXN")
    sin_total = resultado[resultado["ESTATUS_VENCIMIENTO"] != "TOTAL"]
    vig = sin_total[sin_total["ESTATUS_VENCIMIENTO"] == "FACTURAS VIGENTES"]["SALDO_PENDIENTE"].iloc[0]
    vec = sin_total[sin_total["ESTATUS_VENCIMIENTO"] == "FACTURAS VENCIDAS"]["SALDO_PENDIENTE"].iloc[0]
    assert vig == pytest.approx(6_000)
    assert vec == pytest.approx(4_000)


def test_cartera_vencida_vs_vigente_pct_suma_uno() -> None:
    analytics = Analytics(RANGOS)
    df = _df_mixto()
    resultado = analytics._cartera_vencida_vs_vigente(df, "MXN")
    sin_total = resultado[resultado["ESTATUS_VENCIMIENTO"] != "TOTAL"]
    assert sin_total["PCT_DEL_TOTAL"].sum() == pytest.approx(1.0, abs=0.01)


def test_cartera_vencida_vs_vigente_tiene_fila_total() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._cartera_vencida_vs_vigente(_df_mixto(), "MXN")
    assert "TOTAL" in resultado["ESTATUS_VENCIMIENTO"].values


def test_cartera_vencida_vs_vigente_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._cartera_vencida_vs_vigente(pd.DataFrame(), "MXN").empty


def test_cartera_vencida_vs_vigente_excluye_saldos_cero() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 5_000, saldo=5_000, delta_mora=10),
        _venta("B", 5_000, saldo=0.0,   delta_mora=10),
    ])
    resultado = analytics._cartera_vencida_vs_vigente(df, "MXN")
    total = resultado[resultado["ESTATUS_VENCIMIENTO"] == "TOTAL"]["NUM_FACTURAS_PENDIENTES"].iloc[0]
    assert total == 1


# ==================================================================
# _resumen_por_vendedor
# ==================================================================


def test_resumen_por_vendedor_agrupa_por_vendedor() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("CLI_1", 10_000, saldo=10_000, delta_mora=0, vendedor="JUAN"),
        _venta("CLI_2", 5_000,  saldo=5_000,  delta_mora=0, vendedor="PEDRO"),
        _venta("CLI_3", 5_000,  saldo=5_000,  delta_mora=0, vendedor="JUAN"),
    ])
    resultado = analytics._resumen_por_vendedor(df, "MXN")
    sin_total = resultado[resultado["VENDEDOR"] != "TOTAL"]
    assert len(sin_total) == 2
    juan = sin_total[sin_total["VENDEDOR"] == "JUAN"]["SALDO_PENDIENTE"].iloc[0]
    assert juan == pytest.approx(15_000)


def test_resumen_por_vendedor_tiene_fila_total() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._resumen_por_vendedor(_df_mixto(), "MXN")
    assert "TOTAL" in resultado["VENDEDOR"].values


def test_resumen_por_vendedor_columnas() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._resumen_por_vendedor(_df_mixto(), "MXN")
    for col in ["VENDEDOR", "NUM_REGISTROS", "SALDO_PENDIENTE", "IMPORTE_TOTAL"]:
        assert col in resultado.columns


def test_resumen_por_vendedor_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._resumen_por_vendedor(pd.DataFrame(), "MXN").empty


# ==================================================================
# _tendencia_mensual
# ==================================================================


def test_tendencia_mensual_columnas() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._tendencia_mensual(_df_mixto(), "MXN")
    for col in ["ANIO", "MES", "ESTADO", "NUM_FACTURAS", "IMPORTE_TOTAL"]:
        assert col in resultado.columns


def test_tendencia_mensual_clasifica_cobradas_vs_pendientes() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 5_000, saldo=0.0,   delta_mora=0, fecha="2025-03-01"),
        _venta("B", 5_000, saldo=5_000, delta_mora=5, fecha="2025-03-01"),
    ])
    resultado = analytics._tendencia_mensual(df, "MXN")
    estados = set(resultado["ESTADO"].tolist())
    assert "COBRADAS" in estados
    assert "PENDIENTES" in estados


def test_tendencia_mensual_agrupa_por_anio_mes() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 5_000, saldo=5_000, delta_mora=0, fecha="2025-01-10"),
        _venta("B", 5_000, saldo=5_000, delta_mora=0, fecha="2025-02-10"),
    ])
    resultado = analytics._tendencia_mensual(df, "MXN")
    meses = sorted(resultado["MES"].unique().tolist())
    assert 1 in meses
    assert 2 in meses


def test_tendencia_mensual_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._tendencia_mensual(pd.DataFrame(), "MXN").empty


# ==================================================================
# _resumen_por_concepto
# ==================================================================


def test_resumen_por_concepto_separa_cargos_abonos() -> None:
    analytics = Analytics(RANGOS)
    df = pd.DataFrame([
        _venta("A", 10_000, saldo=10_000, delta_mora=0, concepto="VENTA CREDITO"),
        _cobro("A", 5_000, concepto="COBRO"),
    ])
    resultado = analytics._resumen_por_concepto(df, "MXN")
    sin_total = resultado[resultado["CONCEPTO"] != "TOTAL"]
    assert "NUM_CARGOS" in resultado.columns
    assert "NUM_ABONOS" in resultado.columns
    venta_row = sin_total[sin_total["CONCEPTO"].str.contains("VENTA")].iloc[0]
    assert venta_row["NUM_CARGOS"] == 1


def test_resumen_por_concepto_tiene_fila_total() -> None:
    analytics = Analytics(RANGOS)
    resultado = analytics._resumen_por_concepto(_df_mixto(), "MXN")
    assert "TOTAL" in resultado["CONCEPTO"].values


def test_resumen_por_concepto_df_vacio_devuelve_vacio() -> None:
    analytics = Analytics(RANGOS)
    assert analytics._resumen_por_concepto(pd.DataFrame(), "MXN").empty
