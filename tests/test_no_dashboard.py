"""Tests S6-A: verifica la eliminacion completa del dashboard Streamlit.

Sin Firebird ni PostgreSQL requeridos.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_deps() -> list[str]:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["dependencies"]


def test_streamlit_eliminado_de_pyproject() -> None:
    """streamlit no debe aparecer en las dependencias del proyecto."""
    deps = _pyproject_deps()
    presentes = [d for d in deps if d.lower().startswith("streamlit")]
    assert not presentes, f"streamlit sigue en pyproject.toml: {presentes}"


def test_plotly_eliminado_de_pyproject() -> None:
    """plotly no debe aparecer en las dependencias del proyecto."""
    deps = _pyproject_deps()
    presentes = [d for d in deps if d.lower().startswith("plotly")]
    assert not presentes, f"plotly sigue en pyproject.toml: {presentes}"


def test_directorio_dashboard_eliminado() -> None:
    """El directorio dashboard/ no debe existir en el proyecto."""
    assert not (ROOT / "dashboard").exists(), (
        "dashboard/ sigue existiendo; debe eliminarse por completo"
    )


def test_directorio_streamlit_eliminado() -> None:
    """.streamlit/config.toml no debe existir en el proyecto."""
    assert not (ROOT / ".streamlit").exists(), (
        ".streamlit/ sigue existiendo; debe eliminarse por completo"
    )


def test_tarea_dashboard_eliminada_de_mise() -> None:
    """mise.toml no debe contener la tarea 'dashboard'."""
    contenido = (ROOT / "mise.toml").read_text(encoding="utf-8")
    assert "[tasks.dashboard]" not in contenido, (
        "La tarea [tasks.dashboard] sigue en mise.toml"
    )
