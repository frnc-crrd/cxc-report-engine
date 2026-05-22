"""CLI para gestionar asignaciones cobrador <-> cliente.

Uso (con el venv activo):
    python -m src.cobrador_cli pendientes
    python -m src.cobrador_cli listar
    python -m src.cobrador_cli asignar "NOMBRE CLIENTE" "COBRADOR"
    python -m src.cobrador_cli cobradores
    python -m src.cobrador_cli importar data/private/asignaciones.csv

El archivo CSV debe guardarse en data/private/ (excluido de Git por .gitignore)
para evitar la exposicion de datos PII (nombres de clientes y cobradores).

Formato del CSV para importar:
    NOMBRE_CLIENTE,COBRADOR
    EMPRESA ABC,Haidee
    EMPRESA XYZ,Jovanna
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

from config.settings import COBRADOR_DB_URL
from src.cobrador_manager import CobradorManager


def _get_mgr() -> CobradorManager:
    try:
        return CobradorManager(COBRADOR_DB_URL)
    except Exception as exc:
        print(f"Error: no se pudo conectar a la DB de cobradores.\n{exc}", file=sys.stderr)
        print("Verifica que el contenedor este activo: mise run db-up", file=sys.stderr)
        sys.exit(1)


def cmd_pendientes(mgr: CobradorManager) -> None:
    """Lista clientes sin cobrador asignado (PENDIENTE)."""
    assignments = mgr.get_assignments()
    pendientes = sorted(k for k, v in assignments.items() if v == "PENDIENTE")
    if not pendientes:
        print("No hay clientes sin asignar.")
        return
    print(f"\nClientes PENDIENTE ({len(pendientes)}):")
    for nombre in pendientes:
        print(f"  {nombre}")


def cmd_listar(mgr: CobradorManager) -> None:
    """Lista todas las asignaciones ordenadas por cobrador."""
    assignments = mgr.get_assignments()
    if not assignments:
        print("No hay clientes en la DB. Ejecuta el pipeline primero: py main.py")
        return
    por_cobrador: dict[str, list[str]] = {}
    for cliente, cobrador in assignments.items():
        por_cobrador.setdefault(cobrador, []).append(cliente)
    total = 0
    for cobrador in sorted(por_cobrador):
        clientes = sorted(por_cobrador[cobrador])
        print(f"\n{cobrador} ({len(clientes)}):")
        for c in clientes:
            print(f"  {c}")
        total += len(clientes)
    print(f"\nTotal: {total} clientes")


def cmd_asignar(mgr: CobradorManager, nombre_cliente: str, cobrador: str) -> None:
    """Asigna un cliente a un cobrador."""
    nombre_upper = nombre_cliente.strip().upper()
    if not mgr.update_cobrador(nombre_upper, cobrador.strip()):
        print(f"Cliente '{nombre_upper}' no encontrado en la DB.")
        print("Tip: ejecuta el pipeline primero para sincronizar clientes ('py main.py').")
        sys.exit(1)
    print(f"Asignado: '{nombre_upper}' -> '{cobrador.strip()}'")


def cmd_eliminar(mgr: CobradorManager, nombre_cliente: str) -> None:
    """Elimina un cliente de la DB local (solo para corregir datos incorrectos).

    El cliente volvera a aparecer en el proximo pipeline si sigue existiendo
    en Firebird. Para eliminar permanentemente, darlo de baja en Microsip primero.
    """
    nombre_upper = nombre_cliente.strip().upper()
    if not mgr.delete_cliente(nombre_upper):
        print(f"Cliente '{nombre_upper}' no encontrado en la DB.")
        sys.exit(1)
    print(f"Eliminado: '{nombre_upper}'")


def cmd_cobradores(mgr: CobradorManager) -> None:
    """Lista los cobradores con su conteo de clientes."""
    assignments = mgr.get_assignments()
    conteo: dict[str, int] = {}
    for cobrador in assignments.values():
        conteo[cobrador] = conteo.get(cobrador, 0) + 1
    if not conteo:
        print("No hay datos. Ejecuta el pipeline primero.")
        return
    print(f"\n{'COBRADOR':<30} {'CLIENTES':>8}")
    print("-" * 40)
    for cobrador in sorted(conteo):
        print(f"{cobrador:<30} {conteo[cobrador]:>8}")


def _detectar_encoding(path: Path) -> str:
    """Detecta encoding del CSV. Revisa BOM primero; si no hay BOM, prueba UTF-8
    y cae a CP1252 (Windows-1252, estandar de Microsip/Excel en espanol mexicano)."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def cmd_exportar(mgr: CobradorManager, archivo: str) -> None:
    """Exporta todas las asignaciones a un CSV (columnas: NOMBRE_CLIENTE, COBRADOR).

    El archivo resultante puede abrirse en Excel, editarse y reimportarse con
    el comando ``importar`` para actualizar las asignaciones en bloque.
    Si el archivo destino ya existe, se crea un respaldo con extension .bak
    antes de sobreescribirlo.
    """
    assignments = mgr.get_assignments()
    if not assignments:
        print("No hay datos en la DB. Ejecuta el pipeline primero: py main.py")
        return
    path = Path(archivo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        print(f"Respaldo creado: {bak}")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["NOMBRE_CLIENTE", "COBRADOR"])
        for nombre, cobrador in sorted(assignments.items()):
            writer.writerow([nombre, cobrador])
    print(f"Exportado: {len(assignments)} asignaciones → {archivo}")


