"""Tests S7-A: validacion de configuracion con Pydantic.

Verifica FirebirdConfig y CobradorConfig:
- campos requeridos fallan con ValidationError claro
- validators de vacios/formato
- coercion de tipos (port str->int)
- model_dump() retorna la estructura que espera FirebirdConnector

Sin Firebird ni PostgreSQL requeridos.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ── FirebirdConfig ────────────────────────────────────────────────────────────

def test_firebird_config_falla_sin_database() -> None:
    """FirebirdConfig debe rechazar la ausencia de 'database'."""
    from config.schema import FirebirdConfig

    with pytest.raises(ValidationError) as exc_info:
        FirebirdConfig(password="secreto")
    assert "database" in str(exc_info.value).lower()


def test_firebird_config_falla_sin_password() -> None:
    """FirebirdConfig debe rechazar la ausencia de 'password'."""
    from config.schema import FirebirdConfig

    with pytest.raises(ValidationError) as exc_info:
        FirebirdConfig(database="/ruta/db.fdb")
    assert "password" in str(exc_info.value).lower()


def test_firebird_config_falla_password_vacio() -> None:
    """FirebirdConfig debe rechazar password como cadena vacia."""
    from config.schema import FirebirdConfig

    with pytest.raises(ValidationError) as exc_info:
        FirebirdConfig(database="/ruta/db.fdb", password="")
    assert "password" in str(exc_info.value).lower()


def test_firebird_config_falla_database_vacia() -> None:
    """FirebirdConfig debe rechazar database como cadena vacia."""
    from config.schema import FirebirdConfig

    with pytest.raises(ValidationError) as exc_info:
        FirebirdConfig(database="", password="secreto")
    assert "database" in str(exc_info.value).lower()


def test_firebird_config_acepta_config_minima() -> None:
    """FirebirdConfig debe aceptar solo database y password (campos opcionales = None)."""
    from config.schema import FirebirdConfig

    config = FirebirdConfig(database="/ruta/db.fdb", password="secreto")
    assert config.database == "/ruta/db.fdb"
    assert config.password == "secreto"
    assert config.host is None
    assert config.port is None


def test_firebird_config_acepta_config_completa() -> None:
    """FirebirdConfig acepta todos los campos incluyendo opcionales."""
    from config.schema import FirebirdConfig

    config = FirebirdConfig(
        host="192.168.1.10",
        port=3050,
        database="/ruta/db.fdb",
        user="SYSDBA",
        password="masterkey",
        charset="UTF8",
    )
    assert config.host == "192.168.1.10"
    assert config.port == 3050
    assert config.user == "SYSDBA"
    assert config.charset == "UTF8"


def test_firebird_config_coerce_port_str_a_int() -> None:
    """FirebirdConfig debe convertir port de str a int automaticamente."""
    from config.schema import FirebirdConfig

    config = FirebirdConfig(database="/ruta/db.fdb", password="secreto", port="3050")
    assert config.port == 3050
    assert isinstance(config.port, int)


def test_firebird_config_model_dump_tiene_claves_del_conector() -> None:
    """model_dump() debe retornar exactamente las claves que usa FirebirdConnector."""
    from config.schema import FirebirdConfig

    config = FirebirdConfig(database="/ruta/db.fdb", password="secreto")
    d = config.model_dump()
    assert set(d.keys()) == {"host", "port", "database", "user", "password", "charset"}


# ── CobradorConfig ────────────────────────────────────────────────────────────

def test_cobrador_config_falla_url_no_postgresql() -> None:
    """CobradorConfig debe rechazar una URL que no use el esquema postgresql://."""
    from config.schema import CobradorConfig

    with pytest.raises(ValidationError) as exc_info:
        CobradorConfig(url="mysql://usuario:pwd@localhost/cobrador")
    assert "postgresql" in str(exc_info.value).lower()


def test_cobrador_config_acepta_url_postgresql() -> None:
    """CobradorConfig acepta una URL postgresql:// valida."""
    from config.schema import CobradorConfig

    config = CobradorConfig(url="postgresql://cxc_admin:pwd@localhost:5432/cobrador")
    assert config.url.startswith("postgresql://")


def test_cobrador_config_falla_sin_url() -> None:
    """CobradorConfig debe fallar si no se proporciona url."""
    from config.schema import CobradorConfig

    with pytest.raises(ValidationError) as exc_info:
        CobradorConfig()
    assert "url" in str(exc_info.value).lower()
