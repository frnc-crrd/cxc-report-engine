"""Tests unitarios para src/kpis.py.

Cubre: generar_kpis (estructura de claves, doble moneda, alias),
DSO, CEI, indice de morosidad, concentracion Pareto/ABC,
utilizacion de limite de credito, morosidad por cliente.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.kpis import (
    _calcular_concentracion,
    _calcular_kpis_macro,
    _calcular_limite_credito,
    _calcular_morosidad_por_cliente,
    generar_kpis,
)

# ==================================================================
# Helpers de datos sinteticos
# ==================================================================

HOY = pd.Timestamp("today").normalize()
HACE_30 = HOY - pd.Timedelta(days=30)
HACE_120 = HOY - pd.Timedelta(days=120)


def _fila_venta(
    cliente: str,
    importe: float,
    saldo: float,
    delta_mora: float = 0.0,
    moneda: str = "MXN",
    fecha: pd.Timestamp | None = None,
    limite: float = 0.0,
    estatus: str = "A",
) -> dict[str, object]:
    return {
        "NOMBRE_CLIENTE": cliente,
        "TIPO_IMPTE": "C",
        "CONCEPTO": "VENTA CONTADO",
        "IMPORTE": importe,
        "IMPUESTO": importe * 0.16,
        "SALDO_FACTURA": saldo,
        "DELTA_MORA": delta_mora,
        "MONEDA": moneda,
        "FECHA_EMISION": fecha if fecha is not None else HACE_30,
        "LIMITE_CREDITO": limite,
        "ESTATUS_CLIENTE": estatus,
    }


def _fila_cobro(
    cliente: str,
    importe: float,
    moneda: str = "MXN",
    fecha: pd.Timestamp | None = None,
) -> dict[str, object]:
    return {
        "NOMBRE_CLIENTE": cliente,
        "TIPO_IMPTE": "R",
        "CONCEPTO": "COBRO",
        "IMPORTE": importe,
        "IMPUESTO": 0.0,
        "SALDO_FACTURA": 0.0,
        "DELTA_MORA": 0.0,
        "MONEDA": moneda,
        "FECHA_EMISION": fecha if fecha is not None else HACE_30,
        "LIMITE_CREDITO": 0.0,
        "ESTATUS_CLIENTE": "A",
    }


def _df_basico() -> pd.DataFrame:
    """Dataset minimo con ventas y cobros en MXN."""
    filas = [
        _fila_venta("CLIENTE_A", 10_000, saldo=10_000, delta_mora=0),
        _fila_venta("CLIENTE_B", 5_000, saldo=5_000, delta_mora=15),
        _fila_cobro("CLIENTE_A", 3_000),
    ]
    return pd.DataFrame(filas)


def _df_doble_moneda() -> pd.DataFrame:
    filas = [
        _fila_venta("CLI_MXN", 50_000, saldo=50_000, moneda="MXN"),
        _fila_venta("CLI_USD", 1_000, saldo=1_000, moneda="USD"),
    ]
    return pd.DataFrame(filas)


# ==================================================================
# generar_kpis — estructura y claves
# ==================================================================


def test_generar_kpis_df_vacio_devuelve_dict_vacio() -> None:
    resultado = generar_kpis(pd.DataFrame())
    assert resultado == {}


def test_generar_kpis_claves_por_moneda() -> None:
    resultado = generar_kpis(_df_doble_moneda())
    claves_esperadas = {
        "kpis_resumen_mxn", "kpis_concentracion_mxn",
        "kpis_limite_credito_mxn", "kpis_morosidad_cliente_mxn",
        "kpis_resumen_usd", "kpis_concentracion_usd",
        "kpis_limite_credito_usd", "kpis_morosidad_cliente_usd",
    }
    assert claves_esperadas <= set(resultado.keys())


def test_generar_kpis_alias_mxn_presentes() -> None:
    """Alias sin sufijo deben apuntar al equivalente MXN."""
    resultado = generar_kpis(_df_doble_moneda())
    assert "kpis_resumen" in resultado
    assert "kpis_concentracion" in resultado
    assert "kpis_limite_credito" in resultado
    assert "kpis_morosidad_cliente" in resultado


def test_generar_kpis_valores_mxn_son_dataframes() -> None:
    resultado = generar_kpis(_df_basico())
    assert isinstance(resultado["kpis_resumen_mxn"], pd.DataFrame)
    assert isinstance(resultado["kpis_concentracion_mxn"], pd.DataFrame)


def test_generar_kpis_monedas_son_independientes() -> None:
    """MXN y USD deben tener saldos distintos."""
    resultado = generar_kpis(_df_doble_moneda())
    df_mxn = resultado["kpis_resumen_mxn"]
    df_usd = resultado["kpis_resumen_usd"]
    assert not df_mxn.equals(df_usd)


# ==================================================================
# DSO — Days Sales Outstanding
# ==================================================================


def test_dso_proporcional_al_saldo() -> None:
    """DSO = (saldo / ventas_en_periodo) * dias."""
    dias = 90
    filas = [_fila_venta("CLI", 10_000, saldo=5_000, fecha=HACE_30)]
    df = pd.DataFrame(filas)
    resultado = generar_kpis(df, dias_periodo=dias)
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_dso = df_resumen[df_resumen["KPI"].str.contains("DSO")].iloc[0]
    ventas = 10_000 * 1.16
    esperado = round((5_000 / ventas) * dias, 1)
    assert fila_dso["VALOR"] == pytest.approx(esperado, abs=0.1)


def test_dso_cero_cuando_no_hay_ventas_en_periodo() -> None:
    """Sin ventas en el periodo, DSO debe ser 0."""
    filas = [_fila_venta("CLI", 10_000, saldo=10_000, fecha=HACE_120)]
    df = pd.DataFrame(filas)
    resultado = generar_kpis(df, dias_periodo=30)
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_dso = df_resumen[df_resumen["KPI"].str.contains("DSO")].iloc[0]
    assert fila_dso["VALOR"] == 0.0


# ==================================================================
# CEI — Collection Effectiveness Index
# ==================================================================


def test_cei_columnas_presentes() -> None:
    resultado = generar_kpis(_df_basico())
    df_resumen = resultado["kpis_resumen_mxn"]
    assert "KPI" in df_resumen.columns
    assert "VALOR" in df_resumen.columns
    assert "UNIDAD" in df_resumen.columns
    assert "INTERPRETACION" in df_resumen.columns


def test_cei_entre_0_y_1() -> None:
    """CEI es una proporcion; debe estar en [0, 1]."""
    resultado = generar_kpis(_df_basico())
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_cei = df_resumen[df_resumen["KPI"].str.contains("CEI")].iloc[0]
    assert 0.0 <= float(fila_cei["VALOR"]) <= 1.0


def test_cei_uno_cuando_no_hay_cobrable() -> None:
    """Sin cobros ni cargos en el periodo, cobrable==0 → CEI==1.0."""
    filas = [_fila_venta("CLI", 0, saldo=0, fecha=HACE_120)]
    df = pd.DataFrame(filas)
    resultado = generar_kpis(df, dias_periodo=30)
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_cei = df_resumen[df_resumen["KPI"].str.contains("CEI")].iloc[0]
    assert float(fila_cei["VALOR"]) == pytest.approx(1.0)


# ==================================================================
# Indice de Morosidad
# ==================================================================


def test_morosidad_correcto_con_saldo_vencido() -> None:
    """50% de saldo vencido → morosidad == 0.5."""
    filas = [
        _fila_venta("A", 10_000, saldo=5_000, delta_mora=0),
        _fila_venta("B", 10_000, saldo=5_000, delta_mora=15),
    ]
    df = pd.DataFrame(filas)
    resultado = generar_kpis(df)
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_mora = df_resumen[df_resumen["KPI"].str.contains("Morosidad")].iloc[0]
    assert float(fila_mora["VALOR"]) == pytest.approx(0.5, abs=0.01)


def test_morosidad_cero_cuando_no_hay_vencidos() -> None:
    filas = [_fila_venta("A", 10_000, saldo=10_000, delta_mora=0)]
    df = pd.DataFrame(filas)
    resultado = generar_kpis(df)
    df_resumen = resultado["kpis_resumen_mxn"]
    fila_mora = df_resumen[df_resumen["KPI"].str.contains("Morosidad")].iloc[0]
    assert float(fila_mora["VALOR"]) == pytest.approx(0.0)


# ==================================================================
# _calcular_concentracion — ABC
# ==================================================================


def _df_concentracion() -> pd.DataFrame:
    """10 clientes con saldos distintos para clasificacion ABC."""
    saldos = [50_000, 20_000, 10_000, 8_000, 5_000, 3_000, 2_000, 1_000, 500, 100]
    filas = [
        {
            "NOMBRE_CLIENTE": f"CLI_{i}",
            "TIPO_IMPTE": "C",
            "CONCEPTO": "VENTA",
            "SALDO_FACTURA": s,
            "IMPORTE": s,
            "IMPUESTO": 0.0,
        }
        for i, s in enumerate(saldos)
    ]
    return pd.DataFrame(filas)


def test_concentracion_tiene_fila_total() -> None:
    df = _df_concentracion()
    resultado = _calcular_concentracion(df)
    assert "TOTAL" in resultado["NOMBRE_CLIENTE"].values


def test_concentracion_columnas_requeridas() -> None:
    df = _df_concentracion()
    resultado = _calcular_concentracion(df)
    for col in ["NOMBRE_CLIENTE", "SALDO_PENDIENTE", "PCT_DEL_TOTAL", "PCT_ACUMULADO", "CLASIFICACION"]:
        assert col in resultado.columns


def test_concentracion_clasificacion_abc() -> None:
    df = _df_concentracion()
    resultado = _calcular_concentracion(df)
    clientes = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"]
    clasif = set(clientes["CLASIFICACION"].tolist())
    assert clasif <= {"A", "B", "C"}
    assert "A" in clasif


def test_concentracion_primer_cliente_es_a() -> None:
    """El cliente con mayor saldo siempre es categoria A."""
    df = _df_concentracion()
    resultado = _calcular_concentracion(df)
    primer_cliente = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"].iloc[0]
    assert primer_cliente["CLASIFICACION"] == "A"


def test_concentracion_orden_descendente() -> None:
    """Clientes activos deben aparecer de mayor a menor saldo."""
    df = _df_concentracion()
    resultado = _calcular_concentracion(df)
    clientes_activos = resultado[
        (resultado["NOMBRE_CLIENTE"] != "TOTAL") &
        (pd.to_numeric(resultado["SALDO_PENDIENTE"], errors="coerce") > 0)
    ]["SALDO_PENDIENTE"].tolist()
    assert clientes_activos == sorted(clientes_activos, reverse=True)


def test_concentracion_df_sin_ventas_devuelve_vacio() -> None:
    df = pd.DataFrame({"TIPO_IMPTE": ["R"], "CONCEPTO": ["COBRO"], "SALDO_FACTURA": [100.0]})
    assert _calcular_concentracion(df).empty


# ==================================================================
# _calcular_limite_credito — alertas
# ==================================================================


def _df_limite() -> pd.DataFrame:
    filas = [
        {**_fila_venta("SIN_LIMITE", 10_000, saldo=5_000, limite=0.0), "ESTATUS_CLIENTE": "A"},
        {**_fila_venta("NORMAL", 10_000, saldo=3_000, limite=10_000), "ESTATUS_CLIENTE": "A"},
        {**_fila_venta("ALTO", 10_000, saldo=7_500, limite=10_000), "ESTATUS_CLIENTE": "A"},
        {**_fila_venta("CRITICO", 10_000, saldo=9_500, limite=10_000), "ESTATUS_CLIENTE": "A"},
        {**_fila_venta("SOBRE_LIMITE", 10_000, saldo=12_000, limite=10_000), "ESTATUS_CLIENTE": "A"},
    ]
    return pd.DataFrame(filas)


def test_limite_credito_alertas_correctas() -> None:
    df = _df_limite()
    resultado = _calcular_limite_credito(df)
    sin_total = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"]
    alertas = sin_total.set_index("NOMBRE_CLIENTE")["ALERTA"].to_dict()
    assert alertas["SIN_LIMITE"] == "SIN_LIMITE"
    assert alertas["NORMAL"] == "NORMAL"
    assert alertas["ALTO"] == "ALTO"
    assert alertas["CRITICO"] == "CRITICO"
    assert alertas["SOBRE_LIMITE"] == "SOBRE_LIMITE"


def test_limite_credito_tiene_fila_total() -> None:
    df = _df_limite()
    resultado = _calcular_limite_credito(df)
    assert "TOTAL" in resultado["NOMBRE_CLIENTE"].values


def test_limite_credito_sin_nombre_cliente_devuelve_vacio() -> None:
    df = pd.DataFrame({"IMPORTE": [100.0]})
    assert _calcular_limite_credito(df).empty


# ==================================================================
# _calcular_morosidad_por_cliente
# ==================================================================


def test_morosidad_por_cliente_separa_vencido_vigente() -> None:
    filas = [
        _fila_venta("MOROSO", 10_000, saldo=10_000, delta_mora=30),
        _fila_venta("PUNTUAL", 5_000, saldo=5_000, delta_mora=0),
    ]
    df = pd.DataFrame(filas)
    resultado = _calcular_morosidad_por_cliente(df, HOY)
    sin_total = resultado[resultado["NOMBRE_CLIENTE"] != "TOTAL"]
    moroso = sin_total[sin_total["NOMBRE_CLIENTE"] == "MOROSO"].iloc[0]
    puntual = sin_total[sin_total["NOMBRE_CLIENTE"] == "PUNTUAL"].iloc[0]
    assert moroso["SALDO_VENCIDO"] > 0
    assert puntual["SALDO_VENCIDO"] == 0
    assert moroso["SALDO_VIGENTE"] == 0
    assert puntual["SALDO_VIGENTE"] > 0


def test_morosidad_por_cliente_pct_vencido() -> None:
    """PCT_VENCIDO debe ser 1.0 cuando todo el saldo esta vencido."""
    filas = [_fila_venta("CLI", 10_000, saldo=10_000, delta_mora=60)]
    df = pd.DataFrame(filas)
    resultado = _calcular_morosidad_por_cliente(df, HOY)
    fila = resultado[resultado["NOMBRE_CLIENTE"] == "CLI"].iloc[0]
    assert float(fila["PCT_VENCIDO"]) == pytest.approx(1.0)


def test_morosidad_por_cliente_sin_ventas_devuelve_vacio() -> None:
    filas = [_fila_cobro("CLI", 5_000)]
    df = pd.DataFrame(filas)
    assert _calcular_morosidad_por_cliente(df, HOY).empty


def test_morosidad_por_cliente_tiene_fila_total() -> None:
    filas = [_fila_venta("A", 10_000, saldo=5_000, delta_mora=10)]
    df = pd.DataFrame(filas)
    resultado = _calcular_morosidad_por_cliente(df, HOY)
    assert "TOTAL" in resultado["NOMBRE_CLIENTE"].values