def _buscar_nombre_fallback(nombre_csv: str, assignments: dict[str, str]) -> str | None:
    """Busca en assignments el nombre canonico cuando el CSV tiene '?' como placeholder
    de un caracter no-ASCII (ej. SALDA?A -> SALDAÑA). Devuelve el nombre de la DB
    o None si no hay coincidencia unica."""
    if "?" not in nombre_csv:
        return None
    patron = re.escape(nombre_csv).replace(r"\?", r"[^\x00-\x7F]")
    matches = [k for k in assignments if re.fullmatch(patron, k)]
    return matches[0] if len(matches) == 1 else None


def cmd_importar(mgr: CobradorManager, archivo: str) -> None:
    """Importa asignaciones desde un CSV (columnas: NOMBRE_CLIENTE, COBRADOR).

    Acepta archivos guardados desde Excel (UTF-16 con BOM), UTF-8 con BOM
    o UTF-8 sin BOM. El separador puede ser coma o punto y coma.
    """
    path = Path(archivo)
    if not path.exists():
        print(f"Archivo no encontrado: {archivo}", file=sys.stderr)
        sys.exit(1)

    encoding = _detectar_encoding(path)

    # Detectar delimitador probando la primera linea
    with open(path, newline="", encoding=encoding) as f:
        primera = f.readline()
    delimitador = ";" if primera.count(";") > primera.count(",") else ","

    errores = 0
    filas_validas: list[dict[str, str]] = []
    with open(path, newline="", encoding=encoding) as f:
        reader = csv.DictReader(f, delimiter=delimitador)
        # Normalizar nombres de columnas (strip de espacios y BOM residual)
        if reader.fieldnames:
            reader.fieldnames = [c.strip().lstrip("﻿") for c in reader.fieldnames]
        if not reader.fieldnames or "NOMBRE_CLIENTE" not in reader.fieldnames or "COBRADOR" not in reader.fieldnames:
            print("El CSV debe tener columnas NOMBRE_CLIENTE y COBRADOR.", file=sys.stderr)
            print(f"  Columnas detectadas: {reader.fieldnames}", file=sys.stderr)
            sys.exit(1)
        assignments = mgr.get_assignments()
        for row in reader:
            nombre = row["NOMBRE_CLIENTE"].strip().upper()
            cobrador = row["COBRADOR"].strip()
            if not nombre or not cobrador:
                continue
            if nombre not in assignments:
                nombre_real = _buscar_nombre_fallback(nombre, assignments)
                if nombre_real is not None:
                    print(f"  OK (via fallback): {nombre} -> {nombre_real} -> {cobrador}")
                    filas_validas.append({"nombre_cliente": nombre_real, "cobrador": cobrador})
                else:
                    print(f"  SKIP (no encontrado en DB): {nombre}")
                    errores += 1
            else:
                print(f"  OK: {nombre} -> {cobrador}")
                filas_validas.append({"nombre_cliente": nombre, "cobrador": cobrador})

    resultado = mgr.bulk_update(filas_validas)
    print(f"\nImportacion completada: {resultado['actualizados']} actualizados, {errores} omitidos.")


def main() -> None:
    """Punto de entrada de la CLI de gestion de cobradores.

    Procesa los subcomandos ``pendientes``, ``listar``, ``cobradores``,
    ``asignar``, ``exportar``, ``importar`` y ``eliminar`` para administrar
    el catalogo de asignaciones cliente -> cobrador en PostgreSQL.
    """
    parser = argparse.ArgumentParser(description="Gestiona asignaciones cobrador <-> cliente")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pendientes", help="Lista clientes sin cobrador asignado")
    sub.add_parser("listar", help="Lista todas las asignaciones por cobrador")
    sub.add_parser("cobradores", help="Resumen de cobradores y cantidad de clientes")

    p_asignar = sub.add_parser("asignar", help="Asigna un cliente a un cobrador")
    p_asignar.add_argument("cliente", help="Nombre del cliente (exacto, se convierte a mayusculas)")
    p_asignar.add_argument("cobrador", help="Nombre del cobrador")

    p_exportar = sub.add_parser("exportar", help="Exporta asignaciones a CSV para edicion masiva")
    p_exportar.add_argument("archivo", help="Ruta de destino del archivo CSV")

    p_importar = sub.add_parser("importar", help="Importa asignaciones desde CSV")
    p_importar.add_argument("archivo", help="Ruta al archivo CSV")

    p_eliminar = sub.add_parser("eliminar", help="Elimina un cliente de la DB local")
    p_eliminar.add_argument("cliente", help="Nombre exacto del cliente (se convierte a mayusculas)")

    args = parser.parse_args()
    mgr = _get_mgr()
    try:
        if args.cmd == "pendientes":
            cmd_pendientes(mgr)
        elif args.cmd == "listar":
            cmd_listar(mgr)
        elif args.cmd == "cobradores":
            cmd_cobradores(mgr)
        elif args.cmd == "asignar":
            cmd_asignar(mgr, args.cliente, args.cobrador)
        elif args.cmd == "exportar":
            cmd_exportar(mgr, args.archivo)
        elif args.cmd == "importar":
            cmd_importar(mgr, args.archivo)
        elif args.cmd == "eliminar":
            cmd_eliminar(mgr, args.cliente)
    finally:
        mgr.dispose()


if __name__ == "__main__":
    main()
