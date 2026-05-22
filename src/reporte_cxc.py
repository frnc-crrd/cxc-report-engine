"""Generador de reporte operativo de Cuentas por Cobrar (CxC).

Toma los datos crudos del query maestro de Microsip y genera:

- ``reporte_cxc``: Movimientos filtrados (sin cancelados, sin tipo A o T)
  con saldo por factura, saldo acumulado por cliente y metricas
  de ciclo de cobranza.
- ``por_acreditar``: Ecosistema de anticipos (TIPO_IMPTE = 'A' y 'T')
  que representan saldos a favor o devoluciones pendientes de aplicar.
- ``facturas_abiertas``: Cargos con saldo pendiente (SALDO_FACTURA >= 0.01)
  mas sus abonos parciales. Agrupa cada factura con sus cobros
  aplicados mediante bandas alternas de color.
- ``facturas_cerradas``: Cargos completamente cobrados o sobrepagados
  (SALDO_FACTURA < 0.01) mas todos sus abonos vinculados. Incluye
  DELTA_RECAUDO y CATEGORIA_RECAUDO para analizar el comportamiento.
- ``movimientos_reales_totales``: Union de facturas abiertas y cerradas
  con todas las columnas del reporte operativo, incluyendo
  SALDO_CLIENTE, ambas categorias y ambos deltas.

Logica de saldos (moneda original, sin conversion a MXN):
    ``SALDO_FACTURA``  = (IMPORTE + IMPUESTO) del cargo menos la suma de
                         (IMPORTE + IMPUESTO) de abonos vinculados por
                         ``DOCTO_CC_ACR_ID``. Solo se calcula para
                         movimientos tipo ``C``; el resto queda como NULL.
    ``SALDO_CLIENTE``  = suma acumulada de movimientos por cliente
                         (``C`` suma, ``R`` resta).

Metricas de ciclo de cobranza (solo en filas de cargo):
    ``DELTA_RECAUDO``  = Fecha del ultimo abono menos FECHA_VENCIMIENTO.
                         Solo para facturas pagadas (SALDO < 0.01).
                         Negativo = pago anticipado, positivo = dias
                         de retraso real.
    ``CATEGORIA_RECAUDO`` = Clasificacion del comportamiento de pago segun
                         DELTA_RECAUDO (Mapeado de settings).
    ``DELTA_MORA``     = HOY menos FECHA_VENCIMIENTO.
                         Solo para facturas abiertas (SALDO >= 0.01).
    ``CATEGORIA_MORA`` = Clasificacion del riesgo segun DELTA_MORA
                         (Mapeado de settings).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from config.settings import CANCELADO_VALUES, RANGOS_ANTIGUEDAD, RANGOS_RECAUDO

logger = logging.getLogger(__name__)

# ======================================================================
# COLUMNAS DE CADA VISTA
# ======================================================================

COLUMNAS_REPORTE: list[str] = [
    "NOMBRE_CLIENTE",
    "MONEDA",
    "CONDICIONES",
    "ESTATUS_CLIENTE",
    "CONCEPTO",
    "FOLIO",
    "FECHA_EMISION",
    "FECHA_VENCIMIENTO",
    "DESCRIPCION",
    "TIPO_IMPTE",
    "CARGOS",
    "ABONOS",
    "IMPORTE",
    "IMPUESTO",
    "SALDO_FACTURA",
    "SALDO_CLIENTE",
    "DELTA_RECAUDO",
    "CATEGORIA_RECAUDO",
    "DELTA_MORA",
    "CATEGORIA_MORA",
]

COLUMNAS_POR_ACREDITAR: list[str] = [
    c for c in COLUMNAS_REPORTE
    if c not in (
        "CONDICIONES", "FECHA_VENCIMIENTO", "CARGOS", "IMPUESTO",
        "SALDO_FACTURA", "SALDO_CLIENTE",
        "DELTA_RECAUDO", "CATEGORIA_RECAUDO",
        "DELTA_MORA", "CATEGORIA_MORA",
    )
]

COLUMNAS_FACTURAS_ABIERTAS: list[str] = [
    "NOMBRE_CLIENTE",
    "MONEDA",
    "CONDICIONES",
    "ESTATUS_CLIENTE",
    "CONCEPTO",
    "FOLIO",
    "FECHA_EMISION",
    "FECHA_VENCIMIENTO",
    "DESCRIPCION",
    "CARGOS",
    "ABONOS",
    "IMPORTE",
    "IMPUESTO",
    "SALDO_FACTURA",
    "DELTA_MORA",
    "CATEGORIA_MORA",
]

COLUMNAS_FACTURAS_CERRADAS: list[str] = [
    "NOMBRE_CLIENTE",
    "MONEDA",
    "CONDICIONES",
    "ESTATUS_CLIENTE",
    "CONCEPTO",
    "FOLIO",
    "FECHA_EMISION",
    "FECHA_VENCIMIENTO",
    "DESCRIPCION",
    "CARGOS",
    "ABONOS",
    "IMPORTE",
    "IMPUESTO",
    "SALDO_FACTURA",
    "DELTA_RECAUDO",
    "CATEGORIA_RECAUDO",
]

COLUMNAS_MOVIMIENTOS_TOTALES: list[str] = [
    "NOMBRE_CLIENTE",
    "MONEDA",
    "CONDICIONES",
    "ESTATUS_CLIENTE",
    "CONCEPTO",
    "FOLIO",
    "FECHA_EMISION",
    "FECHA_VENCIMIENTO",
    "DESCRIPCION",
    "TIPO_IMPTE",
    "CARGOS",
    "ABONOS",
    "IMPORTE",
    "IMPUESTO",
    "SALDO_FACTURA",
    "SALDO_CLIENTE",
    "DELTA_RECAUDO",
    "CATEGORIA_RECAUDO",
    "DELTA_MORA",
    "CATEGORIA_MORA",
]


# ======================================================================
# FUNCION PRINCIPAL
# ======================================================================

def generar_reporte_cxc(df_crudo: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Genera el reporte operativo completo de CxC."""
    df = _preparar(df_crudo)

    por_acreditar = _obtener_por_acreditar(df)
    df_filtrado = _filtrar_movimientos(df)
    df_filtrado = _calcular_saldo_factura(df_filtrado)
    df_filtrado = _calcular_metricas_ciclo(df_filtrado)
    df_filtrado = _calcular_saldo_cliente(df_filtrado)

    facturas_abiertas_raw = _extraer_facturas_abiertas(df_filtrado)
    facturas_cerradas_raw = _extraer_facturas_cerradas(df_filtrado)

    facturas_abiertas = _seleccionar_columnas(
        facturas_abiertas_raw, COLUMNAS_FACTURAS_ABIERTAS,
    )
    if "_BAND_GROUP" in facturas_abiertas_raw.columns:
        facturas_abiertas["_BAND_GROUP"] = facturas_abiertas_raw["_BAND_GROUP"].values

    facturas_cerradas = _seleccionar_columnas(
        facturas_cerradas_raw, COLUMNAS_FACTURAS_CERRADAS,
    )
    if "_BAND_GROUP" in facturas_cerradas_raw.columns:
        facturas_cerradas["_BAND_GROUP"] = facturas_cerradas_raw["_BAND_GROUP"].values

    df_filtrado = agregar_bandas_grupo(df_filtrado)

    reporte = _seleccionar_columnas(df_filtrado, COLUMNAS_REPORTE)
    if "_BAND_GROUP" in df_filtrado.columns:
        reporte["_BAND_GROUP"] = df_filtrado["_BAND_GROUP"].values

    movimientos_totales = _agregar_zscores(df_filtrado)

    por_acreditar = _seleccionar_columnas(por_acreditar, COLUMNAS_POR_ACREDITAR)

    n_clientes = reporte["NOMBRE_CLIENTE"].nunique() if "NOMBRE_CLIENTE" in reporte.columns else 0
    logger.info("Reporte generado: %d filas, %d clientes", len(reporte), n_clientes)

    return {
        "reporte_cxc":               reporte,
        "por_acreditar":             por_acreditar,
        "movimientos_abiertos_cxc":  facturas_abiertas,
        "movimientos_cerrados_cxc":  facturas_cerradas,
        "movimientos_totales_cxc":   movimientos_totales,
    }


