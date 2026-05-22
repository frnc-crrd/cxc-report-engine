"""Pipeline principal de auditoria CxC para Microsip.

Orquesta la extraccion de datos desde Firebird, generacion del reporte
operativo, auditoria de anomalias, analisis de cartera, KPIs estrategicos
y exportacion a tres archivos Excel independientes mas reportes por cobrador.

Archivos generados:
    01_reporte_cxc_TIMESTAMP.xlsx      — movimientos CxC con todas las pestañas
    02_analisis_cxc_TIMESTAMP.xlsx     — analisis por moneda, KPIs y tendencias
    03_auditoria_cxc_TIMESTAMP.xlsx    — hallazgos de calidad y anomalias
    output/cobradores/cobrador_NOMBRE_TIMESTAMP.xlsx  — uno por cobrador asignado
    output/powerbi/**/*.parquet        — vistas del pipeline en formato Parquet

Uso:
    python main.py                    # Pipeline completo
    python main.py --test-connection  # Solo probar conexion a Firebird
    python main.py --skip-audit       # Saltar auditoria de anomalias
    python main.py --skip-analytics   # Saltar analisis de cartera
    python main.py --skip-kpis        # Saltar KPIs estrategicos
    python main.py --skip-parquet     # Saltar exportacion Parquet para Power BI
    python main.py --dry-run          # Simular sin escribir archivos
"""

from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any

import pandas as pd
from tenacity import RetryCallState, RetryError, Retrying, stop_after_attempt, wait_fixed

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import (
    ANOMALIAS,
    BASE_DIR,
    CANCELADO_VALUES,
    COBRADOR_DB_URL,
    EMAIL_AZURE_CLIENT_ID,
    EMAIL_AZURE_TENANT_ID,
    EMAIL_SMTP_USER,
    EMAIL_TOKEN_CACHE,
    EXCEL_NOMBRES,
    FIREBIRD_CONFIG,
    KPI_PERIODO_DIAS,
    OUTPUT_DIR,
    RANGOS_ANTIGUEDAD,
)
from src.analytics import Analytics
from src.auditor import Auditor
from src.cobrador_manager import CobradorManager
from src.db_connector import FirebirdConnector
from src.data_transformer import DataTransformer
from rich.console import Console

from src.cobrador_cli import _buscar_nombre_fallback, _detectar_encoding
from src.email_sender import cargar_rutas, enviar_rutas
from src.excel_formatter import COLUMNAS_CALCULADAS_CXC, exportar_excel, extraer_banda
from src.logging_config import configurar_logging
from src.kpis import generar_kpis
from src.parquet_exporter import exportar_parquet
from src.pdf_cobrador import generar_pdfs_por_cobrador
from src.reporte_cobrador import filtrar_assignments_por_cobrador, generar_reportes_por_cobrador
from src.reporte_cxc import agregar_bandas_grupo, generar_reporte_cxc

# ======================================================================
# LOGGING
# ======================================================================

logger = logging.getLogger("main")
_console = Console(highlight=False)

# Ruta al CSV de asignaciones cobrador — respaldo persistente entre ejecuciones
_ASIGNACIONES_CSV = Path("data/private/asignaciones.csv")


