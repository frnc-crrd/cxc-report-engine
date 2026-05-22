"""Modulo de transformacion de datos en memoria para CxC.

Este modulo reemplaza las consultas SQL complejas por extracciones
simples a nivel de tabla, realizando las uniones (JOINs), filtros y
calculos de columnas derivadas directamente en memoria utilizando Pandas.
Garantiza el aislamiento de la logica de negocio frente a la base de datos.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from src.db_connector import FirebirdConnector

logger = logging.getLogger(__name__)


class DataTransformer:
    """Clase encargada de construir el conjunto de datos maestro de CxC.

    Extrae tablas de forma independiente y las combina en memoria para
    replicar la logica de negocio sin exponerla en sentencias SQL.

    Attributes:
        connector (FirebirdConnector): Instancia del conector a la base de datos.
    """

    def __init__(self, connector: FirebirdConnector) -> None:
        """Inicializa el transformador de datos.

        Args:
            connector (FirebirdConnector): Conector activo a Firebird.
        """
        self.connector = connector
        self._clientes_raw: pd.DataFrame | None = None

    def _extract_tables(self) -> dict[str, pd.DataFrame]:
        """Extrae de la base de datos unicamente las columnas requeridas por tabla.

        Returns:
            dict[str, pd.DataFrame]: Diccionario de DataFrames crudos por tabla.
        """
        logger.info("Iniciando extraccion plana de tablas para ocultamiento de logica...")
        
        tablas = {
            "DOCTOS_CC": [
                "DOCTO_CC_ID", "CLIENTE_ID", "COND_PAGO_ID", "CONCEPTO_CC_ID",
                "FOLIO", "HORA", "SISTEMA_ORIGEN", "NATURALEZA_CONCEPTO",
                "DESCRIPCION", "USUARIO_CREADOR", "FECHA_HORA_CREACION",
                "USUARIO_ULT_MODIF", "FECHA_HORA_ULT_MODIF", "USUARIO_CANCELACION",
                "FECHA_HORA_CANCELACION"
            ],
            "IMPORTES_DOCTOS_CC": [
                "DOCTO_CC_ID", "DOCTO_CC_ACR_ID", "IMPTE_DOCTO_CC_ID",
                "FECHA", "CANCELADO", "APLICADO", "TIPO_IMPTE",
                "IMPORTE", "IMPUESTO"
            ],
            "USOS_ANTICIPOS_CC": [
                "DOCTO_CC_ID", "ANTICIPO_CC_ID", "TIPO_USO"
            ],
            "CLIENTES": [
                "CLIENTE_ID", "TIPO_CLIENTE_ID", "VENDEDOR_ID",
                "MONEDA_ID", "NOMBRE", "ESTATUS", "LIMITE_CREDITO"
            ],
            "TIPOS_CLIENTES": ["TIPO_CLIENTE_ID", "NOMBRE"],
            "VENDEDORES": ["VENDEDOR_ID", "NOMBRE"],
            "MONEDAS": ["MONEDA_ID", "CLAVE_FISCAL"],
            "CONCEPTOS_CC": ["CONCEPTO_CC_ID", "NOMBRE"],
            "VENCIMIENTOS_CARGOS_CC": ["DOCTO_CC_ID", "FECHA_VENCIMIENTO"],
            "CONDICIONES_PAGO": ["COND_PAGO_ID", "NOMBRE"],
        }

        tables = self.connector.extract_tables_batch(tablas)
        self._clientes_raw = tables.get("CLIENTES")
        return tables

    def _merge_data(self, dataframes: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Realiza los cruces de informacion equivalentes a LEFT JOINs.

        Args:
            dataframes (dict[str, pd.DataFrame]): DataFrames crudos extraidos.

        Returns:
            pd.DataFrame: DataFrame unificado con todas las relaciones.
        """
        logger.info("Ensamblando relaciones en memoria (JOINs)...")
        
        # Base principal
        df = dataframes["DOCTOS_CC"].copy()

        # Join CLIENTES
        df = df.merge(dataframes["CLIENTES"], on="CLIENTE_ID", how="left", suffixes=("", "_CLIENTE"))
        df.rename(columns={"NOMBRE": "NOMBRE_CLIENTE", "ESTATUS": "ESTATUS_CLIENTE"}, inplace=True)

        # Join TIPOS_CLIENTES
        df = df.merge(dataframes["TIPOS_CLIENTES"], on="TIPO_CLIENTE_ID", how="left")
        df.rename(columns={"NOMBRE": "TIPO_CLIENTE"}, inplace=True)

        # Join VENDEDORES
        df = df.merge(dataframes["VENDEDORES"], on="VENDEDOR_ID", how="left")
        df.rename(columns={"NOMBRE": "VENDEDOR"}, inplace=True)

        # Join MONEDAS
        df = df.merge(dataframes["MONEDAS"], on="MONEDA_ID", how="left")
        df.rename(columns={"CLAVE_FISCAL": "MONEDA"}, inplace=True)

        # Join CONCEPTOS_CC
        df = df.merge(dataframes["CONCEPTOS_CC"], on="CONCEPTO_CC_ID", how="left")
        df.rename(columns={"NOMBRE": "CONCEPTO"}, inplace=True)

        # Join VENCIMIENTOS_CARGOS_CC
        df = df.merge(dataframes["VENCIMIENTOS_CARGOS_CC"], on="DOCTO_CC_ID", how="left")

        # Join IMPORTES_DOCTOS_CC
        df = df.merge(dataframes["IMPORTES_DOCTOS_CC"], on="DOCTO_CC_ID", how="left")
        df.rename(columns={"FECHA": "FECHA_EMISION"}, inplace=True)

        # Join USOS_ANTICIPOS_CC
        df = df.merge(dataframes["USOS_ANTICIPOS_CC"], on="DOCTO_CC_ID", how="left")
        df.rename(columns={"TIPO_USO": "TIPO_USO_ANTICIPO"}, inplace=True)

        # Join CONDICIONES_PAGO
        df = df.merge(dataframes["CONDICIONES_PAGO"], on="COND_PAGO_ID", how="left")
        df.rename(columns={"NOMBRE": "CONDICIONES"}, inplace=True)

        return df

    def _calculate_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Genera campos calculados aplicando las reglas de negocio.

        Args:
            df (pd.DataFrame): DataFrame unificado.

        Returns:
            pd.DataFrame: DataFrame con las columnas condicionales calculadas.
        """
        logger.info("Aplicando calculos y logica de negocio...")

        # Replicacion de CASE WHEN para cargos y abonos
        df["CARGOS"] = np.where(
            df["NATURALEZA_CONCEPTO"] == "C", 
            pd.to_numeric(df["IMPORTE"], errors="coerce").fillna(0), 
            0
        )
        df["ABONOS"] = np.where(
            df["NATURALEZA_CONCEPTO"] == "R", 
            pd.to_numeric(df["IMPORTE"], errors="coerce").fillna(0), 
            0
        )

        return df

    def get_all_clientes(self) -> list[dict[str, Any]]:
        """Retorna todos los clientes para sincronizar con la cobrador DB.

        Reutiliza la tabla CLIENTES ya extraida en get_master_cxc_data() si esta
        disponible, evitando una segunda llamada a Firebird.

        Returns:
            list[dict]: Lista de {'cliente_id': int, 'nombre_cliente': str}.
        """
        if self._clientes_raw is not None:
            df = self._clientes_raw[["CLIENTE_ID", "NOMBRE"]].copy()
        else:
            df = self.connector.extract_table("CLIENTES", ["CLIENTE_ID", "NOMBRE"])
        df = df.dropna(subset=["CLIENTE_ID", "NOMBRE"]).copy()
        df["CLIENTE_ID"] = pd.to_numeric(df["CLIENTE_ID"], errors="coerce")
        df = df.dropna(subset=["CLIENTE_ID"])
        df["CLIENTE_ID"] = df["CLIENTE_ID"].astype(int)
        df["NOMBRE"] = df["NOMBRE"].str.strip().str.upper()
        df = df.rename(columns={"CLIENTE_ID": "cliente_id", "NOMBRE": "nombre_cliente"})
        return cast(list[dict[str, Any]], df[["cliente_id", "nombre_cliente"]].to_dict(orient="records"))

    def get_master_cxc_data(self) -> pd.DataFrame:
        """Orquesta la extraccion, union y calculo para generar la vista maestra.

        Este metodo es el reemplazo directo de la anterior consulta SQL.

        Returns:
            pd.DataFrame: Conjunto de datos final procesado, ordenado y formateado,
            listo para ser consumido por el pipeline de auditoria.
        """
        dataframes = self._extract_tables()
        df = self._merge_data(dataframes)
        df = self._calculate_columns(df)

        # Ordenacion especificada en la consulta SQL original
        logger.info("Ordenando resultados...")
        df.sort_values(
            by=["NOMBRE_CLIENTE", "DOCTO_CC_ACR_ID", "DOCTO_CC_ID", "FECHA_EMISION"],
            ascending=[True, True, True, False],
            inplace=True,
            ignore_index=True
        )

        # Seleccion estricta de columnas para mantener la consistencia del pipeline
        columnas_finales = [
            "DOCTO_CC_ID", "DOCTO_CC_ACR_ID", "IMPTE_DOCTO_CC_ID", "ANTICIPO_CC_ID",
            "CLIENTE_ID", "TIPO_CLIENTE_ID", "VENDEDOR_ID", "MONEDA_ID", "COND_PAGO_ID", "CONCEPTO_CC_ID",
            "NOMBRE_CLIENTE", "TIPO_CLIENTE", "MONEDA", "CONDICIONES", "VENDEDOR", "ESTATUS_CLIENTE", "LIMITE_CREDITO",
            "CONCEPTO", "FOLIO",
            "FECHA_EMISION", "FECHA_VENCIMIENTO", "HORA",
            "SISTEMA_ORIGEN", "NATURALEZA_CONCEPTO", "CANCELADO", "APLICADO",
            "DESCRIPCION",
            "TIPO_USO_ANTICIPO", "CARGOS", "ABONOS", "TIPO_IMPTE", "IMPORTE", "IMPUESTO",
            "USUARIO_CREADOR", "FECHA_HORA_CREACION", "USUARIO_ULT_MODIF", "FECHA_HORA_ULT_MODIF",
            "USUARIO_CANCELACION", "FECHA_HORA_CANCELACION"
        ]
        
        logger.info("Transformacion completada exitosamente.")
        return df[columnas_finales]