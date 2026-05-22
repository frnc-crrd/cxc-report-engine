"""Generador de reportes PDF por cobrador, orientacion landscape A4.

Columnas incluidas (subset de movimientos_abiertos_cxc):
    NOMBRE_CLIENTE, MONEDA, CONCEPTO, FOLIO, FECHA_EMISION,
    FECHA_VENCIMIENTO, CARGOS, ABONOS, SALDO_FACTURA, DELTA_MORA,
    CATEGORIA_MORA

Caracteristicas:
    - A4 horizontal con margenes reducidos para maximizar la tabla
    - Encabezado institucional con cobrador, fecha y metricas clave
    - Filas alternadas azul palido / blanco para facilitar lectura
    - CATEGORIA_MORA con semaforo de colores por nivel de riesgo
    - Fila de totales al final de la ultima pagina
    - Pie de pagina con numeracion "Pagina X de Y"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.frames import Frame

logger = logging.getLogger(__name__)

# ── Columnas a incluir en el PDF (orden exacto) ────────────────────────────
_COLUMNAS_PDF = [
    "NOMBRE_CLIENTE",
    "MONEDA",
    "CONCEPTO",
    "FOLIO",
    "FECHA_EMISION",
    "FECHA_VENCIMIENTO",
    "CARGOS",
    "ABONOS",
    "SALDO_FACTURA",
    "DELTA_MORA",
    "CATEGORIA_MORA",
]

_HEADERS_DISPLAY = [
    "Cliente",
    "Mon.",
    "Concepto",
    "Folio",
    "F. Emision",
    "F. Vencimiento",
    "Cargos",
    "Abonos",
    "Saldo",
    "Mora",
    "Categoria",
]

# Ancho de cada columna en puntos (A4 landscape usable con margenes 12mm = ~774 pts)
_COL_WIDTHS = [148, 34, 67, 50, 55, 60, 65, 65, 67, 43, 110]

# ── Paleta de colores ──────────────────────────────────────────────────────
_NAVY      = colors.HexColor("#1E3A5F")
_COBALT    = colors.HexColor("#2563EB")
_ROW_ODD   = colors.HexColor("#EFF4FB")
_ROW_EVEN  = colors.white
_TOTAL_BG  = colors.HexColor("#D6E4F7")
_TEXT_DARK = colors.HexColor("#1A1A2E")
_TEXT_MUTED = colors.HexColor("#6C757D")

# Semaforo CATEGORIA_MORA: (bg, texto)
_CAT_COLORS: dict[str, tuple[Any, Any]] = {
    "vigente: vence hoy":       (colors.HexColor("#FFF8DC"), colors.HexColor("#856404")),
    "vigente: vence mañana":    (colors.HexColor("#D4EDDA"), colors.HexColor("#155724")),
    "vigente:":                 (colors.HexColor("#D4EDDA"), colors.HexColor("#155724")),
    "vencido: 1-30":            (colors.HexColor("#FFF3CD"), colors.HexColor("#856404")),
    "vencido: 31-60":           (colors.HexColor("#FFE5B4"), colors.HexColor("#8B4513")),
    "vencido: 61-90":           (colors.HexColor("#FFCBA4"), colors.HexColor("#7B2D00")),
    "vencido: 91-120":          (colors.HexColor("#FFCDD2"), colors.HexColor("#7F0000")),
    "vencido: más de 120":      (colors.HexColor("#EF9A9A"), colors.HexColor("#5B0000")),
    "vencido: más":             (colors.HexColor("#EF9A9A"), colors.HexColor("#5B0000")),
}


def _color_categoria(valor: str | None) -> tuple[Any, Any]:
    if not valor:
        return colors.white, _TEXT_DARK
    low = str(valor).lower()
    for key, pair in _CAT_COLORS.items():
        if low.startswith(key):
            return pair
    return colors.white, _TEXT_DARK


# ── Formateadores ──────────────────────────────────────────────────────────

def _fmt_fecha(val: Any) -> str:
    if val is None:
        return "—"
    try:
        if pd.isna(val):
            return "—"
    except (TypeError, ValueError):
        pass
    if hasattr(val, "strftime"):
        try:
            return str(val.strftime("%d/%m/%Y"))
        except (ValueError, AttributeError):
            return "—"
    return str(val)


def _fmt_moneda(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    try:
        v = float(val)
        return f"${v:,.2f}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_delta(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    try:
        d = int(float(val))
        return f"+{d}d" if d > 0 else f"{d}d"
    except (TypeError, ValueError):
        return str(val)


def _fmt_str(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return str(val).strip()


# ── Estilos de parrafo para celdas ────────────────────────────────────────

def _make_cell_style(
    align: int = TA_LEFT,
    font_size: int = 7,
    text_color: Any = _TEXT_DARK,
    bold: bool = False,
) -> ParagraphStyle:
    return ParagraphStyle(
        name="cell",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=font_size,
        leading=font_size + 2,
        textColor=text_color,
        alignment=align,
        spaceAfter=0,
        spaceBefore=0,
    )


_STYLE_HEADER = _make_cell_style(TA_CENTER, 7, colors.white, bold=True)
_STYLE_LEFT   = _make_cell_style(TA_LEFT,   7, _TEXT_DARK)
_STYLE_CENTER = _make_cell_style(TA_CENTER, 7, _TEXT_DARK)
_STYLE_RIGHT  = _make_cell_style(TA_RIGHT,  7, _TEXT_DARK)
_STYLE_MUTED  = _make_cell_style(TA_RIGHT,  7, _TEXT_MUTED)
_STYLE_TOTAL  = _make_cell_style(TA_RIGHT,  7, _NAVY, bold=True)
_STYLE_TOTAL_LABEL = _make_cell_style(TA_LEFT, 7, _NAVY, bold=True)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    # Escapar caracteres reservados HTML en reportlab
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(text, style)


# ── Constructor de la tabla de datos ──────────────────────────────────────

def _build_data_rows(df: pd.DataFrame) -> list[list[Any]]:
    """Convierte el DataFrame a filas de Paragraphs para la tabla."""
    rows: list[list[Any]] = []
    for _, row in df.iterrows():
        cat_val = _fmt_str(row.get("CATEGORIA_MORA"))
        _, cat_text_color = _color_categoria(cat_val if cat_val != "—" else None)
        cat_style = _make_cell_style(TA_CENTER, 6, cat_text_color)

        delta_val = row.get("DELTA_MORA")
        delta_str = _fmt_delta(delta_val)
        try:
            d_num = int(float(delta_val)) if delta_val is not None and not (isinstance(delta_val, float) and pd.isna(delta_val)) else 0
            delta_color = colors.HexColor("#DC3545") if d_num > 0 else colors.HexColor("#198754")
        except (TypeError, ValueError):
            delta_color = _TEXT_DARK
        delta_style = _make_cell_style(TA_CENTER, 7, delta_color)

        rows.append([
            _p(_fmt_str(row.get("NOMBRE_CLIENTE")), _STYLE_LEFT),
            _p(_fmt_str(row.get("MONEDA")),         _STYLE_CENTER),
            _p(_fmt_str(row.get("CONCEPTO")),       _STYLE_LEFT),
            _p(_fmt_str(row.get("FOLIO")),          _STYLE_CENTER),
            _p(_fmt_fecha(row.get("FECHA_EMISION")),       _STYLE_CENTER),
            _p(_fmt_fecha(row.get("FECHA_VENCIMIENTO")),   _STYLE_CENTER),
            _p(_fmt_moneda(row.get("CARGOS")),      _STYLE_RIGHT),
            _p(_fmt_moneda(row.get("ABONOS")),      _STYLE_RIGHT),
            _p(_fmt_moneda(row.get("SALDO_FACTURA")), _STYLE_RIGHT),
            _p(delta_str,                           delta_style),
            _p(cat_val,                             cat_style),
        ])
    return rows


def _fmt_saldo_header(df: pd.DataFrame) -> str:
    """Saldo(s) para el encabezado: uno por moneda presente, separados por ' | '."""
    monedas: list[str] = []
    if "MONEDA" in df.columns:
        monedas = sorted(str(m) for m in df["MONEDA"].dropna().unique())
    if not monedas:
        try:
            return _fmt_moneda(pd.to_numeric(df["SALDO_FACTURA"], errors="coerce").sum())
        except KeyError:
            return "—"
    parts: list[str] = []
    for m in monedas:
        sub = df[df["MONEDA"] == m]
        try:
            total = pd.to_numeric(sub["SALDO_FACTURA"], errors="coerce").sum()
            parts.append(f"{m} {_fmt_moneda(total)}")
        except KeyError:
            parts.append(f"{m} —")
    return "   |   ".join(parts)


def _build_totals_rows(df: pd.DataFrame) -> list[list[Any]]:
    """Una fila de totales por moneda presente en el DataFrame."""
    def _sum_sub(col: str, sub: pd.DataFrame) -> str:
        try:
            return _fmt_moneda(pd.to_numeric(sub[col], errors="coerce").sum())
        except KeyError:
            return "—"

    def _make_row(label: str, sub: pd.DataFrame) -> list[Any]:
        return [
            _p(label,                        _STYLE_TOTAL_LABEL),
            _p("",                           _STYLE_TOTAL),
            _p("",                           _STYLE_TOTAL),
            _p(f"{len(sub)} reg.",           _STYLE_TOTAL),
            _p("",                           _STYLE_TOTAL),
            _p("",                           _STYLE_TOTAL),
            _p(_sum_sub("CARGOS",        sub), _STYLE_TOTAL),
            _p(_sum_sub("ABONOS",        sub), _STYLE_TOTAL),
            _p(_sum_sub("SALDO_FACTURA", sub), _STYLE_TOTAL),
            _p("",                           _STYLE_TOTAL),
            _p("",                           _STYLE_TOTAL),
        ]

    monedas: list[str] = []
    if "MONEDA" in df.columns:
        monedas = sorted(str(m) for m in df["MONEDA"].dropna().unique())

    if len(monedas) <= 1:
        label = f"TOTALES {monedas[0]}" if monedas else "TOTALES"
        sub = df[df["MONEDA"] == monedas[0]] if monedas and "MONEDA" in df.columns else df
        return [_make_row(label, sub)]

    return [_make_row(f"TOTALES {m}", df[df["MONEDA"] == m]) for m in monedas]


def _build_table(df: pd.DataFrame) -> Table:
    """Construye la Table de reportlab con estilos y colores de categoria."""
    header_row  = [_p(h, _STYLE_HEADER) for h in _HEADERS_DISPLAY]
    data_rows   = _build_data_rows(df)
    totals_rows = _build_totals_rows(df)

    all_rows = [header_row] + data_rows + totals_rows
    n_data   = len(data_rows)
    n_totals = len(totals_rows)

    tbl = Table(all_rows, colWidths=_COL_WIDTHS, repeatRows=1)

    # Estilos base
    base_style: list[Any] = [
        # Encabezado
        ("BACKGROUND",  (0, 0), (-1, 0), _NAVY),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUND", (0, 0), (-1, 0), _NAVY),
        # Bordes generales
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D6E8")),
        ("LINEBELOW",   (0, 0), (-1, 0), 1.0, _COBALT),
        # Linea sobre la primera fila de totales
        ("LINEABOVE",   (0, 1 + n_data), (-1, 1 + n_data), 1.0, _NAVY),
        # Padding
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        # Alineacion vertical centrada en todas las celdas
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ]

    # Fondo de cada fila de totales (una por moneda)
    for j in range(n_totals):
        base_style.append(("BACKGROUND", (0, 1 + n_data + j), (-1, 1 + n_data + j), _TOTAL_BG))

    # Colores alternados por grupo venta: cambia de banda cada vez que CONCEPTO contiene "VENTA"
    if "CONCEPTO" in df.columns:
        es_venta = df["CONCEPTO"].str.contains("VENTA", case=False, na=False)
        bands: list[int] = [int(v) for v in (es_venta.cumsum() % 2).tolist()]
    else:
        bands = [i % 2 for i in range(n_data)]
    for i, band in enumerate(bands):
        row_idx = i + 1
        bg = _ROW_ODD if band == 0 else _ROW_EVEN
        base_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))

    # Colorear columna CATEGORIA_MORA (col index 10) por semaforo
    cat_col = 10
    for i, (_, row) in enumerate(df.iterrows()):
        row_idx = i + 1
        cat_val = _fmt_str(row.get("CATEGORIA_MORA"))
        bg_cat, _ = _color_categoria(cat_val if cat_val != "—" else None)
        if bg_cat is not colors.white:
            base_style.append(("BACKGROUND", (cat_col, row_idx), (cat_col, row_idx), bg_cat))

    tbl.setStyle(TableStyle(base_style))
    return tbl


# ── Callbacks de pagina (header + footer) ─────────────────────────────────

def _make_page_callbacks(
    cobrador: str,
    fecha_str: str,
    n_facturas: int,
    saldo_str: str,
    doc_ref: list[int],   # mutable para pasar n_paginas por referencia
) -> tuple[Any, Any]:
    """Devuelve (onFirstPage, onLaterPages) para dibujar header y footer."""

    def _draw_page(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        w, h = landscape(A4)

        # ── Banda superior navy ───────────────────────────────────────────
        band_h = 18 * mm
        canvas.setFillColor(_NAVY)
        canvas.rect(0, h - band_h, w, band_h, fill=1, stroke=0)

        # Titulo
        canvas.setFont("Helvetica-Bold", 11)
        canvas.setFillColor(colors.white)
        canvas.drawString(12 * mm, h - 11 * mm, "REPORTE DE COBRANZA")

        # Cobrador
        canvas.setFont("Helvetica", 9)
        canvas.drawString(12 * mm, h - 16 * mm, f"Cobrador:  {cobrador}")

        # Metadatos derechos
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - 12 * mm, h - 10 * mm, fecha_str)
        canvas.drawRightString(
            w - 12 * mm, h - 16 * mm,
            f"{n_facturas} registros   |   Saldo: {saldo_str}",
        )

        # ── Franja de acento bajo la banda ───────────────────────────────
        canvas.setFillColor(_COBALT)
        canvas.rect(0, h - band_h - 1.5, w, 1.5, fill=1, stroke=0)

        # ── Footer ───────────────────────────────────────────────────────
        footer_y = 8 * mm
        canvas.setStrokeColor(colors.HexColor("#C8D6E8"))
        canvas.setLineWidth(0.5)
        canvas.line(12 * mm, footer_y + 4 * mm, w - 12 * mm, footer_y + 4 * mm)

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_TEXT_MUTED)
        total_pages = doc_ref[0] if doc_ref[0] > 0 else "?"
        canvas.drawString(12 * mm, footer_y, f"Pagina {doc.page} de {total_pages}")
        canvas.drawRightString(w - 12 * mm, footer_y, "CxC Audit Engine — generado automaticamente")

        canvas.restoreState()

    return _draw_page, _draw_page


# ── Funcion principal ──────────────────────────────────────────────────────

def generar_pdf_cobrador(
    df: pd.DataFrame,
    cobrador: str,
    output_path: Path,
) -> Path:
    """Genera el PDF de un cobrador y lo escribe en output_path.

    Args:
        df: DataFrame con las columnas de _COLUMNAS_PDF disponibles.
        cobrador: Nombre del cobrador (para el encabezado).
        output_path: Ruta de destino del archivo .pdf.

    Returns:
        La misma output_path una vez escrito el archivo.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Filtrar solo columnas disponibles en el orden correcto
    cols_disp = [c for c in _COLUMNAS_PDF if c in df.columns]
    df_pdf = df[cols_disp].copy()

    # Metricas para el encabezado
    n_facturas = len(df_pdf)
    saldo_str = _fmt_saldo_header(df_pdf)

    from datetime import date as _date
    fecha_str = _date.today().strftime("%d/%m/%Y")

    # Construir tabla
    tabla = _build_table(df_pdf)

    # Referencia mutable para el total de paginas (se rellena en dos pasadas)
    doc_ref: list[int] = [0]
    on_page, _ = _make_page_callbacks(cobrador, fecha_str, n_facturas, saldo_str, doc_ref)

    # ── Primera pasada: contar paginas ────────────────────────────────────
    from reportlab.platypus import SimpleDocTemplate as _SDT

    page_w, page_h = landscape(A4)
    margin = 12 * mm
    header_h = 20 * mm
    footer_h = 14 * mm

    doc = _SDT(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=header_h,
        bottomMargin=footer_h,
    )
    story: list[Any] = [tabla]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

    # Total real de paginas
    doc_ref[0] = doc.page

    # ── Segunda pasada: PDF final con "X de Y" correcto ───────────────────
    doc2 = _SDT(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=header_h,
        bottomMargin=footer_h,
    )
    on_page2, _ = _make_page_callbacks(cobrador, fecha_str, n_facturas, saldo_str, doc_ref)
    doc2.build([_build_table(df_pdf)], onFirstPage=on_page2, onLaterPages=on_page2)

    logger.info("PDF '%s': %d filas → %s", cobrador, n_facturas, output_path.name)
    return output_path


