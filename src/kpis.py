"""Módulo de KPIs estratégicos de Cuentas por Cobrar (CxC).

Calcula indicadores clave de desempeño tomando como fuente unica de verdad
la vista ``movimientos_totales_cxc`` ya procesada.

Filtro de Facturas:
    Todo calculo (DSO, CEI, Morosidad, Concentracion) filtra dinamicamente
    por TIPO_IMPTE = 'C' y CONCEPTO que contenga 'VENTA', usando el
    SALDO_FACTURA historico para asegurar cuadre de pagos parciales.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from src.utils import es_venta_mask as _es_venta

logger = logging.getLogger(__name__)


def generar_kpis(
    df_totales: pd.DataFrame,
    dias_periodo: int = 90,
) -> dict[str, pd.DataFrame]:
    """
    Orquesta el cálculo de todos los KPIs extrayendo directo de totales.

    Args:
        df_totales: DataFrame con los movimientos maestros.
        dias_periodo: Ventana de tiempo para el analisis (default: 90).

    Returns:
        Diccionario con DataFrames correspondientes a cada KPI.
    """
    hoy = pd.Timestamp(datetime.now().date())
    inicio_periodo = hoy - pd.Timedelta(days=dias_periodo)

    if df_totales.empty:
        return {}

    df = df_totales.copy()

    # Programacion defensiva: Asegurar dimensiones metricas criticas
    columnas_numericas = ["SALDO_FACTURA", "DELTA_MORA", "IMPORTE", "IMPUESTO", "LIMITE_CREDITO"]
    for col in columnas_numericas:
        if col not in df.columns:
            df[col] = 0.0

    if "MONEDA" in df.columns:
        df["MONEDA"] = df["MONEDA"].astype(str).str.strip().str.upper()

    if "CONCEPTO" in df.columns:
        df["CONCEPTO"] = df["CONCEPTO"].astype(str).str.strip().str.upper()

    if "FECHA_EMISION" in df.columns:
        df["FECHA_EMISION"] = pd.to_datetime(df["FECHA_EMISION"], errors="coerce")

    # Forzar casteo a numerico de las columnas garantizadas
    for col in columnas_numericas:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    resultados: dict[str, pd.DataFrame] = {}

    for moneda in ["MXN", "USD"]:
        sufijo = f"_{moneda.lower()}"
        df_moneda = df[df["MONEDA"] == moneda].copy() if "MONEDA" in df.columns else df.copy()

        dso_cei_mora = _calcular_kpis_macro(df_moneda, hoy, inicio_periodo, dias_periodo)

        kpi_vals = {row["KPI"]: row["VALOR"] for row in dso_cei_mora}
        dso_val = kpi_vals.get("DSO (Days Sales Outstanding)", 0.0)
        cei_val = kpi_vals.get("CEI (Collection Effectiveness Index)", 0.0)
        mora_val = kpi_vals.get("Índice de Morosidad", 0.0)
        logger.info(
            "KPIs %s — DSO: %.1f dias | CEI: %.2f%% | Morosidad: %.2f%%",
            moneda, dso_val,
            float(cei_val) * 100 if isinstance(cei_val, (int, float)) else 0.0,
            float(mora_val) * 100 if isinstance(mora_val, (int, float)) else 0.0,
        )

        concentracion = _calcular_concentracion(df_moneda)
        limite_credito = _calcular_limite_credito(df_moneda)
        morosidad_cliente = _calcular_morosidad_por_cliente(df_moneda, hoy)

        n_clientes = max(0, len(concentracion) - 1) if not concentracion.empty else 0
        n_sobre_limite = int(
            (limite_credito["ALERTA"] == "SOBRE_LIMITE").sum()
            if not limite_credito.empty and "ALERTA" in limite_credito.columns
            else 0
        )
        logger.info(
            "KPIs %s — Clientes activos: %d | Sobre limite: %d | Tablas: concentracion(%d), credito(%d), morosidad(%d)",
            moneda, n_clientes, n_sobre_limite,
            len(concentracion), len(limite_credito), len(morosidad_cliente),
        )

        resultados[f"kpis_resumen{sufijo}"] = pd.DataFrame(dso_cei_mora)
        resultados[f"kpis_concentracion{sufijo}"] = concentracion
        resultados[f"kpis_limite_credito{sufijo}"] = limite_credito
        resultados[f"kpis_morosidad_cliente{sufijo}"] = morosidad_cliente

    # Alias de compatibilidad estricta para pruebas de Nivel 1
    if "kpis_resumen_mxn" in resultados:
        resultados["kpis_resumen"] = resultados["kpis_resumen_mxn"]
        resultados["kpis_concentracion"] = resultados["kpis_concentracion_mxn"]
        resultados["kpis_limite_credito"] = resultados["kpis_limite_credito_mxn"]
        resultados["kpis_morosidad_cliente"] = resultados["kpis_morosidad_cliente_mxn"]

    return resultados


def _calcular_kpis_macro(
    df: pd.DataFrame,
    hoy: pd.Timestamp,
    inicio_periodo: pd.Timestamp,
    dias_periodo: int,
) -> list[dict[str, Any]]:
    """Calcula indicadores de alto nivel (DSO, CEI, Morosidad)."""
    mask_ventas = _es_venta(df)
    df_ventas = df[mask_ventas]

    saldo_total = df_ventas["SALDO_FACTURA"].sum()
    ventas_periodo = df_ventas.loc[df_ventas["FECHA_EMISION"] >= inicio_periodo, ["IMPORTE", "IMPUESTO"]].sum().sum()

    dso = (saldo_total / ventas_periodo) * dias_periodo if ventas_periodo > 0 else 0.0

    if "TIPO_IMPTE" in df.columns:
        es_abono = df["TIPO_IMPTE"] == "R"
        en_periodo = df["FECHA_EMISION"] >= inicio_periodo

        cobros_periodo = df.loc[es_abono & en_periodo, ["IMPORTE", "IMPUESTO"]].sum().sum()
        saldo_actual = df.loc[df["TIPO_IMPTE"] == "C", ["IMPORTE", "IMPUESTO"]].sum().sum() - df.loc[es_abono, ["IMPORTE", "IMPUESTO"]].sum().sum()
        cargos_periodo = df.loc[(df["TIPO_IMPTE"] == "C") & en_periodo, ["IMPORTE", "IMPUESTO"]].sum().sum()
    else:
        cobros_periodo = 0.0
        saldo_actual = 0.0
        cargos_periodo = 0.0

    saldo_inicio = saldo_actual - cargos_periodo + cobros_periodo
    cobrable = saldo_inicio + cargos_periodo

    cei = (cobros_periodo / cobrable) if cobrable > 0 else 1.0

    saldo_vencido = df_ventas.loc[df_ventas["DELTA_MORA"] > 0, "SALDO_FACTURA"].sum()
    morosidad = (saldo_vencido / saldo_total) if saldo_total > 0 else 0.0

    return [
        {
            "KPI": "DSO (Days Sales Outstanding)",
            "VALOR": round(dso, 1),
            "UNIDAD": "días",
            "INTERPRETACION": f"Saldo actual: ${saldo_total:,.2f} vs ${ventas_periodo:,.2f} facturado en {dias_periodo} días.",
        },
        {
            "KPI": "CEI (Collection Effectiveness Index)",
            "VALOR": cei,
            "UNIDAD": "%",
            "INTERPRETACION": f"Cobros: ${cobros_periodo:,.2f} de ${cobrable:,.2f} cobrable en el periodo.",
        },
        {
            "KPI": "Índice de Morosidad",
            "VALOR": morosidad,
            "UNIDAD": "%",
            "INTERPRETACION": f"Vencida: ${saldo_vencido:,.2f} de ${saldo_total:,.2f} total facturado.",
        },
    ]


def _calcular_concentracion(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula la concentracion de saldos mediante distribucion ABC."""
    mask_ventas = _es_venta(df)
    df_ventas = df[mask_ventas]

    if df_ventas.empty or "NOMBRE_CLIENTE" not in df_ventas.columns:
        return pd.DataFrame()

    por_cliente = df_ventas.groupby("NOMBRE_CLIENTE")["SALDO_FACTURA"].sum().round(2).reset_index(name="SALDO_PENDIENTE")

    mask_ceros = por_cliente["SALDO_PENDIENTE"] <= 0
    df_activos = por_cliente[~mask_ceros].sort_values("SALDO_PENDIENTE", ascending=False)
    df_ceros = por_cliente[mask_ceros].sort_values("NOMBRE_CLIENTE", ascending=True)
    por_cliente = pd.concat([df_activos, df_ceros], ignore_index=True)

    total = por_cliente["SALDO_PENDIENTE"].sum()
    if total <= 0:
        return pd.DataFrame()

    por_cliente["PCT_DEL_TOTAL"] = por_cliente["SALDO_PENDIENTE"] / total
    por_cliente["PCT_ACUMULADO"] = por_cliente["PCT_DEL_TOTAL"].cumsum()

    clasif = []
    for i, val in enumerate(por_cliente["PCT_ACUMULADO"]):
        val_pct = val * 100.0
        if i == 0 or val_pct <= 80.0:
            clasif.append("A")
        elif val_pct <= 95.0:
            clasif.append("B")
        else:
            clasif.append("C")
    por_cliente["CLASIFICACION"] = clasif

    if len(por_cliente) > 0:
        por_cliente.loc[por_cliente.index[-1], "PCT_ACUMULADO"] = 1.00

    tot_row = pd.DataFrame([{
        "NOMBRE_CLIENTE": "TOTAL",
        "SALDO_PENDIENTE": total,
        "PCT_DEL_TOTAL": 1.00,
        "PCT_ACUMULADO": "",
        "CLASIFICACION": ""
    }])
    return pd.concat([por_cliente, tot_row], ignore_index=True)[["NOMBRE_CLIENTE", "SALDO_PENDIENTE", "PCT_DEL_TOTAL", "PCT_ACUMULADO", "CLASIFICACION"]]