# ======================================================================
# FUNCIONES INTERNAS — ENRIQUECIMIENTO CON Z-SCORES
# ======================================================================

def _aplicar_zscore(
    df: pd.DataFrame,
    columna: str,
    umbral: float,
    mask: "pd.Series | None" = None,
) -> pd.DataFrame:
    """Inserta ZSCORE_{columna} y ATIPICO_{columna} justo después de columna."""
    df[f"ZSCORE_{columna}"] = np.nan
    df[f"ATIPICO_{columna}"] = None
    if columna in df.columns:
        m = mask if mask is not None else df[columna].notna()
        vals = df.loc[m, columna].dropna()
        if len(vals) >= 3 and vals.std() > 0:
            z = np.abs((df.loc[m, columna] - vals.mean()) / vals.std())
            df.loc[m, f"ZSCORE_{columna}"] = z.round(4)
            df.loc[m, f"ATIPICO_{columna}"] = z >= umbral
    df = _insertar_columna_despues(df, columna, f"ZSCORE_{columna}", df.pop(f"ZSCORE_{columna}"))
    df = _insertar_columna_despues(df, f"ZSCORE_{columna}", f"ATIPICO_{columna}", df.pop(f"ATIPICO_{columna}"))
    return df


def _insertar_columna_despues(
    df: pd.DataFrame, referencia: str, nombre: str, valores: "pd.Series",
) -> pd.DataFrame:
    if referencia in df.columns:
        pos = df.columns.tolist().index(referencia) + 1
    else:
        pos = len(df.columns)
    df.insert(pos, nombre, valores)
    return df


