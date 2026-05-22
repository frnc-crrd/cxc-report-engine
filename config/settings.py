"""Configuracion del proyecto de auditoria CxC para Microsip.

Define rutas de archivos, parametros de analisis (rangos de antiguedad,
umbrales de anomalias), ventana de KPIs, nombres de archivos Excel de
salida y contrasenas de hojas protegidas.

Las credenciales de conexion a la base de datos Firebird se gestionan
mediante variables de entorno por motivos de seguridad, evitando su
exposicion en el codigo fuente.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from config.schema import CobradorConfig, FirebirdConfig

# ============================================================================
# RUTAS DEL PROYECTO
# ============================================================================

BASE_DIR: Path = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Path = BASE_DIR / "output"

# Cargar variables de entorno desde el archivo .env si existe en el entorno local
load_dotenv(BASE_DIR / ".env")

# ============================================================================
# CONEXION A FIREBIRD (Microsip - Produccion)
# ============================================================================

_port_str = os.getenv("FIREBIRD_PORT")
FIREBIRD_CONFIG: dict[str, str | int | None] = FirebirdConfig(
    host=os.getenv("FIREBIRD_HOST", os.getenv("FIREBIRD_IP")),
    port=int(_port_str) if _port_str else None,
    database=os.getenv("FIREBIRD_DATABASE") or "",
    user=os.getenv("FIREBIRD_USER"),
    password=os.getenv("FIREBIRD_PASSWORD") or "",
    charset=os.getenv("FIREBIRD_CHARSET"),
).model_dump()

# ============================================================================
# RANGOS DE ANTIGUEDAD Y RECAUDO
# ============================================================================

RANGOS_ANTIGUEDAD: list[tuple[int | None, int | None, str]] = [
    (None, -2, "VIGENTE: MÁS DE 1 DÍA"),
    (-1,   -1, "VIGENTE: VENCE MAÑANA"),
    (0,    0,  "VIGENTE: VENCE HOY"),
    (1,    30, "VENCIDO: 1-30 DÍAS"),
    (31,   60, "VENCIDO: 31-60 DÍAS"),
    (61,   90, "VENCIDO: 61-90 DÍAS"),
    (91,   120,"VENCIDO: 91-120 DÍAS"),
    (121, None,"VENCIDO: MÁS DE 120 DÍAS"),
]

RANGOS_RECAUDO: list[tuple[int | None, int | None, str]] = [
    (None, -1, "PAGO ANTICIPADO"),
    (0,    0,  "PAGO PUNTUAL"),
    (1,    15, "RETRASO LEVE (1-15)"),
    (16,   30, "RETRASO MODERADO (16-30)"),
    (31,   60, "RETRASO ALTO (31-60)"),
    (61, None, "RETRASO CRITICO (>60)"),
]

ANOMALIAS: dict[str, int | float] = {
    "importe_zscore_umbral":       3.0,
    "delta_recaudo_zscore_umbral": 3.0,
    "delta_mora_zscore_umbral":    3.0,
    "dias_vencimiento_critico":    90,
}

# ============================================================================
# KPIS ESTRATEGICOS
# ============================================================================

KPI_PERIODO_DIAS: int = 90

# ============================================================================
# ARCHIVOS DE SALIDA
# ============================================================================

OUTPUT_FORMATS: list[str] = ["xlsx"]

EXCEL_ENGINE: str = "openpyxl"

EXCEL_NOMBRES: dict[str, str] = {
    "cxc":       "01_reporte_cxc",
    "analisis":  "02_analisis_cxc",
    "auditoria": "03_auditoria_cxc",
}

SHEET_PASSWORDS: dict[str, str | None] = {
    "registros_totales_cxc": os.getenv("EXCEL_SHEET_PASSWORD"),
}

# ============================================================================
# COBRADOR DB — PostgreSQL en contenedor Podman (compose.yml)
# ============================================================================

COBRADOR_DB_URL: str = CobradorConfig(
    url=os.getenv("COBRADOR_DB_URL") or (
        "postgresql://{user}:{pwd}@{host}:{port}/{db}".format(
            user=os.getenv("COBRADOR_DB_USER", "cxc_admin"),
            pwd=os.getenv("COBRADOR_DB_PASSWORD", ""),
            host=os.getenv("COBRADOR_DB_HOST", "localhost"),
            port=os.getenv("COBRADOR_DB_PORT", "5432"),
            db=os.getenv("COBRADOR_DB_NAME", "cobrador"),
        )
    )
).url

# ============================================================================
# EMAIL — Microsoft Graph API + OAuth2 (MSAL)
# ============================================================================

EMAIL_SMTP_USER: str = os.getenv("SMTP_USER", "")
EMAIL_AZURE_CLIENT_ID: str = os.getenv("AZURE_CLIENT_ID", "")
EMAIL_AZURE_TENANT_ID: str = os.getenv("AZURE_TENANT_ID", "consumers")
EMAIL_TOKEN_CACHE: Path = BASE_DIR / os.getenv("MSAL_TOKEN_CACHE", ".msal_token_cache.bin")

# ============================================================================
# CONSTANTES DE DOMINIO COMPARTIDAS
# ============================================================================

# Valores que Microsip usa para marcar documentos como cancelados.
# Centralizado aquí para evitar duplicación entre auditor, reporte_cxc y main.
CANCELADO_VALUES: list[str | int | bool] = ["S", "SI", "s", "si", 1, True, "1"]