def _calcular_limite_credito(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula la utilizacion y disponibilidad sobre los limites de credito."""
    if "NOMBRE_CLIENTE" not in df.columns:
        return pd.DataFrame()

    mask_ventas = _es_venta(df)
    es_abono = df["TIPO_IMPTE"] == "R" if "TIPO_IMPTE" in df.columns else pd.Series(False, index=df.index)
    df["_MONTO"] = df["IMPORTE"] + df["IMPUESTO"]

    df_clientes = df.dropna(subset=["NOMBRE_CLIENTE"]).drop_duplicates("NOMBRE_CLIENTE")
    limites = df_clientes.set_index("NOMBRE_CLIENTE")["LIMITE_CREDITO"]
    estatus = df_clientes.set_index("NOMBRE_CLIENTE")["ESTATUS_CLIENTE"] if "ESTATUS_CLIENTE" in df.columns else pd.Series(dtype=str)

    cargos_agg = df[mask_ventas].groupby("NOMBRE_CLIENTE").agg(
        NUM_FACTURAS_TOTALES=("_MONTO", "count"),
        TOTAL_CARGOS=("_MONTO", "sum"),
        SALDO_PENDIENTE=("SALDO_FACTURA", "sum")
    ).reset_index()

    pagado = df[es_abono].groupby("NOMBRE_CLIENTE")["_MONTO"].sum().reset_index(name="TOTAL_ABONOS")

    resultado = cargos_agg.merge(limites, on="NOMBRE_CLIENTE", how="left")
    resultado = resultado.merge(pagado, on="NOMBRE_CLIENTE", how="left")

    resultado["ESTATUS_CLIENTE"] = resultado["NOMBRE_CLIENTE"].map(estatus).fillna("N/A")
    resultado["LIMITE_CREDITO"] = resultado["LIMITE_CREDITO"].fillna(0)
    resultado["NUM_FACTURAS_TOTALES"] = resultado["NUM_FACTURAS_TOTALES"].fillna(0).astype(int)
    resultado["TOTAL_CARGOS"] = resultado["TOTAL_CARGOS"].fillna(0).round(2)
    resultado["TOTAL_ABONOS"] = resultado["TOTAL_ABONOS"].fillna(0).round(2)
    resultado["SALDO_PENDIENTE"] = resultado["SALDO_PENDIENTE"].fillna(0).round(2)

    resultado["UTILIZACION_PCT"] = np.where(
        resultado["LIMITE_CREDITO"] > 0,
        (resultado["SALDO_PENDIENTE"] / resultado["LIMITE_CREDITO"]),
        np.nan,
    )
    resultado["DISPONIBLE"] = np.where(
        resultado["LIMITE_CREDITO"] > 0,
        (resultado["LIMITE_CREDITO"] - resultado["SALDO_PENDIENTE"]).round(2),
        0.0,
    )

    condiciones = [
        resultado["LIMITE_CREDITO"] == 0,
        resultado["UTILIZACION_PCT"] > 1.0,
        resultado["UTILIZACION_PCT"] >= 0.90,
        resultado["UTILIZACION_PCT"] >= 0.70,
    ]
    opciones = ["SIN_LIMITE", "SOBRE_LIMITE", "CRITICO", "ALTO"]
    resultado["ALERTA"] = np.select(condiciones, opciones, default="NORMAL")

    mask_ceros = resultado["SALDO_PENDIENTE"] <= 0
    df_activos = resultado[~mask_ceros].sort_values("SALDO_PENDIENTE", ascending=False)
    df_ceros = resultado[mask_ceros].sort_values("NOMBRE_CLIENTE", ascending=True)
    resultado = pd.concat([df_activos, df_ceros], ignore_index=True)

    cols = ["NOMBRE_CLIENTE", "ESTATUS_CLIENTE", "NUM_FACTURAS_TOTALES", "TOTAL_CARGOS", "TOTAL_ABONOS", "SALDO_PENDIENTE", "LIMITE_CREDITO", "UTILIZACION_PCT", "DISPONIBLE", "ALERTA"]
    resultado = resultado[cols]

    if resultado.empty:
        return resultado

    tot_row = pd.DataFrame([{
        "NOMBRE_CLIENTE": "TOTAL",
        "ESTATUS_CLIENTE": "",
        "NUM_FACTURAS_TOTALES": resultado["NUM_FACTURAS_TOTALES"].sum(),
        "TOTAL_CARGOS": resultado["TOTAL_CARGOS"].sum(),
        "TOTAL_ABONOS": resultado["TOTAL_ABONOS"].sum(),
        "SALDO_PENDIENTE": resultado["SALDO_PENDIENTE"].sum(),
        "LIMITE_CREDITO": "",
        "UTILIZACION_PCT": "",
        "DISPONIBLE": "",
        "ALERTA": ""
    }])
    return pd.concat([resultado, tot_row], ignore_index=True)


def _calcular_morosidad_por_cliente(df: pd.DataFrame, hoy: pd.Timestamp) -> pd.DataFrame:
    """Calcula volumen de facturacion sana contra carteras estancadas."""
    mask_ventas = _es_venta(df)
    df_ventas = df[mask_ventas].copy()

    if df_ventas.empty or "NOMBRE_CLIENTE" not in df_ventas.columns:
        return pd.DataFrame()

    df_ventas["_ES_PENDIENTE"] = df_ventas["SALDO_FACTURA"] > 0
    df_ventas["DIAS_VENCIDO"] = np.where(df_ventas["_ES_PENDIENTE"], df_ventas["DELTA_MORA"], 0)
    
    df_ventas["_VENCIDO"] = (df_ventas["_ES_PENDIENTE"]) & (df_ventas["DELTA_MORA"] > 0)
    df_ventas["_VIGENTE"] = (df_ventas["_ES_PENDIENTE"]) & (df_ventas["DELTA_MORA"] <= 0)
    
    df_ventas["_SALDO_VENCIDO"] = np.where(df_ventas["_VENCIDO"], df_ventas["SALDO_FACTURA"], 0.0)
    df_ventas["_SALDO_VIGENTE"] = np.where(df_ventas["_VIGENTE"], df_ventas["SALDO_FACTURA"], 0.0)

    por_cliente = df_ventas.groupby("NOMBRE_CLIENTE").agg(
        NUM_FACTURAS_TOTALES=("SALDO_FACTURA", "count"),
        NUM_FACTURAS_PENDIENTES=("_ES_PENDIENTE", "sum"),
        NUM_FACTURAS_VIGENTES=("_VIGENTE", "sum"),
        NUM_FACTURAS_VENCIDAS=("_VENCIDO", "sum"),
        SALDO_PENDIENTE=("SALDO_FACTURA", "sum"),
        SALDO_VIGENTE=("_SALDO_VIGENTE", "sum"),
        SALDO_VENCIDO=("_SALDO_VENCIDO", "sum"),
        DIAS_VENCIDO_MAX=("DIAS_VENCIDO", "max"),
    ).reset_index()

    por_cliente["SALDO_PENDIENTE"] = por_cliente["SALDO_PENDIENTE"].round(2)
    por_cliente["SALDO_VIGENTE"] = por_cliente["SALDO_VIGENTE"].round(2)
    por_cliente["SALDO_VENCIDO"] = por_cliente["SALDO_VENCIDO"].round(2)

    for c in ["NUM_FACTURAS_TOTALES", "NUM_FACTURAS_PENDIENTES", "NUM_FACTURAS_VIGENTES", "NUM_FACTURAS_VENCIDAS", "DIAS_VENCIDO_MAX"]:
        por_cliente[c] = por_cliente[c].astype(int)

    por_cliente["PCT_VENCIDO"] = np.where(
        por_cliente["SALDO_PENDIENTE"] > 0,
        (por_cliente["SALDO_VENCIDO"] / por_cliente["SALDO_PENDIENTE"]),
        0.0,
    )

    mask_ceros = por_cliente["SALDO_PENDIENTE"] <= 0
    df_activos = por_cliente[~mask_ceros].sort_values("SALDO_PENDIENTE", ascending=False)
    df_ceros = por_cliente[mask_ceros].sort_values("NOMBRE_CLIENTE", ascending=True)
    por_cliente = pd.concat([df_activos, df_ceros], ignore_index=True)

    cols = ["NOMBRE_CLIENTE", "SALDO_PENDIENTE", "SALDO_VIGENTE", "SALDO_VENCIDO", "PCT_VENCIDO", "NUM_FACTURAS_TOTALES", "NUM_FACTURAS_PENDIENTES", "NUM_FACTURAS_VIGENTES", "NUM_FACTURAS_VENCIDAS", "DIAS_VENCIDO_MAX"]
    por_cliente = por_cliente[[c for c in cols if c in por_cliente.columns]]

    if por_cliente.empty:
        return por_cliente

    tot_row = pd.DataFrame([{
        "NOMBRE_CLIENTE": "TOTAL",
        "SALDO_PENDIENTE": por_cliente["SALDO_PENDIENTE"].sum(),
        "SALDO_VIGENTE": por_cliente["SALDO_VIGENTE"].sum(),
        "SALDO_VENCIDO": por_cliente["SALDO_VENCIDO"].sum(),
        "PCT_VENCIDO": "",
        "NUM_FACTURAS_TOTALES": por_cliente["NUM_FACTURAS_TOTALES"].sum(),
        "NUM_FACTURAS_PENDIENTES": por_cliente["NUM_FACTURAS_PENDIENTES"].sum(),
        "NUM_FACTURAS_VIGENTES": por_cliente["NUM_FACTURAS_VIGENTES"].sum(),
        "NUM_FACTURAS_VENCIDAS": por_cliente["NUM_FACTURAS_VENCIDAS"].sum(),
        "DIAS_VENCIDO_MAX": ""
    }])
    return pd.concat([por_cliente, tot_row], ignore_index=True)