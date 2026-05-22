"""Tests para metricas de tiempo del pipeline (S4-A).

No requiere Firebird ni PostgreSQL.
"""

from __future__ import annotations

import logging
import re

import pytest


# ── S4-A: _log_tabla_tiempos ──────────────────────────────────────────────


def test_log_tabla_tiempos_registra_todos_los_pasos(caplog: pytest.LogCaptureFixture) -> None:
    """_log_tabla_tiempos debe emitir una linea de log por cada paso."""
    from main import _log_tabla_tiempos

    tiempos = {
        "PASO 1 — Extraccion":  1.234,
        "PASO 2 — Reporte CxC": 0.456,
        "TOTAL":                1.690,
    }

    with caplog.at_level(logging.INFO, logger="main"):
        _log_tabla_tiempos(tiempos)

    for paso in tiempos:
        assert any(paso in msg for msg in caplog.messages), (
            f"No se registro tiempo de '{paso}' en el log"
        )


def test_log_tabla_tiempos_formato_incluye_segundos(caplog: pytest.LogCaptureFixture) -> None:
    """Cada linea de tiempo debe incluir un numero decimal seguido de 's'."""
    from main import _log_tabla_tiempos

    with caplog.at_level(logging.INFO, logger="main"):
        _log_tabla_tiempos({"PASO 1": 0.123})

    patron_segundos = re.compile(r"\d+\.\d+s")
    assert any(patron_segundos.search(msg) for msg in caplog.messages), (
        "No se encontro formato de segundos (ej. '0.12s') en los mensajes de log"
    )


def test_log_tabla_tiempos_incluye_encabezado(caplog: pytest.LogCaptureFixture) -> None:
    """El log debe incluir un encabezado 'TIEMPOS' visible."""
    from main import _log_tabla_tiempos

    with caplog.at_level(logging.INFO, logger="main"):
        _log_tabla_tiempos({"TOTAL": 5.0})

    assert any("TIEMPOS" in msg for msg in caplog.messages), (
        "No se encontro encabezado 'TIEMPOS' en el log"
    )