def generar_pdfs_por_cobrador(
    df_abiertos: pd.DataFrame,
    assignments: dict[str, str],
    output_dir: Path,
    timestamp: str,
) -> list[Path]:
    """Genera un PDF por cada cobrador (misma logica de split que los Excel).

    Args:
        df_abiertos: Vista movimientos_abiertos_cxc.
        assignments: Mapeo {nombre_cliente: cobrador}.
        output_dir:  Directorio de salida.
        timestamp:   Sufijo para los nombres de archivo.

    Returns:
        Lista de PDFs generados.
    """
    if df_abiertos.empty:
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import re

    def _slug(nombre: str) -> str:
        s = nombre.strip().replace(" ", "_")
        return re.sub(r"[^\w]", "", s, flags=re.ASCII)[:40] or "cobrador"

    df = df_abiertos.copy()
    df["_COBRADOR"] = df["NOMBRE_CLIENTE"].map(assignments).fillna("PENDIENTE")

    archivos: list[Path] = []
    for cobrador, grupo in df.groupby("_COBRADOR", sort=True):
        if grupo.empty:
            continue
        slug = _slug(str(cobrador))
        pdf_path = output_dir / f"COBRADOR_{slug}_{timestamp}.pdf"
        generar_pdf_cobrador(grupo.drop(columns=["_COBRADOR"]), str(cobrador), pdf_path)
        archivos.append(pdf_path)

    return archivos
