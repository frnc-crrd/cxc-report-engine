"""Modulo de analisis de cartera de Cuentas por Cobrar (CxC).

Genera los reportes a partir de las vistas ya procesadas por el
pipeline operativo, utilizando exclusivamente ``movimientos_totales_cxc``
como fuente unica de verdad para garantizar que los saldos y pagos parciales
cuadren a la perfeccion.

Filtro de Facturas:
    Todo calculo que involucre "Facturas" exige estrictamente que el
    registro sea un cargo (TIPO_IMPTE == 'C') y que su CONCEPTO
    contenga la palabra "VENTA".
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.utils import es_venta_mask

logger = logging.getLogger(__name__)

_SIN_CONCEPTO: str = "Sin concepto asignado"


class Analytics:
    """Motor de analisis de cartera CxC."""

    def __init__(
        self,
        rangos_antiguedad: list[tuple[int | None, int | None, str]],
    ) -> None:
        """Inicializa el motor de analitica con los rangos de antiguedad.

        Args:
            rangos_antiguedad: Lista de tuplas ``(min_dias, max_dias, etiqueta)``
                tal como se define en ``settings.RANGOS_ANTIGUEDAD``.
        """
        self.rangos_antiguedad = rangos_antiguedad

    # ==================================================================
    # METODO PRINCIPAL
    # ==================================================================

    def run_analytics(
        self,
        vistas: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """Ejecuta todos los analisis a partir de las vistas maestras."""
        df_totales    = self._preparar(vistas.get("movimientos_totales_cxc", pd.DataFrame()))
        df_ajustes    = self._preparar(vistas.get("registros_por_acreditar_cxc", pd.DataFrame()))
        df_cancelados = self._preparar(vistas.get("registros_cancelados_cxc", pd.DataFrame()))

        resultados: dict[str, pd.DataFrame] = {
            "cartera_vencida_vs_vigente_mxn": self._cartera_vencida_vs_vigente(df_totales, "MXN"),
            "cartera_vencida_vs_vigente_usd": self._cartera_vencida_vs_vigente(df_totales, "USD"),
            "antiguedad_cartera_mxn":         self._antiguedad_cartera(df_totales, "MXN"),
            "antiguedad_cartera_usd":         self._antiguedad_cartera(df_totales, "USD"),
            "antiguedad_por_cliente_mxn":     self._antiguedad_por_cliente(df_totales, "MXN"),
            "antiguedad_por_cliente_usd":     self._antiguedad_por_cliente(df_totales, "USD"),
            "resumen_concepto_cxc_mxn":       self._resumen_por_concepto(df_totales, "MXN"),
            "resumen_concepto_cxc_usd":       self._resumen_por_concepto(df_totales, "USD"),
            "resumen_cancelados_cxc_mxn":     self._resumen_cancelados(df_cancelados, "MXN"),
            "resumen_cancelados_cxc_usd":     self._resumen_cancelados(df_cancelados, "USD"),
            "resumen_ajustes_cxc_mxn":        self._resumen_ajustes(df_ajustes, "MXN"),
            "resumen_ajustes_cxc_usd":        self._resumen_ajustes(df_ajustes, "USD"),
            # Adiciones para visualizacion y retrocompatibilidad
            "tendencia_mensual_mxn":          self._tendencia_mensual(df_totales, "MXN"),
            "tendencia_mensual_usd":          self._tendencia_mensual(df_totales, "USD"),
            "resumen_por_vendedor_mxn":       self._resumen_por_vendedor(df_totales, "MXN"),
            "resumen_por_vendedor_usd":       self._resumen_por_vendedor(df_totales, "USD"),
        }

        for nombre, df in resultados.items():
            logger.info("Analisis '%s': %d filas.", nombre, len(df))

        return resultados

    # ==================================================================
    # PREPARACION
    # ==================================================================

    def _preparar(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        df = df.copy()
        df.columns = pd.Index([c.upper().strip() for c in df.columns])

        if "_BAND_GROUP" in df.columns:
            df = df.drop(columns=["_BAND_GROUP"])

        df = df.reset_index(drop=True)

        for col in ["FECHA_EMISION", "FECHA_VENCIMIENTO"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        for col in ["IMPORTE", "IMPUESTO", "CARGOS", "ABONOS", "SALDO_FACTURA", "DELTA_MORA"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        if "TIPO_IMPTE" in df.columns:
            df["TIPO_IMPTE"] = df["TIPO_IMPTE"].astype(str).str.strip().str.upper()
        if "MONEDA" in df.columns:
            df["MONEDA"] = df["MONEDA"].astype(str).str.strip().str.upper()

        if "CONCEPTO" in df.columns:
            df["CONCEPTO"] = df["CONCEPTO"].fillna(_SIN_CONCEPTO).astype(str).str.strip().str.upper()
        else:
            df["CONCEPTO"] = _SIN_CONCEPTO

        return df

    def _es_venta(self, df: pd.DataFrame) -> pd.Series:
        return es_venta_mask(df)

    def _monto(self, df: pd.DataFrame) -> pd.Series:
        imp = df["IMPORTE"]  if "IMPORTE"  in df.columns else pd.Series(0.0, index=df.index)
        tax = df["IMPUESTO"] if "IMPUESTO" in df.columns else pd.Series(0.0, index=df.index)
        return imp + tax

    def _bucket_mora(self, df: pd.DataFrame) -> pd.Series:
        mora = df["DELTA_MORA"]
        condiciones = []
        etiquetas   = []

        for min_d, max_d, label in self.rangos_antiguedad:
            if min_d is None and max_d is not None:
                condiciones.append(mora <= max_d)
            elif max_d is None and min_d is not None:
                condiciones.append(mora >= min_d)
            elif min_d is not None and max_d is not None:
                condiciones.append((mora >= min_d) & (mora <= max_d))
            etiquetas.append(label)

        return pd.Series(np.select(condiciones, etiquetas, default="Fuera de rango"), index=df.index)

    # ==================================================================
    # ANTIGUEDAD DE CARTERA
    # ==================================================================

    def _antiguedad_cartera(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        
        mask_ventas_abiertas = self._es_venta(df) & (df["SALDO_FACTURA"] > 0)
        cargos = df[mask_ventas_abiertas].copy()

        if cargos.empty:
            return pd.DataFrame()

        cargos["_RANGO"] = self._bucket_mora(cargos)
        cargos["_SALDO"] = cargos["SALDO_FACTURA"]

        orden_rangos = [r[2] for r in self.rangos_antiguedad]

        agrupado = (
            cargos.groupby("_RANGO")["_SALDO"]
            .agg(NUM_FACTURAS_PENDIENTES="count", SALDO_PENDIENTE="sum")
            .reset_index()
            .rename(columns={"_RANGO": "RANGO_ANTIGUEDAD"})
        )

        total_saldo = agrupado["SALDO_PENDIENTE"].sum()
        agrupado["PCT_DEL_TOTAL"] = (agrupado["SALDO_PENDIENTE"] / total_saldo) if total_saldo > 0 else 0.0
        agrupado["SALDO_PENDIENTE"] = agrupado["SALDO_PENDIENTE"].round(2)

        agrupado["RANGO_ANTIGUEDAD"] = pd.Categorical(
            agrupado["RANGO_ANTIGUEDAD"], categories=orden_rangos, ordered=True,
        )
        agrupado = agrupado.sort_values("RANGO_ANTIGUEDAD").reset_index(drop=True)
        agrupado["RANGO_ANTIGUEDAD"] = agrupado["RANGO_ANTIGUEDAD"].astype(str)

        tot_row = pd.DataFrame([{
            "RANGO_ANTIGUEDAD": "TOTAL",
            "NUM_FACTURAS_PENDIENTES": agrupado["NUM_FACTURAS_PENDIENTES"].sum(),
            "SALDO_PENDIENTE": total_saldo,
            "PCT_DEL_TOTAL": 1.0 if total_saldo > 0 else 0.0
        }])
        
        return pd.concat([agrupado, tot_row], ignore_index=True)

    # ==================================================================
    # ANTIGUEDAD POR CLIENTE
    # ==================================================================

    def _antiguedad_por_cliente(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty or "NOMBRE_CLIENTE" not in df_totales.columns:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        
        es_cargo_venta = self._es_venta(df)
        es_abono = df["TIPO_IMPTE"] == "R"
        
        if df[es_cargo_venta].empty:
            return pd.DataFrame()

        df["_MONTO"] = self._monto(df)

        total_cargos = df[es_cargo_venta].groupby("NOMBRE_CLIENTE")["_MONTO"].sum().rename("TOTAL_CARGOS")
        total_abonos = df[es_abono].groupby("NOMBRE_CLIENTE")["_MONTO"].sum().rename("TOTAL_ABONOS")
        
        num_facturas_totales = df[es_cargo_venta].groupby("NOMBRE_CLIENTE").size().rename("NUM_FACTURAS_TOTALES")

        abiertas = df[es_cargo_venta & (df["SALDO_FACTURA"] > 0)].copy()
        num_facturas_pendientes = abiertas.groupby("NOMBRE_CLIENTE").size().rename("NUM_FACTURAS_PENDIENTES")
        saldo_pendiente = abiertas.groupby("NOMBRE_CLIENTE")["SALDO_FACTURA"].sum().rename("SALDO_PENDIENTE")

        estatus_map = df[es_cargo_venta].dropna(subset=["NOMBRE_CLIENTE", "ESTATUS_CLIENTE"]).groupby("NOMBRE_CLIENTE")["ESTATUS_CLIENTE"].first().to_dict() if "ESTATUS_CLIENTE" in df.columns else {}

        pivot_rows: dict[str, dict[str, float]] = {}
        if not abiertas.empty:
            abiertas["_RANGO"] = self._bucket_mora(abiertas)
            for cliente, grupo in abiertas.groupby("NOMBRE_CLIENTE"):
                pivot_rows[str(cliente)] = {}
                for _, _, label in self.rangos_antiguedad:
                    pivot_rows[str(cliente)][label] = float(grupo.loc[grupo["_RANGO"] == label, "SALDO_FACTURA"].sum())

        cols_pivot = [label for _, _, label in self.rangos_antiguedad]
        todos_clientes = sorted(list(total_cargos.index))

        filas: list[dict[str, object]] = []
        for cliente in todos_clientes:
            tc = round(float(total_cargos.get(cliente, 0)), 2)
            ta = round(float(total_abonos.get(cliente, 0)), 2)
            sp = round(float(saldo_pendiente.get(cliente, 0)), 2)

            fila: dict[str, object] = {
                "NOMBRE_CLIENTE":  cliente,
                "ESTATUS_CLIENTE": estatus_map.get(cliente, ""),
                "NUM_FACTURAS_TOTALES":  int(num_facturas_totales.get(cliente, 0)),
                "NUM_FACTURAS_PENDIENTES": int(num_facturas_pendientes.get(cliente, 0)),
                "TOTAL_CARGOS": tc,
                "TOTAL_ABONOS": ta,
                "SALDO_PENDIENTE": sp,
            }
            for col in cols_pivot:
                fila[col] = round(pivot_rows.get(cliente, {}).get(col, 0.0), 2)
            
            filas.append(fila)

        resultado = pd.DataFrame(filas)
        if not resultado.empty:
            # Dual Sort: Primero Saldo Pendiente descendente, luego Nombre Ascendente
            mask_ceros = resultado["SALDO_PENDIENTE"] <= 0
            df_activos = resultado[~mask_ceros].sort_values("SALDO_PENDIENTE", ascending=False)
            df_ceros = resultado[mask_ceros].sort_values("NOMBRE_CLIENTE", ascending=True)
            resultado = pd.concat([df_activos, df_ceros], ignore_index=True)

            tot_row = {
                "NOMBRE_CLIENTE": "TOTAL",
                "ESTATUS_CLIENTE": "",
                "NUM_FACTURAS_TOTALES": resultado["NUM_FACTURAS_TOTALES"].sum(),
                "NUM_FACTURAS_PENDIENTES": resultado["NUM_FACTURAS_PENDIENTES"].sum(),
                "TOTAL_CARGOS": resultado["TOTAL_CARGOS"].sum(),
                "TOTAL_ABONOS": resultado["TOTAL_ABONOS"].sum(),
                "SALDO_PENDIENTE": resultado["SALDO_PENDIENTE"].sum()
            }
            for col in cols_pivot:
                tot_row[col] = resultado[col].sum()
                
            resultado = pd.concat([resultado, pd.DataFrame([tot_row])], ignore_index=True)

        columnas_finales = ["NOMBRE_CLIENTE", "ESTATUS_CLIENTE", "NUM_FACTURAS_TOTALES", "NUM_FACTURAS_PENDIENTES", "TOTAL_CARGOS", "TOTAL_ABONOS", "SALDO_PENDIENTE"] + cols_pivot
        return resultado[columnas_finales]

    # ==================================================================
    # CARTERA VENCIDA VS VIGENTE
    # ==================================================================

    def _cartera_vencida_vs_vigente(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        
        mask_ventas_abiertas = self._es_venta(df) & (df["SALDO_FACTURA"] > 0)
        cargos = df[mask_ventas_abiertas].copy()

        if cargos.empty:
            return pd.DataFrame()

        cargos["_ESTATUS"] = np.where(cargos["DELTA_MORA"] <= 0, "FACTURAS VIGENTES", "FACTURAS VENCIDAS")

        agrupado = (
            cargos.groupby("_ESTATUS")
            .agg(NUM_FACTURAS_PENDIENTES=("SALDO_FACTURA", "count"), SALDO_PENDIENTE=("SALDO_FACTURA", "sum"))
            .reset_index()
            .rename(columns={"_ESTATUS": "ESTATUS_VENCIMIENTO"})
        )

        total_saldo = agrupado["SALDO_PENDIENTE"].sum()
        agrupado["PCT_DEL_TOTAL"] = (agrupado["SALDO_PENDIENTE"] / total_saldo) if total_saldo > 0 else 0.0
        agrupado["SALDO_PENDIENTE"] = agrupado["SALDO_PENDIENTE"].round(2)

        orden_estatus = ["FACTURAS VIGENTES", "FACTURAS VENCIDAS"]
        agrupado["ESTATUS_VENCIMIENTO"] = pd.Categorical(
            agrupado["ESTATUS_VENCIMIENTO"], categories=orden_estatus, ordered=True,
        )
        resultado = agrupado.sort_values("ESTATUS_VENCIMIENTO").reset_index(drop=True)
        resultado["ESTATUS_VENCIMIENTO"] = resultado["ESTATUS_VENCIMIENTO"].astype(str)

        tot_row = pd.DataFrame([{
            "ESTATUS_VENCIMIENTO": "TOTAL",
            "NUM_FACTURAS_PENDIENTES": resultado["NUM_FACTURAS_PENDIENTES"].sum(),
            "SALDO_PENDIENTE": total_saldo,
            "PCT_DEL_TOTAL": 1.0 if total_saldo > 0 else 0.0
        }])
        
        return pd.concat([resultado, tot_row], ignore_index=True)[["ESTATUS_VENCIMIENTO", "NUM_FACTURAS_PENDIENTES", "SALDO_PENDIENTE", "PCT_DEL_TOTAL"]]

    # ==================================================================
    # RESUMEN POR CONCEPTO
    # ==================================================================

    def _resumen_por_concepto(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty or "TIPO_IMPTE" not in df_totales.columns:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        if df.empty:
            return pd.DataFrame()

        df["_MONTO"] = self._monto(df)
        es_cargo = df["TIPO_IMPTE"] == "C"
        es_abono = df["TIPO_IMPTE"] == "R"

        cargos_agg = (
            df[es_cargo].groupby("CONCEPTO")
            .agg(NUM_CARGOS=("_MONTO", "count"), TOTAL_CARGOS=("_MONTO", "sum"))
        )
        abonos_agg = (
            df[es_abono].groupby("CONCEPTO")
            .agg(NUM_ABONOS=("_MONTO", "count"), TOTAL_ABONOS=("_MONTO", "sum"))
        )

        resultado = cargos_agg.join(abonos_agg, how="outer").fillna(0).reset_index()
        
        for col in ["TOTAL_CARGOS", "TOTAL_ABONOS"]:
            resultado[col] = resultado[col].round(2)
        for col in ["NUM_CARGOS", "NUM_ABONOS"]:
            resultado[col] = resultado[col].astype(int)

        resultado = resultado.sort_values(["TOTAL_CARGOS", "TOTAL_ABONOS"], ascending=[False, False]).reset_index(drop=True)

        tot_row = pd.DataFrame([{
            "CONCEPTO": "TOTAL",
            "NUM_CARGOS": resultado["NUM_CARGOS"].sum(),
            "NUM_ABONOS": resultado["NUM_ABONOS"].sum(),
            "TOTAL_CARGOS": resultado["TOTAL_CARGOS"].sum(),
            "TOTAL_ABONOS": resultado["TOTAL_ABONOS"].sum()
        }])
        
        return pd.concat([resultado, tot_row], ignore_index=True)[["CONCEPTO", "NUM_CARGOS", "NUM_ABONOS", "TOTAL_CARGOS", "TOTAL_ABONOS"]]

    # ==================================================================
    # RESUMEN AJUSTES
    # ==================================================================

    def _resumen_ajustes(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        if df.empty:
            return pd.DataFrame()

        df["NOMBRE_CLIENTE"] = df.get("NOMBRE_CLIENTE", pd.Series("Sin cliente", index=df.index)).fillna("Sin cliente")
        df["_MONTO"] = self._monto(df)

        agrupado = (
            df.groupby("NOMBRE_CLIENTE")
            .agg(NUM_REGISTROS=("_MONTO", "count"), IMPORTE_AJUSTE=("_MONTO", "sum"))
            .reset_index()
        )

        agrupado["IMPORTE_AJUSTE"] = agrupado["IMPORTE_AJUSTE"].round(2)
        
        # Dual Sort
        mask_ceros = agrupado["IMPORTE_AJUSTE"] == 0
        df_activos = agrupado[~mask_ceros].sort_values("IMPORTE_AJUSTE", ascending=False)
        df_ceros = agrupado[mask_ceros].sort_values("NOMBRE_CLIENTE", ascending=True)
        agrupado = pd.concat([df_activos, df_ceros], ignore_index=True)

        tot_row = pd.DataFrame([{
            "NOMBRE_CLIENTE": "TOTAL",
            "NUM_REGISTROS": agrupado["NUM_REGISTROS"].sum(),
            "IMPORTE_AJUSTE": agrupado["IMPORTE_AJUSTE"].sum()
        }])
        
        return pd.concat([agrupado, tot_row], ignore_index=True)[["NOMBRE_CLIENTE", "NUM_REGISTROS", "IMPORTE_AJUSTE"]]

    # ==================================================================
    # RESUMEN CANCELADOS
    # ==================================================================

    def _resumen_cancelados(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        if df.empty:
            return pd.DataFrame()

        df["CONCEPTO"] = df.get("CONCEPTO", pd.Series(_SIN_CONCEPTO, index=df.index)).fillna(_SIN_CONCEPTO)
        df["_MONTO"] = self._monto(df)
        
        es_cargo = df["TIPO_IMPTE"] == "C"
        es_abono = df["TIPO_IMPTE"] == "R"

        c_agg = df[es_cargo].groupby("CONCEPTO").agg(NUM_CARGOS=("_MONTO", "count"), TOTAL_CARGOS_CANCELADOS=("_MONTO", "sum"))
        r_agg = df[es_abono].groupby("CONCEPTO").agg(NUM_ABONOS=("_MONTO", "count"), TOTAL_ABONOS_CANCELADOS=("_MONTO", "sum"))

        agrupado = c_agg.join(r_agg, how="outer").fillna(0).reset_index()

        for col in ["TOTAL_CARGOS_CANCELADOS", "TOTAL_ABONOS_CANCELADOS"]:
            agrupado[col] = agrupado[col].round(2)
        for col in ["NUM_CARGOS", "NUM_ABONOS"]:
            agrupado[col] = agrupado[col].astype(int)

        agrupado = agrupado.sort_values(["TOTAL_CARGOS_CANCELADOS", "TOTAL_ABONOS_CANCELADOS"], ascending=[False, False]).reset_index(drop=True)

        tot_row = pd.DataFrame([{
            "CONCEPTO": "TOTAL",
            "NUM_CARGOS": agrupado["NUM_CARGOS"].sum(),
            "NUM_ABONOS": agrupado["NUM_ABONOS"].sum(),
            "TOTAL_CARGOS_CANCELADOS": agrupado["TOTAL_CARGOS_CANCELADOS"].sum(),
            "TOTAL_ABONOS_CANCELADOS": agrupado["TOTAL_ABONOS_CANCELADOS"].sum()
        }])
        
        return pd.concat([agrupado, tot_row], ignore_index=True)[["CONCEPTO", "NUM_CARGOS", "NUM_ABONOS", "TOTAL_CARGOS_CANCELADOS", "TOTAL_ABONOS_CANCELADOS"]]

    # ==================================================================
    # RESUMEN POR VENDEDOR
    # ==================================================================

    def _resumen_por_vendedor(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        """Agrega los importes segmentados por vendedor garantizando robustez ante nulos."""
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        
        if df.empty:
            return pd.DataFrame()

        df["VENDEDOR"] = df.get("VENDEDOR", pd.Series("Sin vendedor", index=df.index)).fillna("Sin vendedor")
        df["_MONTO"] = self._monto(df)

        agrupado = (
            df.groupby("VENDEDOR")
            .agg(NUM_REGISTROS=("_MONTO", "count"), SALDO_PENDIENTE=("SALDO_FACTURA", "sum"), IMPORTE_TOTAL=("_MONTO", "sum"))
            .reset_index()
        )
        agrupado["IMPORTE_TOTAL"] = agrupado["IMPORTE_TOTAL"].round(2)
        agrupado["SALDO_PENDIENTE"] = agrupado["SALDO_PENDIENTE"].round(2)
        agrupado = agrupado.sort_values("SALDO_PENDIENTE", ascending=False).reset_index(drop=True)

        tot_row = pd.DataFrame([{
            "VENDEDOR": "TOTAL",
            "NUM_REGISTROS": agrupado["NUM_REGISTROS"].sum(),
            "SALDO_PENDIENTE": agrupado["SALDO_PENDIENTE"].sum(),
            "IMPORTE_TOTAL": agrupado["IMPORTE_TOTAL"].sum()
        }])
        
        return pd.concat([agrupado, tot_row], ignore_index=True)[["VENDEDOR", "NUM_REGISTROS", "SALDO_PENDIENTE", "IMPORTE_TOTAL"]]

    # ==================================================================
    # TENDENCIA MENSUAL (Heatmap/Calendario)
    # ==================================================================

    def _tendencia_mensual(self, df_totales: pd.DataFrame, moneda: str) -> pd.DataFrame:
        """Genera el agrupado de cobradas vs pendientes por año y mes."""
        if df_totales.empty:
            return pd.DataFrame()

        df = df_totales[df_totales["MONEDA"] == moneda].copy() if "MONEDA" in df_totales.columns else df_totales.copy()
        ventas = df[self._es_venta(df)].copy()

        if ventas.empty or "FECHA_EMISION" not in ventas.columns:
            return pd.DataFrame()

        ventas = ventas.dropna(subset=["FECHA_EMISION"]).copy()
        ventas["ANIO"] = ventas["FECHA_EMISION"].dt.year.astype(int)
        ventas["MES"] = ventas["FECHA_EMISION"].dt.month.astype(int)
        ventas["_MONTO_TOTAL"] = self._monto(ventas)
        ventas["ESTADO"] = np.where(ventas["SALDO_FACTURA"] <= 0.01, "COBRADAS", "PENDIENTES")
        
        agrupado = (
            ventas.groupby(["ANIO", "MES", "ESTADO"])
            .agg(NUM_FACTURAS=("_MONTO_TOTAL", "count"), IMPORTE_TOTAL=("_MONTO_TOTAL", "sum"), SALDO_PENDIENTE=("SALDO_FACTURA", "sum"))
            .reset_index()
        )
        return agrupado.sort_values(["ANIO", "MES", "ESTADO"]).reset_index(drop=True)