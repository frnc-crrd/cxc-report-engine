"""Tests unitarios para src/cobrador_cli.py.

Cubre la logica pura de cada comando sin requerir PostgreSQL.
El CobradorManager se reemplaza con MagicMock en todos los tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.cobrador_cli import (
    _buscar_nombre_fallback,
    _detectar_encoding,
    cmd_asignar,
    cmd_cobradores,
    cmd_eliminar,
    cmd_exportar,
    cmd_importar,
    cmd_listar,
    cmd_pendientes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mgr_con_assignments(assignments: dict[str, str]) -> MagicMock:
    mgr = MagicMock()
    mgr.get_assignments.return_value = assignments
    return mgr


# ---------------------------------------------------------------------------
# _detectar_encoding
# ---------------------------------------------------------------------------

def test_detectar_encoding_utf16_le(tmp_path: Path) -> None:
    """BOM UTF-16 LE (FF FE) debe detectarse como utf-16."""
    f = tmp_path / "test.csv"
    f.write_bytes(b"\xff\xfeNOMBRE_CLIENTE,COBRADOR\r\n")
    assert _detectar_encoding(f) == "utf-16"


def test_detectar_encoding_utf16_be(tmp_path: Path) -> None:
    """BOM UTF-16 BE (FE FF) debe detectarse como utf-16."""
    f = tmp_path / "test.csv"
    f.write_bytes(b"\xfe\xffNOMBRE_CLIENTE,COBRADOR\r\n")
    assert _detectar_encoding(f) == "utf-16"


def test_detectar_encoding_utf8_bom(tmp_path: Path) -> None:
    """BOM UTF-8 (EF BB BF) debe detectarse como utf-8-sig."""
    f = tmp_path / "test.csv"
    f.write_bytes(b"\xef\xbb\xbfNOMBRE_CLIENTE,COBRADOR\r\n")
    assert _detectar_encoding(f) == "utf-8-sig"


def test_detectar_encoding_utf8_sin_bom(tmp_path: Path) -> None:
    """Archivo sin BOM debe detectarse como utf-8."""
    f = tmp_path / "test.csv"
    f.write_text("NOMBRE_CLIENTE,COBRADOR\n", encoding="utf-8")
    assert _detectar_encoding(f) == "utf-8"


# ---------------------------------------------------------------------------
# _buscar_nombre_fallback
# ---------------------------------------------------------------------------

def test_buscar_nombre_fallback_resuelve_enye(tmp_path: Path) -> None:
    """'?' debe coincidir con caracter no-ASCII como 'Ñ'."""
    assignments = {"SALDAÑA": "HAIDEE", "SALDANA": "JUAN"}
    assert _buscar_nombre_fallback("SALDA?A", assignments) == "SALDAÑA"


def test_buscar_nombre_fallback_sin_interrogacion_devuelve_none() -> None:
    """Si el nombre no tiene '?' no hay nada que resolver."""
    assignments = {"SALDANA": "JUAN"}
    assert _buscar_nombre_fallback("SALDANA", assignments) is None


def test_buscar_nombre_fallback_ambiguo_devuelve_none() -> None:
    """Si mas de un nombre coincide con el patron, devuelve None para no asignar mal."""
    # SALDAÑA y SALDAÜA ambas tienen un caracter no-ASCII en la posicion 5 -> ambiguo
    assignments = {"SALDAÑA": "HAIDEE", "SALDAÜA": "JUAN"}
    assert _buscar_nombre_fallback("SALDA?A", assignments) is None


def test_cmd_importar_resuelve_interrogacion_via_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """importar debe resolver '?' via fallback y actualizar con el nombre canonico de la DB."""
    csv_file = tmp_path / "asig.csv"
    csv_file.write_text("NOMBRE_CLIENTE,COBRADOR\nSALDA?A,HAIDEE\n", encoding="utf-8")
    mgr = MagicMock()
    mgr.get_assignments.return_value = {"SALDAÑA": "PENDIENTE"}
    mgr.bulk_update.return_value = {"actualizados": 1}
    cmd_importar(mgr, str(csv_file))
    llamada = mgr.bulk_update.call_args[0][0]
    assert llamada[0]["nombre_cliente"] == "SALDAÑA"
    out = capsys.readouterr().out
    assert "fallback" in out.lower()


def test_detectar_encoding_cp1252(tmp_path: Path) -> None:
    """CSV sin BOM con caracteres CP1252 (estandar Microsip) debe detectarse como cp1252."""
    f = tmp_path / "test.csv"
    # 0xd1='N con tilde', 0xcd='I con acento agudo' en CP1252 — invalidos en UTF-8 strict
    f.write_bytes(b"NOMBRE_CLIENTE,COBRADOR\r\nSALDA\xd1A,JUAN\r\n")
    assert _detectar_encoding(f) == "cp1252"


def test_cmd_importar_archivo_cp1252(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """importar debe leer correctamente un CSV guardado con CP1252 (Excel Mexico)."""
    f = tmp_path / "cp1252.csv"
    # "SALDAÑA" y "MUÑOZ" en CP1252
    f.write_bytes(
        b"NOMBRE_CLIENTE,COBRADOR\r\n"
        b"EMPRESA SALDA\xd1A,HAIDEE\r\n"
        b"EMPRESA MU\xd1OZ,JUAN\r\n"
    )
    mgr = MagicMock()
    mgr.get_assignments.return_value = {
        "EMPRESA SALDAÑA": "PENDIENTE",
        "EMPRESA MUÑOZ": "PENDIENTE",
    }
    mgr.bulk_update.return_value = {"actualizados": 2}
    cmd_importar(mgr, str(f))
    called_args = mgr.bulk_update.call_args[0][0]
    nombres = {r["nombre_cliente"] for r in called_args}
    assert "EMPRESA SALDAÑA" in nombres
    assert "EMPRESA MUÑOZ" in nombres
    out = capsys.readouterr().out
    assert "2 actualizados" in out


# ---------------------------------------------------------------------------
# cmd_pendientes
# ---------------------------------------------------------------------------

def test_cmd_pendientes_imprime_clientes_pendientes(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"CLIENTE A": "PENDIENTE", "CLIENTE B": "HAIDEE", "CLIENTE C": "PENDIENTE"}
    cmd_pendientes(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "CLIENTE A" in out
    assert "CLIENTE C" in out
    assert "CLIENTE B" not in out


def test_cmd_pendientes_sin_pendientes_imprime_mensaje(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"CLIENTE A": "HAIDEE", "CLIENTE B": "JOVANNA"}
    cmd_pendientes(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "No hay clientes sin asignar" in out


def test_cmd_pendientes_muestra_conteo(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"A": "PENDIENTE", "B": "PENDIENTE", "C": "JUAN"}
    cmd_pendientes(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "2" in out


# ---------------------------------------------------------------------------
# cmd_listar
# ---------------------------------------------------------------------------

def test_cmd_listar_agrupa_por_cobrador(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"EMPRESA X": "HAIDEE", "EMPRESA Y": "HAIDEE", "EMPRESA Z": "JOVANNA"}
    cmd_listar(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "HAIDEE" in out
    assert "JOVANNA" in out
    assert "EMPRESA X" in out


def test_cmd_listar_sin_datos_imprime_mensaje(capsys: pytest.CaptureFixture[str]) -> None:
    cmd_listar(_mgr_con_assignments({}))
    out = capsys.readouterr().out
    assert "No hay clientes" in out


def test_cmd_listar_muestra_total(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"A": "X", "B": "X", "C": "Y"}
    cmd_listar(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "Total: 3" in out


# ---------------------------------------------------------------------------
# cmd_cobradores
# ---------------------------------------------------------------------------

def test_cmd_cobradores_imprime_tabla(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"A": "HAIDEE", "B": "HAIDEE", "C": "JOVANNA"}
    cmd_cobradores(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "HAIDEE" in out
    assert "JOVANNA" in out


def test_cmd_cobradores_sin_datos_imprime_mensaje(capsys: pytest.CaptureFixture[str]) -> None:
    cmd_cobradores(_mgr_con_assignments({}))
    out = capsys.readouterr().out
    assert "No hay datos" in out


def test_cmd_cobradores_conteo_correcto(capsys: pytest.CaptureFixture[str]) -> None:
    assignments = {"A": "JUAN", "B": "JUAN", "C": "JUAN"}
    cmd_cobradores(_mgr_con_assignments(assignments))
    out = capsys.readouterr().out
    assert "3" in out


# ---------------------------------------------------------------------------
# cmd_asignar
# ---------------------------------------------------------------------------

def test_cmd_asignar_llama_update_cobrador(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.update_cobrador.return_value = True
    cmd_asignar(mgr, "empresa xyz", "HAIDEE")
    mgr.update_cobrador.assert_called_once_with("EMPRESA XYZ", "HAIDEE")


def test_cmd_asignar_normaliza_nombre_a_mayusculas(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.update_cobrador.return_value = True
    cmd_asignar(mgr, "  empresa xyz  ", "HAIDEE")
    mgr.update_cobrador.assert_called_once_with("EMPRESA XYZ", "HAIDEE")


def test_cmd_asignar_cliente_no_encontrado_sale_con_error() -> None:
    mgr = MagicMock()
    mgr.update_cobrador.return_value = False
    with pytest.raises(SystemExit) as exc_info:
        cmd_asignar(mgr, "INEXISTENTE", "HAIDEE")
    assert exc_info.value.code == 1


def test_cmd_asignar_exitoso_imprime_confirmacion(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.update_cobrador.return_value = True
    cmd_asignar(mgr, "EMPRESA TEST", "JOVANNA")
    out = capsys.readouterr().out
    assert "EMPRESA TEST" in out
    assert "JOVANNA" in out


# ---------------------------------------------------------------------------
# cmd_eliminar
# ---------------------------------------------------------------------------

def test_cmd_eliminar_llama_delete_cliente(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.delete_cliente.return_value = True
    cmd_eliminar(mgr, "empresa b")
    mgr.delete_cliente.assert_called_once_with("EMPRESA B")


def test_cmd_eliminar_normaliza_a_mayusculas(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.delete_cliente.return_value = True
    cmd_eliminar(mgr, "  empresa b  ")
    mgr.delete_cliente.assert_called_once_with("EMPRESA B")


def test_cmd_eliminar_no_encontrado_sale_con_error() -> None:
    mgr = MagicMock()
    mgr.delete_cliente.return_value = False
    with pytest.raises(SystemExit) as exc_info:
        cmd_eliminar(mgr, "INEXISTENTE")
    assert exc_info.value.code == 1


def test_cmd_eliminar_exitoso_imprime_confirmacion(capsys: pytest.CaptureFixture[str]) -> None:
    mgr = MagicMock()
    mgr.delete_cliente.return_value = True
    cmd_eliminar(mgr, "EMPRESA B")
    out = capsys.readouterr().out
    assert "EMPRESA B" in out


# ---------------------------------------------------------------------------
# cmd_importar
# ---------------------------------------------------------------------------

def _csv_utf8(tmp_path: Path, contenido: str) -> Path:
    f = tmp_path / "asignaciones.csv"
    f.write_text(contenido, encoding="utf-8")
    return f


def test_cmd_importar_actualiza_clientes_encontrados(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = _csv_utf8(tmp_path, "NOMBRE_CLIENTE,COBRADOR\nEMPRESA A,HAIDEE\nEMPRESA B,JOVANNA\n")
    mgr = MagicMock()
    mgr.get_assignments.return_value = {"EMPRESA A": "PENDIENTE", "EMPRESA B": "PENDIENTE"}
    mgr.bulk_update.return_value = {"actualizados": 2}
    cmd_importar(mgr, str(csv))
    mgr.bulk_update.assert_called_once()
    out = capsys.readouterr().out
    assert "2 actualizados" in out


def test_cmd_importar_omite_clientes_no_en_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = _csv_utf8(tmp_path, "NOMBRE_CLIENTE,COBRADOR\nINEXISTENTE,HAIDEE\n")
    mgr = MagicMock()
    mgr.get_assignments.return_value = {}
    mgr.bulk_update.return_value = {"actualizados": 0}
    cmd_importar(mgr, str(csv))
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "1 omitidos" in out


def test_cmd_importar_archivo_inexistente_sale_con_error(tmp_path: Path) -> None:
    mgr = MagicMock()
    with pytest.raises(SystemExit) as exc_info:
        cmd_importar(mgr, str(tmp_path / "no_existe.csv"))
    assert exc_info.value.code == 1


def test_cmd_importar_csv_sin_columnas_requeridas_sale_con_error(tmp_path: Path) -> None:
    csv = _csv_utf8(tmp_path, "COLUMNA_MALA,OTRA\nA,B\n")
    mgr = MagicMock()
    mgr.get_assignments.return_value = {}
    with pytest.raises(SystemExit) as exc_info:
        cmd_importar(mgr, str(csv))
    assert exc_info.value.code == 1


def test_cmd_importar_delimitador_punto_coma(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = _csv_utf8(tmp_path, "NOMBRE_CLIENTE;COBRADOR\nEMPRESA A;HAIDEE\n")
    mgr = MagicMock()
    mgr.get_assignments.return_value = {"EMPRESA A": "PENDIENTE"}
    mgr.bulk_update.return_value = {"actualizados": 1}
    cmd_importar(mgr, str(csv))
    mgr.bulk_update.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_exportar
# ---------------------------------------------------------------------------

def test_cmd_exportar_crea_archivo_csv(tmp_path: Path) -> None:
    """cmd_exportar debe crear el archivo CSV en la ruta indicada."""
    archivo = str(tmp_path / "salida.csv")
    assignments = {"EMPRESA A": "HAIDEE", "EMPRESA B": "PENDIENTE"}
    mgr = _mgr_con_assignments(assignments)
    cmd_exportar(mgr, archivo)
    assert Path(archivo).exists()


def test_cmd_exportar_escribe_columnas_correctas(tmp_path: Path) -> None:
    """El CSV exportado debe tener exactamente las columnas NOMBRE_CLIENTE y COBRADOR."""
    import csv as csv_mod

    archivo = str(tmp_path / "salida.csv")
    mgr = _mgr_con_assignments({"EMPRESA A": "HAIDEE"})
    cmd_exportar(mgr, archivo)
    with open(archivo, newline="", encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        assert reader.fieldnames == ["NOMBRE_CLIENTE", "COBRADOR"]


def test_cmd_exportar_escribe_todas_las_asignaciones(tmp_path: Path) -> None:
    """El CSV exportado debe contener una fila por cada asignacion del mapa."""
    import csv as csv_mod

    assignments = {"EMPRESA A": "HAIDEE", "EMPRESA B": "JOVANNA", "EMPRESA C": "PENDIENTE"}
    archivo = str(tmp_path / "salida.csv")
    mgr = _mgr_con_assignments(assignments)
    cmd_exportar(mgr, archivo)
    with open(archivo, newline="", encoding="utf-8") as f:
        filas = list(csv_mod.DictReader(f))
    assert len(filas) == 3
    exportadas = {r["NOMBRE_CLIENTE"]: r["COBRADOR"] for r in filas}
    assert exportadas == assignments


def test_cmd_exportar_db_vacia_muestra_mensaje(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si no hay asignaciones, cmd_exportar imprime mensaje informativo sin crear CSV."""
    archivo = str(tmp_path / "salida.csv")
    mgr = _mgr_con_assignments({})
    cmd_exportar(mgr, archivo)
    out = capsys.readouterr().out
    assert "sin datos" in out.lower() or "no hay" in out.lower() or "vac" in out.lower()
    assert not Path(archivo).exists()


def test_cmd_exportar_crea_bak_si_archivo_existe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si el archivo destino ya existe, exportar debe crear un .bak antes de sobreescribir."""
    archivo = tmp_path / "salida.csv"
    archivo.write_text("contenido_original", encoding="utf-8")
    mgr = _mgr_con_assignments({"EMPRESA A": "HAIDEE"})
    cmd_exportar(mgr, str(archivo))
    bak = tmp_path / "salida.csv.bak"
    assert bak.exists(), "Debe existir archivo .bak"
    assert bak.read_text(encoding="utf-8") == "contenido_original"
    out = capsys.readouterr().out
    assert "respaldo" in out.lower() or ".bak" in out.lower()