def _agregar_zscores(df: pd.DataFrame, umbral: float = 3.0) -> pd.DataFrame:
    df = df.copy()

    if "SALDO_CLIENTE" in df.columns and "DELTA_RECAUDO" in df.columns:
        saldo_cliente = df.pop("SALDO_CLIENTE")
        pos = df.columns.tolist().index("DELTA_RECAUDO")
        df.insert(pos, "SALDO_CLIENTE", saldo_cliente)

    _COLS_TRAZABILIDAD: list[str] = [
        "USUARIO_CREADOR", "FECHA_HORA_CREACION", "USUARIO_ULT_MODIF",
        "FECHA_HORA_ULT_MODIF", "USUARIO_CANCELACION", "FECHA_HORA_CANCELACION",
    ]
    presentes = [c for c in _COLS_TRAZABILIDAD if c in df.columns]
    if presentes:
        cols_resto = [c for c in df.columns if c not in presentes]
        df = df[cols_resto + presentes]

    mask_ventas = (df["TIPO_IMPTE"] == "C") if "TIPO_IMPTE" in df.columns else None
    df = _aplicar_zscore(df, "IMPORTE", umbral, mask=mask_ventas)
    df = _aplicar_zscore(df, "DELTA_RECAUDO", umbral)
    df = _aplicar_zscore(df, "DELTA_MORA", umbral)

    return df


# ======================================================================
# FUNCIONES INTERNAS — PREPARACION
# ======================================================================

