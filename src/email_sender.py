"""Envio de reportes por cobrador via Microsoft Graph API (OAuth2/MSAL).

Lee las rutas de envio desde config/email_routes.toml y envia un correo
por cada bloque [[rutas]] con los archivos que correspondan segun cobrador
y tipo de archivo configurados.

Autenticacion:
    Primera ejecucion: flujo interactivo device-code (el usuario abre una URL
    e ingresa un codigo). El token se guarda cifrado en MSAL_TOKEN_CACHE.
    Ejecuciones posteriores: silenciosas (refresh automatico del token).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msal
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_GRAPH_SEND_MAIL = "https://graph.microsoft.com/v1.0/me/sendMail"
_SCOPES = ["https://graph.microsoft.com/Mail.Send"]


# ── Modelo de ruta ─────────────────────────────────────────────────────────


@dataclass
class EmailRuta:
    """Regla de ruteo de archivos por correo electronico.

    Attributes:
        destinatarios: Lista de direcciones de correo que reciben los archivos.
        cobradores: Nombres de cobrador (sin distinguir mayusculas/minusculas)
            cuyos archivos se adjuntan al correo.
        tipos: Extensiones a incluir como adjuntos. Default: pdf y xlsx.
    """

    destinatarios: list[str]
    cobradores: list[str]
    tipos: list[str] = field(default_factory=lambda: ["pdf", "xlsx"])


# ── Carga de configuracion ─────────────────────────────────────────────────


def cargar_rutas(config_path: Path) -> list[EmailRuta]:
    """Lee email_routes.toml y devuelve la lista de rutas configuradas.

    Args:
        config_path: Ruta al archivo .toml de configuracion de rutas.

    Returns:
        Lista de EmailRuta. Vacia si el archivo no existe o no tiene rutas.
    """
    if not config_path.exists():
        logger.debug("No existe %s — envio de email omitido.", config_path)
        return []

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    rutas: list[EmailRuta] = []
    for bloque in data.get("rutas", []):
        destinatarios = [str(d) for d in bloque.get("destinatarios", [])]
        cobradores = [str(c) for c in bloque.get("cobradores", [])]
        tipos_raw = bloque.get("tipos", ["pdf", "xlsx"])
        tipos = [str(t).lower() for t in tipos_raw]
        if not destinatarios or not cobradores:
            logger.warning("Bloque de ruta sin destinatarios o cobradores — ignorado.")
            continue
        rutas.append(EmailRuta(destinatarios=destinatarios, cobradores=cobradores, tipos=tipos))

    return rutas


# ── Autenticacion OAuth2 (MSAL) ────────────────────────────────────────────


def _cargar_cache(cache_path: Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text(encoding="utf-8"))
    return cache


def _guardar_cache(cache: msal.SerializableTokenCache, cache_path: Path) -> None:
    if cache.has_state_changed:
        cache_path.write_text(cache.serialize(), encoding="utf-8")


def _get_access_token(
    client_id: str,
    tenant_id: str,
    cache_path: Path,
) -> str:
    """Obtiene un access token de Microsoft Graph via MSAL.

    Intenta primero adquisicion silenciosa desde cache. Si no hay cache
    o el token expiro, lanza el flujo device-code interactivo.

    Args:
        client_id:  Application (client) ID registrado en Azure.
        tenant_id:  'consumers' para cuentas personales, o el tenant ID de la org.
        cache_path: Ruta donde se serializa el cache de tokens.

    Returns:
        Access token valido para Microsoft Graph.

    Raises:
        RuntimeError: Si la autenticacion falla.
    """
    cache = _cargar_cache(cache_path)

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    # Intento silencioso
    cuentas = app.get_accounts()
    result: dict[str, Any] | None = None
    if cuentas:
        result = app.acquire_token_silent(_SCOPES, account=cuentas[0])

    # Flujo interactivo si es necesario
    if not result or "access_token" not in result:
        logger.info("Autenticacion de email requerida (device code flow).")
        flow = app.initiate_device_flow(scopes=_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(
                f"Error al iniciar device flow: {flow.get('error_description', flow)}"
            )
        # Mostrar instrucciones al usuario en consola
        print(f"\n  {flow['message']}\n")
        result = app.acquire_token_by_device_flow(flow)

    _guardar_cache(cache, cache_path)

    if not result or "access_token" not in result:
        error = result.get("error_description", str(result)) if result else "sin resultado"
        raise RuntimeError(f"No se obtuvo access token: {error}")

    return str(result["access_token"])


# ── Filtrado de archivos ───────────────────────────────────────────────────


def _slug(nombre: str) -> str:
    s = nombre.strip().replace(" ", "_")
    return re.sub(r"[^\w]", "", s, flags=re.ASCII).upper()


def _filtrar(archivos: list[Path], cobradores: list[str], tipos: list[str]) -> list[Path]:
    """Devuelve los archivos que coincidan con al menos un cobrador y tipo."""
    slugs = {_slug(c) for c in cobradores}
    exts = {f".{t.lstrip('.')}" for t in tipos}
    return [
        p for p in archivos
        if p.suffix.lower() in exts and any(slug in p.name.upper() for slug in slugs)
    ]


# ── Envio via Microsoft Graph API ─────────────────────────────────────────


def _reintentable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, RuntimeError):
        return any(code in str(exc) for code in ("429", "502", "503", "504"))
    return False


def _adjunto_graph(path: Path) -> dict[str, str]:
    contenido = base64.b64encode(path.read_bytes()).decode("ascii")
    extension = path.suffix.lower().lstrip(".")
    mime = "application/pdf" if extension == "pdf" else (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if extension == "xlsx" else "application/octet-stream"
    )
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": path.name,
        "contentType": mime,
        "contentBytes": contenido,
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=30, max=120),
    retry=retry_if_exception(_reintentable),
    reraise=True,
)
def _enviar_via_graph(
    access_token: str,
    remitente: str,
    destinatarios: list[str],
    cobradores: list[str],
    adjuntos: list[Path],
) -> None:
    from datetime import date as _date
    fecha = _date.today().strftime("%d/%m/%Y")
    nombres_cob = ", ".join(cobradores)

    lista = "\n".join(f"  - {p.name}" for p in adjuntos)
    cuerpo_texto = (
        f"Se adjuntan los reportes de cobranza para {nombres_cob} generados el {fecha}.\n\n"
        f"Archivos adjuntos ({len(adjuntos)}):\n{lista}\n\n"
        "Generado automaticamente por CxC Audit Engine."
    )

    payload: dict[str, Any] = {
        "message": {
            "subject": f"Reporte de cobranza — {nombres_cob} — {fecha}",
            "body": {"contentType": "Text", "content": cuerpo_texto},
            "toRecipients": [
                {"emailAddress": {"address": d}} for d in destinatarios
            ],
            "attachments": [_adjunto_graph(p) for p in adjuntos],
        },
        "saveToSentItems": False,
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        _GRAPH_SEND_MAIL,
        headers=headers,
        data=json.dumps(payload),
        timeout=60,
    )
    if resp.status_code not in (200, 202):
        raise RuntimeError(
            f"Graph API error {resp.status_code}: {resp.text[:300]}"
        )


# ── Punto de entrada principal ─────────────────────────────────────────────


def enviar_rutas(
    archivos: list[Path],
    rutas: list[EmailRuta],
    smtp_user: str,
    azure_client_id: str,
    azure_tenant_id: str,
    token_cache_path: Path,
) -> int:
    """Procesa todas las rutas y envia un correo por cada una via Graph API.

    Args:
        archivos:          Lista de rutas de archivos generados por el pipeline.
        rutas:             Reglas de envio cargadas desde email_routes.toml.
        smtp_user:         Cuenta de correo remitente (debe coincidir con el token).
        azure_client_id:   Application (client) ID del app registrado en Azure.
        azure_tenant_id:   Tenant de Azure ('consumers' para cuentas personales).
        token_cache_path:  Ruta al archivo de cache de tokens MSAL.

    Returns:
        Numero de correos enviados con exito.
    """
    if not azure_client_id:
        logger.debug("AZURE_CLIENT_ID no configurado — envio de email omitido.")
        return 0

    if not rutas:
        logger.debug("Sin rutas de email configuradas — envio omitido.")
        return 0

    try:
        access_token = _get_access_token(azure_client_id, azure_tenant_id, token_cache_path)
    except RuntimeError as exc:
        logger.error("No se pudo obtener token de Microsoft Graph: %s", exc)
        return 0

    enviados = 0
    for ruta in rutas:
        adjuntos = _filtrar(archivos, ruta.cobradores, ruta.tipos)
        if not adjuntos:
            logger.warning(
                "Sin archivos para cobradores %s (tipos=%s) — ruta omitida.",
                ruta.cobradores, ruta.tipos,
            )
            continue
        try:
            _enviar_via_graph(
                access_token=access_token,
                remitente=smtp_user,
                destinatarios=ruta.destinatarios,
                cobradores=ruta.cobradores,
                adjuntos=adjuntos,
            )
            logger.info(
                "Email enviado → %s | cobradores: %s | %d archivos.",
                ruta.destinatarios, ruta.cobradores, len(adjuntos),
            )
            enviados += 1
        except RuntimeError as exc:
            logger.error(
                "Error al enviar a %s (cobradores=%s): %s",
                ruta.destinatarios, ruta.cobradores, exc,
            )
        except requests.RequestException as exc:
            logger.error(
                "Error de red al enviar a %s (cobradores=%s): %s",
                ruta.destinatarios, ruta.cobradores, exc,
            )

    return enviados
