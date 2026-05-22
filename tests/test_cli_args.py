"""Tests para los argumentos CLI de main.py y la logica de filtrado de cobrador.

Cubre: parse_args() con todos los flags nuevos y existentes,
y filtrar_assignments_por_cobrador() como helper testeable de forma aislada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from main import parse_args
from src.reporte_cobrador import filtrar_assignments_por_cobrador


# ==================================================================
# parse_args — valores por defecto
# ==================================================================


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.skip_audit is False
    assert args.skip_analytics is False
    assert args.skip_kpis is False
    assert args.skip_parquet is False
    assert args.dry_run is False
    assert args.output_dir is None
    assert args.solo_cobrador is None
    assert args.test_connection is False


# ==================================================================
# parse_args — flags existentes siguen funcionando
# ==================================================================


def test_parse_args_skip_audit() -> None:
    args = parse_args(["--skip-audit"])
    assert args.skip_audit is True
    assert args.skip_analytics is False


def test_parse_args_skip_parquet() -> None:
    args = parse_args(["--skip-parquet"])
    assert args.skip_parquet is True


# ==================================================================
# parse_args — nuevos flags S12
# ==================================================================


def test_parse_args_output_dir() -> None:
    args = parse_args(["--output-dir", "/tmp/cxc_test"])
    assert args.output_dir == Path("/tmp/cxc_test")


def test_parse_args_output_dir_es_path() -> None:
    args = parse_args(["--output-dir", "/tmp/out"])
    assert isinstance(args.output_dir, Path)


def test_parse_args_solo_cobrador() -> None:
    args = parse_args(["--solo-cobrador", "JUAN PEREZ"])
    assert args.solo_cobrador == "JUAN PEREZ"


def test_parse_args_dry_run() -> None:
    args = parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parse_args_combinacion_flags() -> None:
    args = parse_args([
        "--dry-run",
        "--output-dir", "/tmp/out",
        "--solo-cobrador", "MARIA",
        "--skip-audit",
    ])
    assert args.dry_run is True
    assert args.output_dir == Path("/tmp/out")
    assert args.solo_cobrador == "MARIA"
    assert args.skip_audit is True
    assert args.skip_analytics is False


# ==================================================================
# filtrar_assignments_por_cobrador
# ==================================================================

ASSIGNMENTS: dict[str, str] = {
    "CLIENTE_ALPHA":  "JUAN",
    "CLIENTE_BETA":   "MARIA",
    "CLIENTE_GAMMA":  "JUAN",
    "CLIENTE_DELTA":  "PEDRO",
    "CLIENTE_EPSILON": "PENDIENTE",
}


def test_filtrar_sin_cobrador_retorna_todos() -> None:
    resultado = filtrar_assignments_por_cobrador(ASSIGNMENTS, None)
    assert resultado == ASSIGNMENTS


def test_filtrar_con_cobrador_filtra_correctamente() -> None:
    resultado = filtrar_assignments_por_cobrador(ASSIGNMENTS, "JUAN")
    assert set(resultado.keys()) == {"CLIENTE_ALPHA", "CLIENTE_GAMMA"}
    assert all(v == "JUAN" for v in resultado.values())


def test_filtrar_cobrador_inexistente_retorna_vacio() -> None:
    resultado = filtrar_assignments_por_cobrador(ASSIGNMENTS, "NO_EXISTE")
    assert resultado == {}


def test_filtrar_assignments_vacias_retorna_vacio() -> None:
    assert filtrar_assignments_por_cobrador({}, "JUAN") == {}


def test_filtrar_no_modifica_original() -> None:
    copia = dict(ASSIGNMENTS)
    filtrar_assignments_por_cobrador(ASSIGNMENTS, "JUAN")
    assert ASSIGNMENTS == copia


def test_filtrar_pendiente_funciona() -> None:
    resultado = filtrar_assignments_por_cobrador(ASSIGNMENTS, "PENDIENTE")
    assert list(resultado.keys()) == ["CLIENTE_EPSILON"]


def test_filtrar_un_solo_cliente() -> None:
    asigs = {"UNICO": "ROSA"}
    assert filtrar_assignments_por_cobrador(asigs, "ROSA") == asigs
    assert filtrar_assignments_por_cobrador(asigs, "OTRO") == {}
