"""Modelos de validacion de configuracion con Pydantic.

Define los contratos de datos para las dos conexiones externas del pipeline:
Firebird (fuente de datos) y PostgreSQL (asignaciones cobrador).

Uso en config/settings.py:
    config = FirebirdConfig(host=..., database=..., password=..., ...)
    FIREBIRD_CONFIG = config.model_dump()
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class FirebirdConfig(BaseModel):
    """Configuracion de conexion a la base de datos Firebird.

    Campos requeridos: database, password.
    Todos los demas son opcionales (None si no se proporcionan).

    Attributes:
        host:     IP o nombre del servidor Firebird.
        port:     Puerto TCP (tipicamente 3050). Se coerce de str a int.
        database: Ruta completa al archivo .fdb en el servidor. Requerido.
        user:     Usuario de la base de datos.
        password: Contrasena de la base de datos. Requerido, no vacio.
        charset:  Juego de caracteres (p. ej. UTF8, WIN1252).
    """

    host:     str | None = None
    port:     int | None = None
    database: str        = Field(..., min_length=1)
    user:     str | None = None
    password: str        = Field(..., min_length=1)
    charset:  str | None = None

    @field_validator("database", "password", mode="before")
    @classmethod
    def _no_vacio(cls, v: object) -> object:
        """Rechaza cadenas que sean None o solo espacios en blanco."""
        if isinstance(v, str) and not v.strip():
            raise ValueError("no puede estar vacio ni contener solo espacios")
        return v

    @field_validator("port", mode="before")
    @classmethod
    def _port_a_int(cls, v: object) -> int | None:
        """Coerce port de str a int si viene como variable de entorno."""
        if v is None or v == "":
            return None
        if isinstance(v, int):
            return v
        return int(str(v))


class CobradorConfig(BaseModel):
    """Configuracion de conexion a PostgreSQL para asignaciones cobrador.

    Attributes:
        url: URL de conexion completa. Debe empezar con postgresql://.
    """

    url: str = Field(..., min_length=1)

    @field_validator("url", mode="before")
    @classmethod
    def _url_postgresql(cls, v: object) -> object:
        """Valida que la URL use el esquema postgresql://."""
        if isinstance(v, str) and not v.startswith("postgresql://"):
            raise ValueError(
                f"la URL de cobrador debe empezar con 'postgresql://', se recibio: '{v}'"
            )
        return v