def _auto_importar_asignaciones(
    mgr: CobradorManager, assignments: dict[str, str]
) -> None:
    """Aplica asignaciones desde el CSV de respaldo despues de sync_clientes.

    Se ejecuta automaticamente en cada pipeline para que las relaciones
    cobrador<->cliente persistan incluso si la DB local pierde sus datos.
    """
    if not _ASIGNACIONES_CSV.exists():
        logger.debug("No existe %s — se omite restauracion de asignaciones.", _ASIGNACIONES_CSV)
        return

    encoding = _detectar_encoding(_ASIGNACIONES_CSV)
    with _ASIGNACIONES_CSV.open(newline="", encoding=encoding) as f:
        primera = f.readline()
    delimitador = ";" if primera.count(";") > primera.count(",") else ","

    filas: list[dict[str, str]] = []
    with _ASIGNACIONES_CSV.open(newline="", encoding=encoding) as f:
        reader = csv.DictReader(f, delimiter=delimitador)
        if reader.fieldnames:
            reader.fieldnames = [c.strip().lstrip("﻿") for c in reader.fieldnames]
        if (
            not reader.fieldnames
            or "NOMBRE_CLIENTE" not in reader.fieldnames
            or "COBRADOR" not in reader.fieldnames
        ):
            logger.warning(
                "asignaciones.csv no tiene columnas NOMBRE_CLIENTE/COBRADOR — se omite."
            )
            return
        for row in reader:
            nombre = row["NOMBRE_CLIENTE"].strip().upper()
            cobrador = row["COBRADOR"].strip()
            if not nombre or not cobrador:
                continue
            if nombre in assignments:
                filas.append({"nombre_cliente": nombre, "cobrador": cobrador})
            else:
                nombre_real = _buscar_nombre_fallback(nombre, assignments)
                if nombre_real is not None:
                    filas.append({"nombre_cliente": nombre_real, "cobrador": cobrador})

    if filas:
        resultado = mgr.bulk_update(filas)
        logger.info(
            "Cobrador asignaciones restauradas desde CSV: %d aplicadas.",
            resultado["actualizados"],
        )
    else:
        logger.debug("asignaciones.csv no tiene entradas aplicables a la DB actual.")


def _auto_exportar_asignaciones(mgr: CobradorManager) -> None:
    """Exporta asignaciones actuales al CSV de respaldo con backup previo.

    Mantiene el CSV sincronizado con el estado real de la DB despues de
    cada ejecucion del pipeline.
    """
    assignments = mgr.get_assignments()
    _ASIGNACIONES_CSV.parent.mkdir(parents=True, exist_ok=True)
    if _ASIGNACIONES_CSV.exists():
        shutil.copy2(_ASIGNACIONES_CSV, _ASIGNACIONES_CSV.with_suffix(".csv.bak"))
    with _ASIGNACIONES_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["NOMBRE_CLIENTE", "COBRADOR"])
        for nombre, cobrador in sorted(assignments.items()):
            writer.writerow([nombre, cobrador])
    logger.info(
        "Asignaciones exportadas al CSV de respaldo: %d entradas → %s",
        len(assignments), _ASIGNACIONES_CSV,
    )


# ======================================================================
# PREPARACION DE DATOS
# ======================================================================

def _formatear_hora(valor: Any) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if isinstance(valor, dt_time):
        return valor.strftime("%H:%M:%S")
    if hasattr(valor, "strftime"):
        return str(valor.strftime("%H:%M:%S"))
    return str(valor)


