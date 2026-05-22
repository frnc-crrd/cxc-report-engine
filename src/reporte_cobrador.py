"""Generador de reportes Excel individuales por cobrador.

Toma la vista movimientos_abiertos_cxc y la divide por cobrador según las
asignaciones en PostgreSQL. CLIENTE_ID nunca se incluye en el output.

Cada Excel generado replica el mismo formato y columnas que la pestaña
movimientos_abiertos_cxc del reporte principal (src/excel_formatter.py).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from src.excel_formatter import exportar_excel

logger = logging.getLogger(__name__)

# Columnas internas que jamás deben aparecer en los reportes de cobrador
_COLS_EXCLUIR = {"CLIENTE_ID", "_BAND_GROUP"}

COBRADOR_PENDIENTE = "PENDIENTE"


def filtrar_assignments_por_cobrador(
    assignments: dict[str, str],
    cobrador: str | None,
) -> dict[str, str]:
    """Filtra el mapeo cliente→cobrador para un unico cobrador.

    Args:
        assignments: Mapeo completo {nombre_cliente: cobrador}.
        cobrador: Nombre del cobrador a conservar. Si es None devuelve
            el diccionario original sin modificar.

    Returns:
        Subconjunto del mapeo donde el valor es exactamente ``cobrador``,
        o el mapeo original si ``cobrador`` es None.
    """
    if cobrador is None:
        return assignments
    return {k: v for k, v in assignments.items() if v == cobrador}


def _sanitizar_nombre(nombre: str) -> str:
    """Convierte un nombre de cobrador en un slug seguro para nombre de archivo."""
    slug = nombre.strip().replace(" ", "_")
    slug = re.sub(r"[^\w]", "", slug, flags=re.ASCII)
    return slug[:40] or "cobrador"


def generar_reportes_por_cobrador(
    df_abiertos: pd.DataFrame,
    assignments: dict[str, str],
    output_dir: Path,
    timestamp: str,
) -> list[Path]:
    """Divide movimientos_abiertos_cxc en un Excel por cobrador.

    Args:
        df_abiertos:  DataFrame de movimientos abiertos (sin CLIENTE_ID en output).
        assignments:  Mapeo {nombre_cliente: cobrador} desde CobradorManager.
        output_dir:   Directorio donde se escriben los archivos de cobrador.
        timestamp:    Sufijo de tiempo para los nombres de archivo.

    Returns:
        Lista de rutas de archivos .xlsx generados.
    """
    if df_abiertos.empty:
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Columnas del output: las que tenga el df menos las internas
    cols_output = [c for c in df_abiertos.columns if c not in _COLS_EXCLUIR]

    # Asignar cobrador a cada fila (left-join implícito con default PENDIENTE)
    df = df_abiertos.copy()
    df["_COBRADOR"] = df["NOMBRE_CLIENTE"].map(assignments).fillna(COBRADOR_PENDIENTE)

    archivos: list[Path] = []

    for cobrador, grupo in df.groupby("_COBRADOR", sort=True):
        if grupo.empty:
            continue

        df_cobrador = grupo[cols_output].copy()

        # Misma logica que el reporte principal: cada fila donde CONCEPTO contiene
        # "VENTA" marca el inicio de un nuevo grupo de factura; los abonos que la
        # siguen heredan la misma banda hasta encontrar la proxima venta.
        if "CONCEPTO" in df_cobrador.columns:
            es_venta = df_cobrador["CONCEPTO"].str.contains("VENTA", case=False, na=False)
            df_cobrador["_BAND_GROUP"] = es_venta.cumsum() % 2

        slug = _sanitizar_nombre(str(cobrador))
        nombre_hoja = f"cobranza_{slug[:25]}"

        path = exportar_excel(
            dataframes={nombre_hoja: df_cobrador},
            nombre_base=f"cobrador_{slug}",
            output_dir=output_dir,
            timestamp=timestamp,
            orden_hojas=[nombre_hoja],
        )
        archivos.append(path)
        logger.info(
            "Cobrador '%s': %d filas → %s", cobrador, len(df_cobrador), path.name
        )

    return archivos
