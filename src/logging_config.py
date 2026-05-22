"""Configuracion de logging para el pipeline CxC.

Proporciona dos modos de salida:
    texto: formato legible en terminal (default).
    JSON:  una linea JSON por registro, apta para ingestor en Elastic/Grafana/Loki.

Uso:
    from src.logging_config import configurar_logging
    configurar_logging(json_mode=True)   # produccion
    configurar_logging(json_mode=False)  # desarrollo (default)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Formateador que emite un objeto JSON compacto por linea de log.

    Campos emitidos:
        timestamp: Fecha y hora en UTC formato ISO-8601 con sufijo Z.
        level:     Nombre del nivel (INFO, WARNING, ERROR, etc.).
        logger:    Nombre del logger (tipicamente __name__ del modulo).
        msg:       Mensaje ya interpolado con sus argumentos.
        exc:       Traza de excepcion si el record la incluye (opcional).
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serializa un LogRecord como una linea JSON en UTF-8.

        Args:
            record: Registro emitido por el sistema de logging estandar.

        Returns:
            Cadena JSON con los campos timestamp, level, logger, msg y exc.
        """
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        entrada: dict[str, str] = {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "level":     record.levelname,
            "logger":    record.name,
            "msg":       record.getMessage(),
        }
        if record.exc_info:
            entrada["exc"] = self.formatException(record.exc_info)
        return json.dumps(entrada, ensure_ascii=False)


_FORMATO_TEXTO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_FECHA_TEXTO   = "%Y-%m-%d %H:%M:%S"


def configurar_logging(
    json_mode: bool = False,
    level: int = logging.INFO,
) -> None:
    """Configura el handler raiz de logging para todo el pipeline.

    Reemplaza cualquier handler existente en el logger raiz para evitar
    duplicados en re-invocaciones (pruebas unitarias, importaciones repetidas).

    Args:
        json_mode: Si True instala JsonFormatter; si False usa formato texto plano.
        level:     Nivel minimo de log (default: INFO).
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_mode
        else logging.Formatter(_FORMATO_TEXTO, datefmt=_FECHA_TEXTO)
    )
    root.addHandler(handler)