def _preparar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = pd.Index([c.upper().strip() for c in df.columns])

    for col in ["FECHA_EMISION", "FECHA_VENCIMIENTO"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in ["IMPORTE", "IMPUESTO", "CARGOS", "ABONOS"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "TIPO_IMPTE" in df.columns:
        df["TIPO_IMPTE"] = df["TIPO_IMPTE"].astype(str).str.strip().str.upper()

    return df

def _seleccionar_columnas(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    cols: list[str] = [c for c in columnas if c in df.columns]
    return df[cols].copy()

def _obtener_por_acreditar(df: pd.DataFrame) -> pd.DataFrame:
    if "TIPO_IMPTE" not in df.columns:
        return pd.DataFrame()

    # Integracion del ecosistema completo de anticipos y devoluciones
    df_a = df[df["TIPO_IMPTE"].isin(["A", "T"])].copy()

    if "CANCELADO" in df_a.columns:
        df_a = df_a[~df_a["CANCELADO"].isin(CANCELADO_VALUES)]

    return df_a

def _filtrar_movimientos(df: pd.DataFrame) -> pd.DataFrame:
    # Boolean indexing crea un nuevo df en cada paso; no se necesita .copy() previo.
    df_f = df
    if "CANCELADO" in df.columns:
        df_f = df_f[~df_f["CANCELADO"].isin(CANCELADO_VALUES)]
    if "TIPO_IMPTE" in df.columns:
        df_f = df_f[~df_f["TIPO_IMPTE"].isin(["A", "T"])]
    return df_f.reset_index(drop=True)


# ======================================================================
# FUNCIONES INTERNAS — CALCULOS DE SALDO
# ======================================================================

def _calcular_saldo_factura(df: pd.DataFrame) -> pd.DataFrame:
    # El caller (generar_reporte_cxc) ya transfiere ownership del df a esta funcion;
    # las columnas temporales se agregan in-place y se eliminan al retornar.
    df["SALDO_FACTURA"] = np.nan
    df["_MONTO"] = df["IMPORTE"] + df["IMPUESTO"]

    es_cargo: pd.Series = df["TIPO_IMPTE"] == "C"
    es_abono: pd.Series = df["TIPO_IMPTE"] == "R"

    if "DOCTO_CC_ACR_ID" in df.columns and "DOCTO_CC_ID" in df.columns:
        abonos_por_cargo: pd.Series = (
            df.loc[es_abono & df["DOCTO_CC_ACR_ID"].notna()]
            .groupby("DOCTO_CC_ACR_ID")["_MONTO"]
            .sum()
        )
        cargo_ids = df.loc[es_cargo, "DOCTO_CC_ID"]
        df.loc[es_cargo, "SALDO_FACTURA"] = (
            df.loc[es_cargo, "_MONTO"].values - cargo_ids.map(abonos_por_cargo).fillna(0).values
        )
    else:
        df.loc[es_cargo, "SALDO_FACTURA"] = df.loc[es_cargo, "_MONTO"]

    df["SALDO_FACTURA"] = df["SALDO_FACTURA"].round(2)
    return df.drop(columns=["_MONTO"])

def _calcular_saldo_cliente(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols = [c for c in ["NOMBRE_CLIENTE", "DOCTO_CC_ACR_ID", "DOCTO_CC_ID", "FECHA_EMISION"] if c in df.columns]
    df = df.sort_values(sort_cols, ascending=[True] * len(sort_cols), na_position="first").reset_index(drop=True)

    monto: pd.Series = df["IMPORTE"] + df["IMPUESTO"]
    movimiento: pd.Series = pd.Series(
        np.where(df["TIPO_IMPTE"] == "C", monto, np.where(df["TIPO_IMPTE"] == "R", -monto, 0)),
        index=df.index,
    )

    df["SALDO_CLIENTE"] = movimiento.groupby(df["NOMBRE_CLIENTE"]).cumsum().round(2)
    return df


# ======================================================================
# FUNCIONES INTERNAS — METRICAS DE CICLO
# ======================================================================

def _calcular_metricas_ciclo(df: pd.DataFrame) -> pd.DataFrame:
    # El df recibido ya es propiedad exclusiva de la cadena de calculo;
    # se agregan las columnas de ciclo directamente sin copia previa.
    hoy = pd.Timestamp.now().normalize()

    es_cargo: pd.Series = df["TIPO_IMPTE"] == "C"
    es_abono: pd.Series = df["TIPO_IMPTE"] == "R"

    df["DELTA_RECAUDO"] = np.nan
    df["CATEGORIA_RECAUDO"] = ""
    df["DELTA_MORA"] = np.nan
    df["CATEGORIA_MORA"] = ""

    # Tolerancia estricta de punto flotante < 0.01 para mitigar fallos de base de datos
    pagadas = es_cargo & (df["SALDO_FACTURA"].fillna(0) < 0.01)

    if pagadas.any() and "DOCTO_CC_ACR_ID" in df.columns and "DOCTO_CC_ID" in df.columns and "FECHA_EMISION" in df.columns:
        ultimo_abono: pd.Series = df.loc[es_abono & df["DOCTO_CC_ACR_ID"].notna()].groupby("DOCTO_CC_ACR_ID")["FECHA_EMISION"].max()
        fecha_ultimo = df.loc[pagadas, "DOCTO_CC_ID"].map(ultimo_abono)
        
        _delta = fecha_ultimo - df.loc[pagadas, "FECHA_VENCIMIENTO"]
        recaudo_dias = (_delta.dt.total_seconds() / 86400.0).to_numpy()
        df.loc[pagadas, "DELTA_RECAUDO"] = recaudo_dias

        cond_recaudo = []
        cat_recaudo = []
        for min_d, max_d, label in RANGOS_RECAUDO:
            if min_d is None and max_d is not None:
                cond_recaudo.append(recaudo_dias <= max_d)
            elif max_d is None and min_d is not None:
                cond_recaudo.append(recaudo_dias >= min_d)
            else:
                assert min_d is not None and max_d is not None
                cond_recaudo.append((recaudo_dias >= min_d) & (recaudo_dias <= max_d))
            cat_recaudo.append(label)

        df.loc[pagadas, "CATEGORIA_RECAUDO"] = np.select(cond_recaudo, cat_recaudo, default="")

    # Tolerancia estricta de punto flotante >= 0.01
    abiertas = es_cargo & (df["SALDO_FACTURA"].fillna(0) >= 0.01)

    if abiertas.any() and "FECHA_VENCIMIENTO" in df.columns:
        mora_dias = (hoy - df.loc[abiertas, "FECHA_VENCIMIENTO"]).dt.days
        df.loc[abiertas, "DELTA_MORA"] = mora_dias

        cond_mora = []
        cat_mora = []
        for min_d, max_d, label in RANGOS_ANTIGUEDAD:
            if min_d is None and max_d is not None:
                cond_mora.append(mora_dias <= max_d)
            elif max_d is None and min_d is not None:
                cond_mora.append(mora_dias >= min_d)
            else:
                cond_mora.append((mora_dias >= min_d) & (mora_dias <= max_d))
            cat_mora.append(label)

        df.loc[abiertas, "CATEGORIA_MORA"] = np.select(cond_mora, cat_mora, default="")

    return df


# ======================================================================
# FUNCIONES INTERNAS — VISTAS DE FACTURAS
# ======================================================================

def agregar_bandas_grupo(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega la columna ``_BAND_GROUP`` para el sombreado alternado en Excel/PDF.

    Cada cargo (TIPO_IMPTE='C') y sus abonos asociados comparten el mismo
    valor en ``_BAND_GROUP``, lo que permite a las hojas de salida pintar
    una banda de color por grupo factura+abonos.

    Args:
        df: DataFrame con al menos las columnas ``DOCTO_CC_ID`` y ``TIPO_IMPTE``.

    Returns:
        Copia del DataFrame con las columnas ``_GRUPO_CARGO`` y ``_BAND_GROUP``
        agregadas. Si faltan las columnas requeridas, ``_BAND_GROUP`` queda en 0.
    """
    if "DOCTO_CC_ID" not in df.columns or "TIPO_IMPTE" not in df.columns:
        df = df.copy()
        df["_BAND_GROUP"] = 0
        return df

    df = df.copy()

    acr_id = df.get("DOCTO_CC_ACR_ID", df["DOCTO_CC_ID"])
    if isinstance(acr_id, pd.Series) and "DOCTO_CC_ID" in df.columns:
        acr_id = acr_id.fillna(df["DOCTO_CC_ID"])

    df["_GRUPO_CARGO"] = np.where(df["TIPO_IMPTE"] == "C", df["DOCTO_CC_ID"], acr_id)

    sort_cols = [c for c in ["NOMBRE_CLIENTE", "_GRUPO_CARGO", "TIPO_IMPTE", "FECHA_EMISION"] if c in df.columns]
    df = df.sort_values(sort_cols, ascending=[True] * len(sort_cols), na_position="first").reset_index(drop=True)

    cambio = df["_GRUPO_CARGO"] != df["_GRUPO_CARGO"].shift()
    df["_BAND_GROUP"] = cambio.cumsum() % 2

    return df.drop(columns=["_GRUPO_CARGO"])

def _extraer_facturas_abiertas(df: pd.DataFrame) -> pd.DataFrame:
    if "SALDO_FACTURA" not in df.columns or "TIPO_IMPTE" not in df.columns:
        return pd.DataFrame()

    cargos_abiertos = df[(df["TIPO_IMPTE"] == "C") & (df["SALDO_FACTURA"].fillna(0) >= 0.01)]

    if cargos_abiertos.empty:
        return pd.DataFrame()

    ids_abiertos: set[Any] = set()
    if "DOCTO_CC_ID" in cargos_abiertos.columns:
        ids_abiertos = set(cargos_abiertos["DOCTO_CC_ID"].dropna())

    abonos_parciales = pd.DataFrame()
    if ids_abiertos and "DOCTO_CC_ACR_ID" in df.columns:
        abonos_parciales = df[(df["TIPO_IMPTE"] == "R") & (df["DOCTO_CC_ACR_ID"].isin(ids_abiertos))]

    resultado = pd.concat([cargos_abiertos, abonos_parciales], ignore_index=True)
    resultado = agregar_bandas_grupo(resultado)

    return resultado

def _extraer_facturas_cerradas(df: pd.DataFrame) -> pd.DataFrame:
    if "SALDO_FACTURA" not in df.columns or "TIPO_IMPTE" not in df.columns:
        return pd.DataFrame()

    cargos_cerrados = df[(df["TIPO_IMPTE"] == "C") & (df["SALDO_FACTURA"].fillna(0) < 0.01)]

    if cargos_cerrados.empty:
        return pd.DataFrame()

    ids_cerrados: set[Any] = set()
    if "DOCTO_CC_ID" in cargos_cerrados.columns:
        ids_cerrados = set(cargos_cerrados["DOCTO_CC_ID"].dropna())

    abonos_completos = pd.DataFrame()
    if ids_cerrados and "DOCTO_CC_ACR_ID" in df.columns:
        abonos_completos = df[(df["TIPO_IMPTE"] == "R") & (df["DOCTO_CC_ACR_ID"].isin(ids_cerrados))]

    resultado = pd.concat([cargos_cerrados, abonos_completos], ignore_index=True)
    resultado = agregar_bandas_grupo(resultado)

    return resultado