def _normalizar_fechas_hora(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["FECHA_EMISION", "FECHA_VENCIMIENTO"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    if "HORA" in df.columns:
        df["HORA"] = df["HORA"].apply(_formatear_hora)
    return df


def preparar_registros_totales(df: pd.DataFrame) -> pd.DataFrame:
    """Prepara la vista ``registros_totales_cxc`` para el reporte Excel.

    Aplica dos transformaciones en cadena:
        1. Normaliza FECHA y HORA al formato de presentacion.
        2. Agrega la columna ``_BAND_GROUP`` para el sombreado alternado.

    Args:
        df: DataFrame con la totalidad de movimientos de CxC.

    Returns:
        DataFrame listo para exportar a la hoja ``registros_totales_cxc``.
    """
    return agregar_bandas_grupo(_normalizar_fechas_hora(df))


def _filtrar_por_acreditar(df_totales: pd.DataFrame) -> pd.DataFrame:
    if "TIPO_IMPTE" not in df_totales.columns:
        return pd.DataFrame()
    tipo_norm = df_totales["TIPO_IMPTE"].astype(str).str.strip().str.upper()
    mask_tipo_a = tipo_norm.isin(["A", "T"])
    if "CANCELADO" in df_totales.columns:
        mask_activos = ~df_totales["CANCELADO"].isin(CANCELADO_VALUES)
        resultado = df_totales[mask_tipo_a & mask_activos].copy()
    else:
        resultado = df_totales[mask_tipo_a].copy()
    if "_BAND_GROUP" in resultado.columns:
        resultado = resultado.drop(columns=["_BAND_GROUP"])
    resultado = agregar_bandas_grupo(resultado)
    logger.info("Registros por acreditar (Anticipos y Devoluciones): %d filas.", len(resultado))
    return resultado


def _filtrar_cancelados(df_totales: pd.DataFrame) -> pd.DataFrame:
    if "CANCELADO" not in df_totales.columns:
        return pd.DataFrame()
    resultado = df_totales[df_totales["CANCELADO"].isin(CANCELADO_VALUES)].copy()
    if "_BAND_GROUP" in resultado.columns:
        resultado = resultado.drop(columns=["_BAND_GROUP"])
    resultado = agregar_bandas_grupo(resultado)
    logger.info("Registros cancelados: %d filas.", len(resultado))
    return resultado


# ======================================================================
# EXPORTACION — TRES ARCHIVOS EXCEL
# ======================================================================

def exportar_tres_exceles(
    cxc: dict[str, pd.DataFrame],
    auditoria: dict[str, pd.DataFrame],
    analisis: dict[str, pd.DataFrame],
    kpis: dict[str, pd.DataFrame],
    timestamp: str,
    output_dir: Path,
) -> list[Path]:
    """Exporta los tres archivos Excel de salida del pipeline.

    Genera ``01_reporte_cxc``, ``02_analisis_cxc`` y ``03_auditoria_cxc``
    con sus respectivas hojas usando :func:`excel_formatter.exportar_excel`.

    Args:
        cxc: Vistas de CxC (movimientos abiertos, cerrados, totales, etc.).
        auditoria: Vistas de auditoria y anomalias detectadas.
        analisis: Vistas de analitica (aging, pivots, tendencias, ABC).
        kpis: Vistas de KPIs (DSO, CEI, morosidad, concentracion).
        timestamp: Sufijo de fecha/hora para los nombres de archivo.
        output_dir: Directorio donde se escriben los Excel.

    Returns:
        Lista con las rutas absolutas de los tres archivos generados.
    """
    archivos: list[Path] = []

    logger.info("Exportando 01_reporte_cxc...")
    archivos.append(exportar_excel(
        dataframes=cxc,
        nombre_base=EXCEL_NOMBRES["cxc"],
        timestamp=timestamp,
        output_dir=output_dir,
        orden_hojas=[
            "movimientos_abiertos_cxc",
            "movimientos_cerrados_cxc",
            "movimientos_totales_cxc",
            "registros_por_acreditar_cxc",
            "registros_cancelados_cxc",
            "registros_totales_cxc",
        ],
        cols_calc_por_hoja={"movimientos_totales_cxc": COLUMNAS_CALCULADAS_CXC},
    ))

    analisis_compilado = {**analisis}
    hojas_kpis_a_fusionar = [
        "kpis_resumen_mxn", "kpis_resumen_usd",
        "kpis_concentracion_mxn", "kpis_concentracion_usd",
        "kpis_limite_credito_mxn", "kpis_limite_credito_usd",
        "kpis_morosidad_cliente_mxn", "kpis_morosidad_cliente_usd",
    ]
    for hoja_kpi in hojas_kpis_a_fusionar:
        if hoja_kpi in kpis:
            analisis_compilado[hoja_kpi] = kpis.pop(hoja_kpi)

    logger.info("Exportando 02_analisis_cxc...")
    archivos.append(exportar_excel(
        dataframes=analisis_compilado,
        nombre_base=EXCEL_NOMBRES["analisis"],
        timestamp=timestamp,
        output_dir=output_dir,
        orden_hojas=[
            "cartera_vencida_vs_vigente_mxn", "cartera_vencida_vs_vigente_usd",
            "antiguedad_cartera_mxn", "antiguedad_cartera_usd",
            "antiguedad_por_cliente_mxn", "antiguedad_por_cliente_usd",
            "resumen_concepto_cxc_mxn", "resumen_concepto_cxc_usd",
            "resumen_cancelados_cxc_mxn", "resumen_cancelados_cxc_usd",
            "resumen_ajustes_cxc_mxn", "resumen_ajustes_cxc_usd",
            "kpis_resumen_mxn", "kpis_resumen_usd",
            "kpis_concentracion_mxn", "kpis_concentracion_usd",
            "kpis_limite_credito_mxn", "kpis_limite_credito_usd",
            "kpis_morosidad_cliente_mxn", "kpis_morosidad_cliente_usd",
            "tendencia_mensual_mxn", "tendencia_mensual_usd",
            "resumen_por_vendedor_mxn", "resumen_por_vendedor_usd",
        ],
    ))

    logger.info("Exportando 03_auditoria_cxc...")
    archivos.append(exportar_excel(
        dataframes=auditoria,
        nombre_base=EXCEL_NOMBRES["auditoria"],
        timestamp=timestamp,
        output_dir=output_dir,
        orden_hojas=[
            "calidad_datos", "importes_atipicos", "recaudos_atipicos",
            "moras_atipicas", "sin_tipo_cliente", "sin_vendedor",
        ],
    ))

    return archivos


# ======================================================================
# HELPERS DE CONSOLA
# ======================================================================

_TOTAL_PASOS = 9


def _paso_console(n: int, desc: str, dry_run: bool = False) -> None:
    prefijo = "[bold yellow][DRY-RUN][/bold yellow] " if dry_run else ""
    _console.print(f"{prefijo}[bold cyan][{n}/{_TOTAL_PASOS}][/bold cyan] {desc}")


def _detalle(texto: str) -> None:
    _console.print(f"  [dim]→[/dim] {texto}")


def _log_tabla_tiempos(tiempos: dict[str, float]) -> None:
    logger.info("TIEMPOS DEL PIPELINE (segundos):")
    for nombre, seg in tiempos.items():
        logger.info("  %-38s %.2fs", nombre, seg)


def _resumen_tiempos_console(tiempos: dict[str, float]) -> None:
    _console.print("\n[bold]Tiempos de ejecucion:[/bold]")
    for nombre, seg in tiempos.items():
        if nombre == "TOTAL":
            _console.print(f"  [bold green]{'TOTAL':<36} {seg:.2f}s[/bold green]")
        else:
            _console.print(f"  [dim]{nombre:<36}[/dim] {seg:.2f}s")


# ======================================================================
# PIPELINE
# ======================================================================

def run_pipeline(
    skip_audit: bool = False,
    skip_analytics: bool = False,
    skip_kpis: bool = False,
    skip_parquet: bool = False,
    skip_email: bool = False,
    output_dir: Path | None = None,
    solo_cobrador: str | None = None,
    dry_run: bool = False,
) -> int:
    """Orquesta la ejecucion completa del pipeline de auditoria CxC.

    Encadena los nueve pasos del pipeline: extraccion Firebird, transformacion,
    reporte CxC, auditoria de anomalias, analitica, KPIs, exportacion Excel,
    sincronizacion de cobradores con PostgreSQL, division por cobrador,
    exportacion Parquet para Power BI y envio de correos.

    Args:
        skip_audit: Omitir el modulo de auditoria de anomalias.
        skip_analytics: Omitir el modulo de analitica de cartera.
        skip_kpis: Omitir el calculo de KPIs estrategicos.
        skip_parquet: Omitir la exportacion Parquet para Power BI.
        skip_email: Omitir el envio automatico de correos.
        output_dir: Directorio de salida. Si es ``None`` se usa
            ``settings.OUTPUT_DIR``.
        solo_cobrador: Si se indica, genera unicamente el reporte de ese
            cobrador. Util para pruebas o re-envios puntuales.
        dry_run: Si es ``True`` ejecuta todo el pipeline sin escribir archivos.

    Returns:
        Codigo de salida del proceso: ``0`` exito, ``1`` fallo no recuperable.
    """
    effective_output_dir = output_dir if output_dir is not None else OUTPUT_DIR

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    t_pipeline = time.perf_counter()
    tiempos: dict[str, float] = {}

    if dry_run:
        _console.print("[bold yellow][DRY-RUN] Modo simulacion: no se escribiran archivos.[/bold yellow]")

    # ── PASO 1: Extraccion ────────────────────────────────────────────────
    _paso_console(1, "Extraccion y transformacion de datos", dry_run)
    logger.info("PASO 1 — Extraccion y transformacion")

    def _antes_reintentar_firebird(retry_state: RetryCallState) -> None:
        n = retry_state.attempt_number
        _detalle(f"[yellow]Sin conexion a Firebird — reintento {n}/3 en 30 s...[/yellow]")
        logger.warning("PASO 1 — Firebird no disponible, intento %d/3", n)

    t0 = time.perf_counter()
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_fixed(30),
            before_sleep=_antes_reintentar_firebird,
        ):
            with attempt:
                connector = FirebirdConnector(FIREBIRD_CONFIG)
                transformer = DataTransformer(connector)
                df = transformer.get_master_cxc_data()
    except RetryError as exc:
        cause = exc.last_attempt.exception()
        logger.error("Firebird no disponible tras 3 intentos: %s", cause)
        return 1
    except Exception as exc:
        logger.error("Error al procesar los datos transaccionales: %s", exc)
        return 1
    tiempos["PASO 1 — Extraccion"] = time.perf_counter() - t0

    logger.info("Datos unificados en memoria: %d filas x %d columnas", *df.shape)
    _detalle(f"{df.shape[0]:,} filas × {df.shape[1]} columnas extraidas de Firebird")

    # ── PASO 2: Reporte CxC ───────────────────────────────────────────────
    _paso_console(2, "Reporte operativo CxC", dry_run)
    logger.info("PASO 2 — Reporte operativo CxC")

    t0 = time.perf_counter()
    resultado_reporte = generar_reporte_cxc(df)
    registros_totales = preparar_registros_totales(df)
    registros_por_acreditar = _filtrar_por_acreditar(registros_totales)
    registros_cancelados = _filtrar_cancelados(registros_totales)
    tiempos["PASO 2 — Reporte CxC"] = time.perf_counter() - t0

    cxc: dict[str, pd.DataFrame] = {
        "movimientos_abiertos_cxc":    resultado_reporte.get("movimientos_abiertos_cxc", pd.DataFrame()),
        "movimientos_cerrados_cxc":    resultado_reporte.get("movimientos_cerrados_cxc", pd.DataFrame()),
        "movimientos_totales_cxc":     resultado_reporte.get("movimientos_totales_cxc", pd.DataFrame()),
        "registros_por_acreditar_cxc": registros_por_acreditar,
        "registros_cancelados_cxc":    registros_cancelados,
        "registros_totales_cxc":       registros_totales,
    }

    df_abiertos = cxc.get("movimientos_abiertos_cxc", pd.DataFrame())
    df_totales  = cxc.get("movimientos_totales_cxc", pd.DataFrame())
    n_clientes  = int(df_totales["NOMBRE_CLIENTE"].nunique()) if "NOMBRE_CLIENTE" in df_totales.columns else 0
    _detalle(
        f"{len(df_totales):,} movimientos totales | "
        f"{n_clientes} clientes | "
        f"{len(df_abiertos)} saldos abiertos"
    )

    # ── PASO 3: Cobrador ──────────────────────────────────────────────────
    _paso_console(3, "Sincronizacion y reportes por cobrador", dry_run)
    logger.info("PASO 3 — Cobrador")

    t0 = time.perf_counter()
    archivos_cobrador: list[Path] = []
    pdfs_cobrador: list[Path] = []
    cobrador_mgr = None
    try:
        cobrador_mgr = CobradorManager(COBRADOR_DB_URL)
        if not df_abiertos.empty:
            todos_clientes = transformer.get_all_clientes()
            sync_result = cobrador_mgr.sync_clientes(todos_clientes)
            logger.info(
                "Cobrador DB: %d nuevos, %d eliminados (no estan en Firebird), %d total.",
                sync_result["nuevos"], sync_result["eliminados"], sync_result["total"],
            )
            _auto_importar_asignaciones(cobrador_mgr, cobrador_mgr.get_assignments())
            assignments = cobrador_mgr.get_assignments()
            assignments = filtrar_assignments_por_cobrador(assignments, solo_cobrador)
            if solo_cobrador:
                logger.info("Filtrando reportes: solo cobrador '%s'.", solo_cobrador)
            if not dry_run:
                cobradores_dir = effective_output_dir / "cobradores"
                archivos_cobrador = generar_reportes_por_cobrador(
                    df_abiertos, assignments, cobradores_dir, timestamp
                )
                pdfs_cobrador = generar_pdfs_por_cobrador(
                    df_abiertos, assignments, cobradores_dir, timestamp
                )
                logger.info(
                    "Reportes por cobrador generados: %d Excel, %d PDF.",
                    len(archivos_cobrador), len(pdfs_cobrador),
                )
                _auto_exportar_asignaciones(cobrador_mgr)
            else:
                logger.info("[DRY-RUN] Omitiendo escritura de reportes por cobrador.")
    except Exception as exc:
        logger.warning(
            "Cobrador DB no disponible — reportes por cobrador omitidos. "
            "Verifica que el contenedor este activo ('mise run db-up') y que "
            "COBRADOR_DB_PASSWORD este definida en .env. Detalle: %s", exc,
        )
    finally:
        if cobrador_mgr is not None:
            cobrador_mgr.dispose()
    tiempos["PASO 3 — Cobrador"] = time.perf_counter() - t0

    n_cobradores = len(archivos_cobrador)
    if n_cobradores:
        _detalle(
            f"{n_cobradores} cobradores | "
            f"{len(archivos_cobrador)} Excel + {len(pdfs_cobrador)} PDF generados"
        )
    else:
        _detalle("Sin archivos de cobrador generados")

    # ── PASO 4: Auditoria ─────────────────────────────────────────────────
    _paso_console(
        4,
        "Auditoria y deteccion de anomalias" + (" (omitida)" if skip_audit else ""),
        dry_run,
    )
    auditoria: dict[str, pd.DataFrame] = {}
    if not skip_audit:
        logger.info("PASO 4 — Auditoria")
        t0 = time.perf_counter()
        reporte_cxc_df = resultado_reporte.get("reporte_cxc", pd.DataFrame())
        auditor = Auditor(ANOMALIAS)
        audit_result = auditor.run_audit(df, df_reporte=reporte_cxc_df)
        auditoria = {
            "calidad_datos":     audit_result.calidad_datos,
            "importes_atipicos": audit_result.importes_atipicos,
            "recaudos_atipicos": audit_result.recaudos_atipicos,
            "moras_atipicas":    audit_result.moras_atipicas,
            "sin_tipo_cliente":  audit_result.sin_tipo_cliente,
            "sin_vendedor":      audit_result.sin_vendedor,
        }
        tiempos["PASO 4 — Auditoria"] = time.perf_counter() - t0
        n_hallazgos = sum(len(v) for v in auditoria.values())
        _detalle(f"{n_hallazgos:,} hallazgos en {len(df):,} registros")

    # ── PASO 5: Analisis ──────────────────────────────────────────────────
    _paso_console(
        5,
        "Analisis de cartera" + (" (omitida)" if skip_analytics else ""),
        dry_run,
    )
    analisis: dict[str, pd.DataFrame] = {}
    if not skip_analytics:
        logger.info("PASO 5 — Analisis de cartera")
        t0 = time.perf_counter()
        vistas_analytics = {
            "movimientos_abiertos_cxc":    cxc.get("movimientos_abiertos_cxc", pd.DataFrame()),
            "movimientos_totales_cxc":     cxc.get("movimientos_totales_cxc", pd.DataFrame()),
            "registros_por_acreditar_cxc": cxc.get("registros_por_acreditar_cxc", pd.DataFrame()),
            "registros_cancelados_cxc":    cxc.get("registros_cancelados_cxc", pd.DataFrame()),
        }
        analytics_engine = Analytics(RANGOS_ANTIGUEDAD)
        analisis = analytics_engine.run_analytics(vistas_analytics)
        tiempos["PASO 5 — Analisis"] = time.perf_counter() - t0
        _detalle(f"{len(analisis)} vistas de analisis generadas")

    # ── PASO 6: KPIs ──────────────────────────────────────────────────────
    _paso_console(
        6,
        "KPIs estrategicos" + (" (omitidos)" if skip_kpis else ""),
        dry_run,
    )
    kpis: dict[str, pd.DataFrame] = {}
    if not skip_kpis:
        logger.info("PASO 6 — KPIs estrategicos")
        t0 = time.perf_counter()
        kpis = generar_kpis(cxc.get("movimientos_totales_cxc", pd.DataFrame()), KPI_PERIODO_DIAS)
        tiempos["PASO 6 — KPIs"] = time.perf_counter() - t0
        # Extraer DSO y CEI de los KPIs para el resumen de consola
        try:
            kpis_mxn = kpis.get("kpis_resumen_mxn", pd.DataFrame())
            kpis_usd = kpis.get("kpis_resumen_usd", pd.DataFrame())
            def _kpi_val(df_k: pd.DataFrame, campo: str) -> str:
                if df_k.empty or "INDICADOR" not in df_k.columns or "VALOR" not in df_k.columns:
                    return "—"
                fila = df_k[df_k["INDICADOR"].astype(str).str.contains(campo, case=False)]
                return str(fila["VALOR"].iloc[0]) if not fila.empty else "—"
            dso_mxn = _kpi_val(kpis_mxn, "DSO")
            cei_mxn = _kpi_val(kpis_mxn, "CEI")
            dso_usd = _kpi_val(kpis_usd, "DSO")
            cei_usd = _kpi_val(kpis_usd, "CEI")
            _detalle(f"MXN — DSO: {dso_mxn} | CEI: {cei_mxn}   USD — DSO: {dso_usd} | CEI: {cei_usd}")
        except Exception:
            _detalle(f"{len(kpis)} tablas de KPIs generadas")

    # ── PASO 7: Excel ─────────────────────────────────────────────────────
    _paso_console(
        7,
        "Exportacion a archivos Excel" + (" (omitida)" if dry_run else ""),
        dry_run,
    )
    logger.info("PASO 7 — Exportacion Excel")

    t0 = time.perf_counter()
    archivos_generados: list[Path] = []
    if not dry_run:
        archivos_generados = exportar_tres_exceles(
            cxc=cxc, auditoria=auditoria, analisis=analisis, kpis=kpis,
            timestamp=timestamp, output_dir=effective_output_dir,
        )
        archivos_generados.extend(archivos_cobrador)
        archivos_generados.extend(pdfs_cobrador)
        _detalle(f"{len(archivos_generados)} archivos generados")
    tiempos["PASO 7 — Excel"] = time.perf_counter() - t0

    # ── PASO 8: Parquet ───────────────────────────────────────────────────
    _paso_console(
        8,
        "Exportacion Parquet para Power BI" + (" (omitida)" if skip_parquet else ""),
        dry_run,
    )
    if not skip_parquet:
        logger.info("PASO 8 — Parquet")
        t0 = time.perf_counter()
        if not dry_run:
            df_registros_totales = cxc.get("registros_totales_cxc", pd.DataFrame())
            df_registros_totales_pq, _ = extraer_banda(df_registros_totales)
            vistas_parquet: dict[str, pd.DataFrame] = {
                "reporte/movimientos_totales_cxc": cxc.get("movimientos_totales_cxc", pd.DataFrame()),
                "reporte/registros_totales_cxc":   df_registros_totales_pq,
            }
            archivos_parquet = exportar_parquet(
                vistas_parquet, effective_output_dir / "powerbi", timestamp
            )
            archivos_generados.extend(archivos_parquet)
            logger.info("Archivos Parquet generados: %d", len(archivos_parquet))
            _detalle(f"{len(archivos_parquet)} archivos Parquet → output/powerbi/")
        tiempos["PASO 8 — Parquet"] = time.perf_counter() - t0

    # ── PASO 9: Email ─────────────────────────────────────────────────────
    _omitir_email = dry_run or skip_email
    _paso_console(9, "Envio de correos" + (" (omitido)" if _omitir_email else ""), dry_run)
    logger.info("PASO 9 — Envio de correos")

    t0 = time.perf_counter()
    if not _omitir_email:
        rutas_email = cargar_rutas(BASE_DIR / "config" / "email_routes.toml")
        n_enviados = enviar_rutas(
            archivos=archivos_generados,
            rutas=rutas_email,
            smtp_user=EMAIL_SMTP_USER,
            azure_client_id=EMAIL_AZURE_CLIENT_ID,
            azure_tenant_id=EMAIL_AZURE_TENANT_ID,
            token_cache_path=EMAIL_TOKEN_CACHE,
        )
        if n_enviados:
            _detalle(f"{n_enviados} correo(s) enviado(s)")
        elif rutas_email:
            _detalle("Sin correos enviados (revisar configuracion o archivos)")
        else:
            _detalle("Sin rutas configuradas en email_routes.toml")
    else:
        logger.info("PASO 9 — Email omitido (%s).", "dry-run" if dry_run else "--skip-email")
    tiempos["PASO 9 — Email"] = time.perf_counter() - t0

    # ── Resumen final ─────────────────────────────────────────────────────
    tiempos["TOTAL"] = time.perf_counter() - t_pipeline

    logger.info("PIPELINE COMPLETADO — %d archivos generados", len(archivos_generados))
    for archivo in archivos_generados:
        logger.info("  %s", archivo.name)
    _log_tabla_tiempos(tiempos)

    _resumen_tiempos_console(tiempos)
    _console.print(
        f"\n[bold green]Pipeline completado.[/bold green] "
        f"{len(archivos_generados)} archivos generados."
    )

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define y parsea los argumentos de linea de comandos.

    Args:
        argv: Lista de argumentos a parsear. Si es ``None`` toma
            ``sys.argv[1:]``.

    Returns:
        ``argparse.Namespace`` con los flags ``skip_*``, ``dry_run``,
        ``output_dir``, ``solo_cobrador``, ``log_json`` y ``test_connection``.
    """
    parser = argparse.ArgumentParser(description="Pipeline de auditoria CxC para Microsip")
    parser.add_argument("--test-connection", action="store_true", help="Solo probar conexion a Firebird.")
    parser.add_argument("--skip-audit",      action="store_true", help="Saltar auditoria de anomalias.")
    parser.add_argument("--skip-analytics",  action="store_true", help="Saltar analisis de cartera.")
    parser.add_argument("--skip-kpis",       action="store_true", help="Saltar KPIs estrategicos.")
    parser.add_argument("--skip-parquet",    action="store_true", help="Saltar exportacion Parquet para Power BI.")
    parser.add_argument("--skip-email",      action="store_true", help="Saltar envio de correos.")
    parser.add_argument("--dry-run",         action="store_true", help="Ejecutar el pipeline sin escribir archivos.")
    parser.add_argument("--output-dir",      type=Path, default=None, metavar="PATH",
                        help="Directorio de salida (default: output/).")
    parser.add_argument("--solo-cobrador",   default=None, metavar="NOMBRE",
                        help="Solo generar el reporte del cobrador especificado.")
    parser.add_argument("--log-json",        action="store_true",
                        help="Emitir logs en formato JSON (apto para Elastic/Grafana/Loki).")
    return parser.parse_args(argv)


def main() -> int:
    """Punto de entrada del CLI del pipeline.

    Configura el logging, decide si ejecutar solo el test de conexion
    a Firebird o el pipeline completo, y propaga los flags ``skip_*``,
    ``dry_run``, ``output_dir`` y ``solo_cobrador``.

    Returns:
        Codigo de salida apto para ``sys.exit()``.
    """
    args = parse_args()
    configurar_logging(json_mode=args.log_json)
    if args.test_connection:
        connector = FirebirdConnector(FIREBIRD_CONFIG)
        return 0 if connector.test_connection() else 1
    return run_pipeline(
        skip_audit=args.skip_audit,
        skip_analytics=args.skip_analytics,
        skip_kpis=args.skip_kpis,
        skip_parquet=args.skip_parquet,
        skip_email=args.skip_email,
        output_dir=args.output_dir,
        solo_cobrador=args.solo_cobrador,
        dry_run=args.dry_run,
    )

if __name__ == "__main__":
    sys.exit(main())
