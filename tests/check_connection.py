"""Diagnostico rapido de conexion a Firebird.

Ejecuta antes de correr el pipeline para verificar que la configuracion
de entorno y la red hacia el servidor operan correctamente.
No requiere datos reales ni modulos de transformacion.

Uso:
    python tests/check_connection.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ok(msg: str) -> None:
    """Imprime un mensaje de exito."""
    print(f"  [OK]    {msg}")


def _fail(msg: str) -> None:
    """Imprime un mensaje de fallo."""
    print(f"  [ERROR] {msg}")


def _warn(msg: str) -> None:
    """Imprime un mensaje de advertencia."""
    print(f"  [WARN]  {msg}")


def _info(msg: str) -> None:
    """Imprime un mensaje informativo."""
    print(f"  [INFO]  {msg}")


def main() -> int:
    """Ejecuta las validaciones de conexion y dependencias.

    Returns:
        int: Numero de errores encontrados durante el diagnostico.
    """
    print("\n" + "=" * 60)
    print("  Diagnostico de Conexion - Pipeline CxC Microsip")
    print("=" * 60)

    errores = 0

    # 1. Configuracion base
    print("\n[1] Verificando configuracion (settings.py)...")
    try:
        from config.settings import FIREBIRD_CONFIG, OUTPUT_DIR
        _ok(f"host:     {FIREBIRD_CONFIG.get('host', '?')}")
        _ok(f"port:     {FIREBIRD_CONFIG.get('port', 3050)}")
        _ok(f"database: {FIREBIRD_CONFIG.get('database', '?')}")
        _ok(f"user:     {FIREBIRD_CONFIG.get('user', '?')}")
        _ok(f"charset:  {FIREBIRD_CONFIG.get('charset', 'WIN1252')}")
        
        if not OUTPUT_DIR.exists():
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            _ok(f"Directorio de salida creado: {OUTPUT_DIR.name}")
            
    except Exception as e:
        _fail(f"Error importando settings: {e}")
        errores += 1
        return errores

    # 2. Archivo de base de datos
    print("\n[2] Verificando archivo .fdb en disco (si es local)...")
    db_path_str = str(FIREBIRD_CONFIG.get("database", ""))
    db_path = Path(db_path_str)
    
    if FIREBIRD_CONFIG.get("host") in ("localhost", "127.0.0.1") and db_path.exists():
        tamano_mb = db_path.stat().st_size / (1024 * 1024)
        _ok(f"Archivo encontrado: {db_path.name} ({tamano_mb:.1f} MB)")
    else:
        _info(f"Ruta configurada: {db_path_str}")
        _info("Validacion omitida por ser conexion remota o ruta no accesible directamente.")

    # 3. Driver de Firebird
    print("\n[3] Verificando driver de Firebird...")
    driver_encontrado = False
    try:
        import fdb
        _ok(f"fdb instalado (Firebird 2.5) - version: {fdb.__version__}")
        driver_encontrado = True
    except ImportError:
        _warn("fdb no instalado")

    if not driver_encontrado:
        try:
            import firebird.driver
            _ok("firebird-driver instalado (Firebird 3+/4+)")
            driver_encontrado = True
        except ImportError:
            _warn("firebird-driver no instalado")

    if not driver_encontrado:
        _fail("No se encontro ningun driver de Firebird")
        _info("Para Microsip (Firebird 2.5): pip install fdb")
        _info("Para Firebird 3+/4+:          pip install firebird-driver")
        errores += 1

    # 4. Dependencias Python
    print("\n[4] Verificando dependencias Python...")
    deps = {
        "pandas": "pandas",
        "numpy": "numpy",
        "openpyxl": "openpyxl",
        "dotenv": "dotenv",
    }
    for nombre, modulo in deps.items():
        try:
            mod = __import__(modulo)
            version = getattr(mod, "__version__", "?")
            _ok(f"{nombre} - version {version}")
        except ImportError:
            _warn(f"{nombre} no instalado - pip install {nombre}")

    # 5. Prueba de conexion real
    if driver_encontrado:
        print("\n[5] Probando conexion real a Firebird...")
        try:
            from src.db_connector import FirebirdConnector
            connector = FirebirdConnector(FIREBIRD_CONFIG)
            ok = connector.test_connection()
            if ok:
                _ok("Conexion exitosa a la base de datos.")
            else:
                _fail("Conexion fallida - revisa credenciales y que el servidor este activo")
                errores += 1
        except Exception as e:
            _fail(f"Error de conexion: {e}")
            _info("Verifica que el servicio de Firebird este corriendo y las credenciales en .env")
            errores += 1
    else:
        print("\n[5] Saltando prueba de conexion (driver no disponible)")

    # Resumen
    print("\n" + "-" * 60)
    if errores == 0:
        print("  [EXITO] Todo listo - puedes ejecutar: python main.py")
    else:
        print(f"  [ERROR] {errores} problema(s) encontrado(s) - revisa los puntos marcados arriba")
    print("-" * 60 + "\n")

    return errores


if __name__ == "__main__":
    sys.exit(main())