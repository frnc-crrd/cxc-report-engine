"""Tests S8-A: cobertura de tipos completa con mypy --strict.

Verifica que src/, config/ y main.py pasen mypy sin errores.
Sin Firebird ni PostgreSQL requeridos.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_mypy_source_sin_errores() -> None:
    """mypy debe pasar sin errores en src/, config/ y main.py."""
    result = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "mypy"), "src/", "config/", "main.py"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"mypy encontro {result.stdout.count('error:')} error(es):\n{result.stdout}"
    )
