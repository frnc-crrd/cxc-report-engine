"""Tests para src/logging_config.py.

Cubre: JsonFormatter (estructura, campos obligatorios, timestamp ISO,
interpolacion de args, excepcion incluida), configurar_logging en modo
texto y JSON, y el flag --log-json en parse_args().
"""

from __future__ import annotations

import json
import logging

import pytest

from src.logging_config import JsonFormatter, configurar_logging
from main import parse_args


# ==================================================================
# JsonFormatter — estructura del registro
# ==================================================================


def _make_record(
    msg: str = "mensaje de prueba",
    level: int = logging.INFO,
    name: str = "modulo.test",
    args: tuple[object, ...] = (),
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )
    return record


def test_json_formatter_retorna_json_valido() -> None:
    fmt = JsonFormatter()
    salida = fmt.format(_make_record())
    parsed = json.loads(salida)
    assert isinstance(parsed, dict)


def test_json_formatter_campos_obligatorios() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record()))
    for campo in ("timestamp", "level", "logger", "msg"):
        assert campo in parsed, f"Falta campo '{campo}'"


def test_json_formatter_level_correcto() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record(level=logging.WARNING)))
    assert parsed["level"] == "WARNING"


def test_json_formatter_logger_correcto() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record(name="src.auditor")))
    assert parsed["logger"] == "src.auditor"


def test_json_formatter_msg_correcto() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record(msg="hola mundo")))
    assert parsed["msg"] == "hola mundo"


def test_json_formatter_interpola_args() -> None:
    fmt = JsonFormatter()
    record = _make_record(msg="valor: %d", args=(42,))
    parsed = json.loads(fmt.format(record))
    assert parsed["msg"] == "valor: 42"


def test_json_formatter_timestamp_es_iso() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record()))
    ts = parsed["timestamp"]
    assert "T" in ts, f"El timestamp '{ts}' no parece ISO-8601"
    assert ts.endswith("Z"), f"El timestamp '{ts}' debe terminar en Z (UTC)"


def test_json_formatter_una_linea_por_registro() -> None:
    fmt = JsonFormatter()
    salida = fmt.format(_make_record())
    assert "\n" not in salida, "El JSON no debe contener saltos de linea internos"


def test_json_formatter_excepcion_incluida() -> None:
    fmt = JsonFormatter()
    try:
        raise ValueError("error de prueba")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="fallo", args=(), exc_info=sys.exc_info(),
        )
    parsed = json.loads(fmt.format(record))
    assert "exc" in parsed
    assert "ValueError" in parsed["exc"]


def test_json_formatter_sin_excepcion_no_tiene_campo_exc() -> None:
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record()))
    assert "exc" not in parsed


def test_json_formatter_caracteres_no_ascii() -> None:
    """El JSON debe manejar acentos y caracteres especiales sin escapar."""
    fmt = JsonFormatter()
    parsed = json.loads(fmt.format(_make_record(msg="facturación México")))
    assert "facturación" in parsed["msg"]


# ==================================================================
# configurar_logging — modo texto
# ==================================================================


def test_configurar_logging_texto_instala_handler(caplog: pytest.LogCaptureFixture) -> None:
    root = logging.getLogger()
    handlers_antes = len(root.handlers)
    configurar_logging(json_mode=False)
    assert len(root.handlers) >= 1
    # Restaurar estado
    root.handlers = root.handlers[:handlers_antes] if handlers_antes else []


def test_configurar_logging_texto_no_usa_json_formatter() -> None:
    configurar_logging(json_mode=False)
    root = logging.getLogger()
    for handler in root.handlers:
        assert not isinstance(handler.formatter, JsonFormatter)
    root.handlers.clear()


# ==================================================================
# configurar_logging — modo JSON
# ==================================================================


def test_configurar_logging_json_instala_json_formatter() -> None:
    configurar_logging(json_mode=True)
    root = logging.getLogger()
    formateadores = [h.formatter for h in root.handlers]
    assert any(isinstance(f, JsonFormatter) for f in formateadores)
    root.handlers.clear()


def test_configurar_logging_json_produce_json_valido(capsys: pytest.CaptureFixture[str]) -> None:
    configurar_logging(json_mode=True)
    logger = logging.getLogger("test.json")
    logger.info("mensaje de test json")
    capturado = capsys.readouterr()
    for linea in capturado.err.splitlines():
        if "mensaje de test json" in linea:
            json.loads(linea)
            break
    else:
        pytest.fail("No se encontro la linea esperada en stderr")
    logging.getLogger().handlers.clear()


def test_configurar_logging_no_duplica_handlers() -> None:
    logging.getLogger().handlers.clear()
    configurar_logging(json_mode=True)
    configurar_logging(json_mode=True)
    assert len(logging.getLogger().handlers) == 1
    logging.getLogger().handlers.clear()


# ==================================================================
# parse_args — flag --log-json
# ==================================================================


def test_parse_args_log_json_default_false() -> None:
    args = parse_args([])
    assert args.log_json is False


def test_parse_args_log_json_activa_modo_json() -> None:
    args = parse_args(["--log-json"])
    assert args.log_json is True


def test_parse_args_log_json_compatible_con_otros_flags() -> None:
    args = parse_args(["--log-json", "--dry-run", "--skip-audit"])
    assert args.log_json is True
    assert args.dry_run is True
    assert args.skip_audit is True
