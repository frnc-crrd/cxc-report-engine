"""Exportacion de vistas del pipeline a formato Parquet para Power BI.

Cada vista se guarda en output/powerbi/{subdir}/{nombre}_{timestamp}.parquet.
Las columnas con tipos mixtos (p. ej. filas de totales con cadenas vacias
en columnas numericas) se convierten a str antes de escribir para garantizar
compatibilidad con el motor pyarrow.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _sanitizar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columnas de tipo object a str para compatibilidad con pyarrow.

    Las filas de totales en los reportes de analisis contienen cadenas vacias
    en columnas que de otro modo son numericas (p. ej. PCT_ACUMULADO en la
    tabla de concentracion ABC). pyarrow rechaza columnas con tipos mixtos,
    por lo que cualquier columna object se normaliza a str.

    Args:
        df: DataFrame a sanitizar.

    Returns:
        Copia del DataFrame con columnas object convertidas a str.
    """
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str)
    return df


def exportar_parquet(
    vistas: dict[str, pd.DataFrame],
    output_dir: Path,
    timestamp: str,
) -> list[Path]:
    """Exporta vistas del pipeline a archivos Parquet para consumo en Power BI.

    Args:
        vistas: Diccionario donde cada clave tiene la forma ``"subdir/nombre"``
            (p. ej. ``"reporte/movimientos_abiertos_cxc"``). El subdir se crea
            dentro de ``output_dir``. DataFrames vacios se omiten sin error.
        output_dir: Directorio base de salida (p. ej. ``output/powerbi/``).
        timestamp: Cadena de fecha-hora que se agrega al nombre de cada
            archivo (p. ej. ``"20250601_120000"``).

    Returns:
        Lista de ``Path`` de los archivos Parquet generados. Los DataFrames
        vacios no generan archivo y no aparecen en la lista.
    """
    generados: list[Path] = []

    for clave, df in vistas.items():
        if df.empty:
            logger.debug("Vista '%s' vacia — omitida.", clave)
            continue

        partes = clave.rsplit("/", 1)
        if len(partes) == 2:
            subdir, nombre = partes
        else:
            subdir, nombre = "", partes[0]

        directorio = output_dir / subdir if subdir else output_dir
        directorio.mkdir(parents=True, exist_ok=True)

        ruta = directorio / f"{nombre}_{timestamp}.parquet"
        _sanitizar_tipos(df).to_parquet(ruta, index=False, engine="pyarrow")
        generados.append(ruta)
        logger.info("Parquet exportado: %s (%d filas)", ruta.name, len(df))

    return generados
