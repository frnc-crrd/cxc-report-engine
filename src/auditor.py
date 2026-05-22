"""Modulo de auditoria y deteccion de anomalias en CxC.

Implementa reglas de negocio para identificar registros sospechosos,
inconsistencias y problemas de calidad de datos en la cartera de
cuentas por cobrar extraida de Microsip.

Reglas implementadas:
    - Importes atipicos en ventas (Z-score sobre TIPO_IMPTE == 'C').
    - Valores atipicos de DELTA_RECAUDO (ciclo de cobro cerrado).
    - Valores atipicos de DELTA_MORA (facturas abiertas vencidas).
    - Clientes sin tipo asignado en el sistema.
    - Ventas sin vendedor asignado.
    - Documentos cancelados con dias hasta cancelacion.
    - Vencimientos criticos (mayor o igual a N dias vencidos).
    - Reporte de calidad de datos (nulos por columna).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from config.settings import CANCELADO_VALUES

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    """Resultado consolidado de la auditoria.

    Cada atributo es un DataFrame con los registros que dispararon
    la regla correspondiente. ``resumen`` es un diccionario con
    conteos agregados para visualizacion rapida.

    Attributes:
        resumen: Diccionario con conteos totales por tipo de hallazgo.
        importes_atipicos: Importes de ventas con Z-score sobre umbral.
        recaudos_atipicos: DELTA_RECAUDO con Z-score sobre umbral.
        moras_atipicas: DELTA_MORA con Z-score sobre umbral.
        sin_tipo_cliente: Documentos de clientes sin tipo asignado.
        sin_vendedor: Ventas sin vendedor asignado.
        documentos_cancelados: Documentos marcados como cancelados.
        calidad_datos: Reporte de nulos, tipos y valores unicos por columna.
    """

    resumen: dict[str, Any] = field(default_factory=dict)
    importes_atipicos: pd.DataFrame = field(default_factory=pd.DataFrame)
    recaudos_atipicos: pd.DataFrame = field(default_factory=pd.DataFrame)
    moras_atipicas: pd.DataFrame = field(default_factory=pd.DataFrame)
    sin_tipo_cliente: pd.DataFrame = field(default_factory=pd.DataFrame)
    sin_vendedor: pd.DataFrame = field(default_factory=pd.DataFrame)
    documentos_cancelados: pd.DataFrame = field(default_factory=pd.DataFrame)
    calidad_datos: pd.DataFrame = field(default_factory=pd.DataFrame)


class Auditor:
    """Motor de auditoria para datos de CxC de Microsip.

    Ejecuta validaciones sobre los datos crudos del query maestro y,
    opcionalmente, sobre el DataFrame del reporte operativo (necesario
    para auditar DELTA_RECAUDO y DELTA_MORA, que solo existen en el
    reporte procesado).

    Args:
        config: Diccionario de umbrales (ver ``settings.ANOMALIAS``).
            Claves esperadas: ``importe_zscore_umbral``,
            ``delta_recaudo_zscore_umbral``,
            ``delta_mora_zscore_umbral``,
            ``dias_vencimiento_critico``.
    """

    def __init__(self, config: dict[str, int | float]) -> None:
        """Inicializa el auditor con los umbrales de anomalia.

        Args:
            config: Diccionario de umbrales tal como se define en
                ``settings.ANOMALIAS``.
        """
        self.config = config

    # ==================================================================
    # METODO PRINCIPAL
    # ==================================================================

    def run_audit(
        self,
        df: pd.DataFrame,
        df_reporte: pd.DataFrame | None = None,
    ) -> AuditResult:
        """Ejecuta todas las validaciones y devuelve un AuditResult.

        Args:
            df: DataFrame con los datos crudos del query maestro de CxC.
            df_reporte: DataFrame del reporte operativo generado por
                ``reporte_cxc.generar_reporte_cxc()``. Requerido para
                auditar DELTA_RECAUDO y DELTA_MORA. Si es None, esas
                dos auditorias quedan vacias.

        Returns:
            AuditResult con cada hallazgo en un DataFrame separado
            y un diccionario ``resumen`` con los conteos.
        """
        logger.info("Iniciando auditoria sobre %d registros...", len(df))
        df = self._preparar_datos(df)

        result = AuditResult()
        result.importes_atipicos = self._detectar_importes_atipicos(df)
        result.sin_tipo_cliente = self._detectar_sin_tipo_cliente(df)
        result.sin_vendedor = self._detectar_sin_vendedor(df)
        result.documentos_cancelados = self._analizar_cancelados(df)
        result.calidad_datos = self._evaluar_calidad_datos(df)

        if df_reporte is not None and not df_reporte.empty:
            result.recaudos_atipicos = self._detectar_atipicos_delta(
                df_reporte,
                columna="DELTA_RECAUDO",
                umbral_key="delta_recaudo_zscore_umbral",
            )
            result.moras_atipicas = self._detectar_atipicos_delta(
                df_reporte,
                columna="DELTA_MORA",
                umbral_key="delta_mora_zscore_umbral",
            )

        result.resumen = self._generar_resumen(df, result)

        logger.info(
            "Auditoria completada — %d hallazgos totales.",
            result.resumen.get("total_hallazgos", 0),
        )
        return result

    # ==================================================================
    # PREPARACION DE DATOS
    # ==================================================================

    def _preparar_datos(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliza nombres de columnas, fechas y tipos numericos.

        Args:
            df: DataFrame crudo del query maestro.

        Returns:
            DataFrame normalizado.
        """
        df = df.copy()
        df.columns = pd.Index([c.upper().strip() for c in df.columns])

        for col in [
            "FECHA_EMISION",
            "FECHA_VENCIMIENTO",
            "FECHA_HORA_CREACION",
            "FECHA_HORA_ULT_MODIF",
            "FECHA_HORA_CANCELACION",
        ]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        for col in ["IMPORTE", "IMPUESTO", "CARGOS", "ABONOS"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "TIPO_IMPTE" in df.columns:
            df["TIPO_IMPTE"] = df["TIPO_IMPTE"].astype(str).str.strip().str.upper()

        return df

    # ==================================================================
    # REGLAS DE AUDITORIA — DATOS CRUDOS
    # ==================================================================

    def _detectar_importes_atipicos(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detecta importes atipicos unicamente en ventas (TIPO_IMPTE == 'C').

        Aplica Z-score sobre la columna IMPORTE de los cargos. Se excluyen
        cobros y otros tipos de movimiento para no distorsionar la media
        de referencia con importes de naturaleza diferente.

        Args:
            df: DataFrame normalizado.

        Returns:
            DataFrame con ventas atipicas, columna ZSCORE_IMPORTE y MOTIVO,
            o vacio si no hay suficientes datos.
        """
        umbral: float = float(self.config.get("importe_zscore_umbral", 3.0))

        if "IMPORTE" not in df.columns or "TIPO_IMPTE" not in df.columns:
            return pd.DataFrame()

        ventas = df[df["TIPO_IMPTE"] == "C"].copy()
        if ventas.empty:
            return pd.DataFrame()

        importes = ventas["IMPORTE"].dropna()
        if len(importes) < 3 or importes.std() == 0:
            return pd.DataFrame()

        ventas["ZSCORE_IMPORTE"] = np.abs(
            (ventas["IMPORTE"] - importes.mean()) / importes.std()
        )

        atipicos = ventas[ventas["ZSCORE_IMPORTE"] >= umbral].copy()
        if not atipicos.empty:
            atipicos["MOTIVO"] = (
                f"Importe de venta atipico (Z-score >= {umbral})"
            )
            logger.info("%d importes de venta atipicos.", len(atipicos))

        return atipicos

    def _detectar_atipicos_delta(
        self,
        df_reporte: pd.DataFrame,
        columna: str,
        umbral_key: str,
    ) -> pd.DataFrame:
        """Detecta valores atipicos en una columna delta del reporte operativo.

        Usa Z-score sobre los valores no nulos de la columna indicada.
        Aplica unicamente sobre filas de tipo cargo (TIPO_IMPTE == 'C')
        para mantener consistencia con la logica de negocio.

        Args:
            df_reporte: DataFrame del reporte operativo con columnas delta.
            columna: Nombre de la columna a analizar (DELTA_RECAUDO o
                DELTA_MORA).
            umbral_key: Clave en ``self.config`` con el umbral de Z-score.

        Returns:
            DataFrame con registros atipicos, columna ZSCORE_{columna}
            y MOTIVO, o vacio si no hay suficientes datos.
        """
        umbral: float = float(self.config.get(umbral_key, 3.0))

        if columna not in df_reporte.columns:
            logger.warning("Columna %s no encontrada en df_reporte.", columna)
            return pd.DataFrame()

        cargos = df_reporte[df_reporte.get("TIPO_IMPTE", pd.Series(dtype=str)) == "C"].copy() if "TIPO_IMPTE" in df_reporte.columns else df_reporte.copy()

        valores = cargos[columna].dropna()
        if len(valores) < 3 or valores.std() == 0:
            return pd.DataFrame()

        zscore_col = f"ZSCORE_{columna}"
        cargos[zscore_col] = np.abs(
            (cargos[columna] - valores.mean()) / valores.std()
        )

        atipicos = cargos[cargos[zscore_col] >= umbral].copy()
        if not atipicos.empty:
            # Eliminar la columna de bandas del reporte operativo para que
            # esta hoja se renderice como tabla plana sin coloreo de grupos.
            if "_BAND_GROUP" in atipicos.columns:
                atipicos = atipicos.drop(columns=["_BAND_GROUP"])
            atipicos["MOTIVO"] = (
                f"Valor atipico en {columna} (Z-score >= {umbral})"
            )
            logger.info(
                "%d registros con %s atipico.", len(atipicos), columna
            )

        return atipicos

    def _detectar_sin_tipo_cliente(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detecta documentos de clientes sin tipo asignado en Microsip.

        Un cliente sin tipo puede indicar alta incompleta en el sistema
        o un cliente importado sin clasificar.

        Args:
            df: DataFrame normalizado.

        Returns:
            DataFrame con registros afectados y columna MOTIVO.
        """
        if "TIPO_CLIENTE" not in df.columns:
            return pd.DataFrame()

        sin_tipo = df[df["TIPO_CLIENTE"].isna()].copy()
        if not sin_tipo.empty:
            sin_tipo["MOTIVO"] = "Cliente sin tipo asignado en el sistema"
            logger.info(
                "%d registros con cliente sin tipo.", len(sin_tipo)
            )

        return sin_tipo

    def _detectar_sin_vendedor(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detecta ventas sin vendedor asignado.

        Ventas sin vendedor no se pueden atribuir a ninguna persona para
        calcular comisiones ni desempeno por ejecutivo.

        Args:
            df: DataFrame normalizado.

        Returns:
            DataFrame con registros afectados y columna MOTIVO.
        """
        if "VENDEDOR" not in df.columns:
            return pd.DataFrame()

        sin_vendedor = df[df["VENDEDOR"].isna()].copy()
        if not sin_vendedor.empty:
            sin_vendedor["MOTIVO"] = "Venta sin vendedor asignado"
            logger.info(
                "%d registros sin vendedor.", len(sin_vendedor)
            )

        return sin_vendedor

    def _analizar_cancelados(self, df: pd.DataFrame) -> pd.DataFrame:
        """Identifica documentos cancelados y calcula dias hasta cancelacion.

        Si existen FECHA_HORA_CREACION y FECHA_HORA_CANCELACION, calcula
        cuantos dias pasaron entre la creacion y la cancelacion.

        Args:
            df: DataFrame normalizado.

        Returns:
            DataFrame con documentos cancelados, columnas MOTIVO y
            opcionalmente DIAS_HASTA_CANCELACION.
        """
        if "CANCELADO" not in df.columns:
            return pd.DataFrame()

        cancelados = df[df["CANCELADO"].isin(CANCELADO_VALUES)].copy()

        if not cancelados.empty:
            cancelados["MOTIVO"] = "Documento cancelado"
            if (
                "FECHA_HORA_CREACION" in df.columns
                and "FECHA_HORA_CANCELACION" in df.columns
            ):
                cancelados["DIAS_HASTA_CANCELACION"] = (
                    cancelados["FECHA_HORA_CANCELACION"]
                    - cancelados["FECHA_HORA_CREACION"]
                ).dt.days
            logger.info(
                "%d documentos cancelados.", len(cancelados)
            )

        return cancelados

    def _evaluar_calidad_datos(self, df: pd.DataFrame) -> pd.DataFrame:
        """Genera reporte de calidad: nulos, tipos y valores unicos por columna.

        Proporciona una vista general del estado de cada columna del
        dataset para identificar problemas de completitud o configuracion
        en Microsip.

        Args:
            df: DataFrame normalizado.

        Returns:
            DataFrame con una fila por columna y metricas de calidad.
        """
        reporte: list[dict[str, Any]] = []
        total = len(df)

        for col in df.columns:
            nulos = int(df[col].isna().sum())
            reporte.append({
                "COLUMNA": col,
                "TIPO_DATO": str(df[col].dtype),
                "TOTAL_REGISTROS": total,
                "NULOS": nulos,
                "PCT_NULOS": round(nulos / total * 100, 2) if total > 0 else 0,
                "VALORES_UNICOS": int(df[col].nunique()),
            })

        return pd.DataFrame(reporte)

    # ==================================================================
    # RESUMEN
    # ==================================================================

    def _generar_resumen(
        self,
        df: pd.DataFrame,
        result: AuditResult,
    ) -> dict[str, Any]:
        """Genera diccionario resumen con conteos de cada tipo de hallazgo.

        Args:
            df: DataFrame normalizado (para total de registros).
            result: AuditResult ya poblado con los DataFrames de hallazgos.

        Returns:
            Diccionario con fecha de auditoria, total de registros y
            conteo por cada tipo de hallazgo.
        """
        resumen: dict[str, Any] = {
            "fecha_auditoria": datetime.now().isoformat(),
            "total_registros": len(df),
            "importes_atipicos": len(result.importes_atipicos),
            "recaudos_atipicos": len(result.recaudos_atipicos),
            "moras_atipicas": len(result.moras_atipicas),
            "sin_tipo_cliente": len(result.sin_tipo_cliente),
            "sin_vendedor": len(result.sin_vendedor),
            "cancelados": len(result.documentos_cancelados),
        }
        resumen["total_hallazgos"] = sum([
            resumen["importes_atipicos"],
            resumen["recaudos_atipicos"],
            resumen["moras_atipicas"],
            resumen["sin_tipo_cliente"],
            resumen["sin_vendedor"],
            resumen["cancelados"],
        ])
        return resumen