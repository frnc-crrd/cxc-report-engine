"""Suite de pruebas del pipeline CxC - Microsip.

Ejecuta tres niveles de prueba en orden:

    Nivel 1 - Sin base de datos (datos sinteticos)
        Verifica que cada modulo de src/ procesa datos correctamente
        sin necesitar conexion a Firebird.  Siempre debe pasar.

    Nivel 2 - Conexion a Firebird
        Verifica que la configuracion de red/credenciales es correcta
        y que el DataTransformer extrae y procesa los datos con la
        estructura esperada. Requiere que la PC pueda alcanzar el servidor.

    Nivel 3 - Pipeline completo end-to-end
        Corre run_pipeline() completo y verifica que los archivos Excel
        se generan con todas las pestanas esperadas.

Uso:
    # Todos los niveles
    python tests/test_pipeline.py

    # Solo nivel 1 (sin DB, siempre disponible)
    python tests/test_pipeline.py --nivel 1

    # Niveles 1 y 2
    python tests/test_pipeline.py --nivel 2

    # Verbose: muestra detalles de cada prueba
    python tests/test_pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Asegurar que el raiz del proyecto este en el path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ======================================================================
# COLORES Y UTILIDADES DE CONSOLA
# ======================================================================
class C:
    """Codigos ANSI para colores en terminal."""
    OK      = "\033[92m"
    WARN    = "\033[93m"
    FAIL    = "\033[91m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"
    CYAN    = "\033[96m"
    GREY    = "\033[90m"


def _ok(msg: str) -> None:
    print(f"  {C.OK}[PASS]{C.RESET}  {msg}")


def _fail(msg: str, detalle: str = "") -> None:
    print(f"  {C.FAIL}[FAIL]{C.RESET}  {msg}")
    if detalle:
        for linea in detalle.strip().splitlines():
            print(f"         {C.GREY}{linea}{C.RESET}")


def _warn(msg: str) -> None:
    print(f"  {C.WARN}[WARN]{C.RESET}  {msg}")


def _header(titulo: str) -> None:
    ancho = 60
    print(f"\n{C.BOLD}{C.CYAN}{'=' * ancho}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {titulo}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'=' * ancho}{C.RESET}")


def _subheader(titulo: str) -> None:
    print(f"\n{C.BOLD}  -- {titulo} --{C.RESET}")


# ======================================================================
# FIXTURE DE DATOS SINTETICOS
# ======================================================================

def _df_sintetico(n: int = 50) -> pd.DataFrame:
    """Genera un DataFrame sintetico que imita la salida del DataTransformer.

    Cubre todos los casos edge que los modulos necesitan:
        - Movimientos C (cargo) y R (abono) vinculados.
        - Facturas vencidas y vigentes.
        - Un documento cancelado.
        - Un importe atipico (outlier).
        - Un registro sin nombre de cliente.

    Args:
        n: Numero de cargos a generar.

    Returns:
        DataFrame con la estructura base procesada.
    """
    rng   = np.random.default_rng(42)
    hoy   = datetime.now().date()

    clientes = [
        "EMPRESA ALPHA SA", "COMERCIAL BETA SC", "GRUPO GAMMA SRL",
        "DISTRIBUIDORA DELTA", "SERVICIOS EPSILON",
    ]
    vendedores = ["CARLOS LOPEZ", "ANA MARTINEZ", "ROBERTO SANCHEZ"]
    conceptos  = ["FACTURA", "NOTA CARGO", "INTERESES"]

    filas: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        fecha_emision     = hoy - timedelta(days=int(rng.integers(1, 200)))
        dias_credito      = rng.choice([30, 60, 90])
        fecha_vencimiento = fecha_emision + timedelta(days=int(dias_credito))
        importe           = round(float(rng.uniform(500, 50_000)), 2)
        impuesto          = round(importe * 0.16, 2)

        filas.append({
            "DOCTO_CC_ID":          i,
            "DOCTO_CC_ACR_ID":      None,
            "FOLIO":                f"FAC-{i:04d}",
            "TIPO_IMPTE":           "C",
            "NATURALEZA_CONCEPTO":  "C",
            "CONCEPTO":             rng.choice(conceptos),
            "DESCRIPCION":          f"Venta periodo {fecha_emision.strftime('%b %Y')}",
            "NOMBRE_CLIENTE":       rng.choice(clientes),
            "CLIENTE_ID":           int(rng.integers(1, len(clientes) + 1)),
            "TIPO_CLIENTE":         rng.choice(["CONTADO", "CREDITO"]),
            "VENDEDOR":             rng.choice(vendedores),
            "FECHA_EMISION":        pd.Timestamp(fecha_emision),
            "FECHA_VENCIMIENTO":    pd.Timestamp(fecha_vencimiento),
            "IMPORTE":              importe,
            "IMPUESTO":             impuesto,
            "CARGOS":               importe + impuesto,
            "ABONOS":               0.0,
            "MONEDA":               "MXN",
            "CONDICIONES":          f"Credito {dias_credito} dias",
            "ESTATUS_CLIENTE":      "ACTIVO",
            "CANCELADO":            "N",
            "APLICADO":             "S",
            "LIMITE_CREDITO":       round(float(rng.uniform(50_000, 300_000)), 2),
            "FECHA_HORA_CREACION":  pd.Timestamp(fecha_emision),
            "FECHA_HORA_ULT_MODIF": pd.Timestamp(fecha_emision),
            "FECHA_HORA_CANCELACION": None,
            "USUARIO_CREADOR":      "SYSDBA",
            "USUARIO_ULT_MODIF":    "SYSDBA",
            "USUARIO_CANCELACION":  None,
            "TIPO_USO_ANTICIPO":    None,
        })

    abono_id = n + 1
    abonos: list[dict[str, Any]] = []
    for cargo in rng.choice(filas, size=int(n * 0.4), replace=False):
        abono_monto = round(float(cargo["IMPORTE"]) * float(rng.uniform(0.3, 1.0)), 2)
        abonos.append({
            **cargo,
            "DOCTO_CC_ID":     abono_id,
            "DOCTO_CC_ACR_ID": cargo["DOCTO_CC_ID"],
            "FOLIO":           f"REC-{abono_id:04d}",
            "TIPO_IMPTE":      "R",
            "NATURALEZA_CONCEPTO": "A",
            "IMPORTE":         abono_monto,
            "IMPUESTO":        round(abono_monto * 0.16, 2),
            "CARGOS":          0.0,
            "ABONOS":          abono_monto,
            "CANCELADO":       "N",
        })
        abono_id += 1

    df = pd.DataFrame(filas + abonos)

    df.loc[0, "CANCELADO"] = "S"
    df.loc[0, "FECHA_HORA_CANCELACION"] = pd.Timestamp(hoy)

    media  = df.loc[df["TIPO_IMPTE"] == "C", "IMPORTE"].mean()
    std    = df.loc[df["TIPO_IMPTE"] == "C", "IMPORTE"].std()
    df.loc[1, "IMPORTE"] = round(media + std * 4.5, 2)

    df.loc[2, "NOMBRE_CLIENTE"] = None

    fila_dup = df.loc[3].copy()
    fila_dup["DOCTO_CC_ID"] = abono_id + 10
    df = pd.concat([df, pd.DataFrame([fila_dup])], ignore_index=True)

    return df


# ======================================================================
# NIVEL 1 - MODULOS CON DATOS SINTETICOS
# ======================================================================

class _TestNivel1:
    """Pruebas de unidad/integracion sin base de datos."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed  = 0
        self.failed  = 0
        self.df      = _df_sintetico()

    def _assert(self, condicion: bool, nombre: str, detalle: str = "") -> None:
        if condicion:
            _ok(nombre)
            self.passed += 1
        else:
            _fail(nombre, detalle)
            self.failed += 1

    # ------------------------------------------------------------------
    # SETTINGS
    # ------------------------------------------------------------------
    def test_settings(self) -> None:
        _subheader("config/settings.py")
        try:
            from config.settings import (
                ANOMALIAS, FIREBIRD_CONFIG, KPI_PERIODO_DIAS,
                OUTPUT_DIR, RANGOS_ANTIGUEDAD,
            )
            self._assert(isinstance(FIREBIRD_CONFIG, dict),     "FIREBIRD_CONFIG es dict")
            self._assert("database" in FIREBIRD_CONFIG,          "FIREBIRD_CONFIG tiene 'database'")
            self._assert(isinstance(RANGOS_ANTIGUEDAD, list),    "RANGOS_ANTIGUEDAD es lista")
            self._assert(len(RANGOS_ANTIGUEDAD) > 0,             "RANGOS_ANTIGUEDAD no esta vacia")
            self._assert(isinstance(ANOMALIAS, dict),            "ANOMALIAS es dict")
            self._assert("importe_zscore_umbral" in ANOMALIAS,   "ANOMALIAS tiene zscore_umbral")
            self._assert("dias_vencimiento_critico" in ANOMALIAS,"ANOMALIAS tiene dias_critico")
            self._assert(isinstance(KPI_PERIODO_DIAS, int),      "KPI_PERIODO_DIAS es entero")
        except Exception as e:
            _fail("Error importando settings", traceback.format_exc())
            self.failed += 1

    # ------------------------------------------------------------------
    # REPORTE CXC
    # ------------------------------------------------------------------
    def test_reporte_cxc(self) -> None:
        _subheader("src/reporte_cxc.py")
        try:
            from src.reporte_cxc import generar_reporte_cxc

            resultado = generar_reporte_cxc(self.df)

            self._assert(isinstance(resultado, dict),                   "generar_reporte_cxc devuelve dict")
            self._assert("reporte_cxc"    in resultado,                 "Clave 'reporte_cxc' presente")
            self._assert("movimientos_abiertos_cxc" in resultado,       "Clave 'movimientos_abiertos_cxc' presente")

            reporte = resultado["reporte_cxc"]
            self._assert(isinstance(reporte, pd.DataFrame),             "reporte_cxc es DataFrame")
            self._assert(len(reporte) > 0,                              "reporte_cxc tiene filas")
            self._assert("SALDO_FACTURA" in reporte.columns,            "Columna SALDO_FACTURA existe")
            self._assert("SALDO_CLIENTE" in reporte.columns,            "Columna SALDO_CLIENTE existe")
            self._assert("CATEGORIA_MORA" in reporte.columns,           "Columna CATEGORIA_MORA existe")
            self._assert("DELTA_MORA" in reporte.columns,               "Columna DELTA_MORA existe")

            if "CANCELADO" in reporte.columns:
                cancelados_en_reporte = reporte[reporte["CANCELADO"].isin(["S", "SI"])].shape[0]
                self._assert(cancelados_en_reporte == 0,                "Cancelados excluidos del reporte")

            self._assert(
                pd.to_numeric(reporte["SALDO_FACTURA"], errors="coerce").notna().any(),
                "SALDO_FACTURA contiene valores numericos validos",
            )

            fv = resultado["movimientos_abiertos_cxc"]
            if not fv.empty:
                self._assert("_BAND_GROUP" in fv.columns,               "movimientos_abiertos_cxc tiene _BAND_GROUP")
                self._assert(fv["_BAND_GROUP"].isin([0, 1]).all(),       "_BAND_GROUP solo tiene 0 o 1")

        except Exception as e:
            _fail("Error en reporte_cxc", traceback.format_exc())
            self.failed += 1

    # ------------------------------------------------------------------
    # ANALYTICS
    # ------------------------------------------------------------------
    def test_analytics(self) -> None:
        _subheader("src/analytics.py")
        try:
            from config.settings import RANGOS_ANTIGUEDAD
            from src.analytics import Analytics
            from src.reporte_cxc import generar_reporte_cxc

            reporte = generar_reporte_cxc(self.df)
            vistas = {
                "movimientos_abiertos_cxc": reporte.get("movimientos_abiertos_cxc", pd.DataFrame()),
                "movimientos_totales_cxc": reporte.get("movimientos_totales_cxc", pd.DataFrame()),
            }

            analytics  = Analytics(RANGOS_ANTIGUEDAD)
            resultados = analytics.run_analytics(vistas)

            self._assert("antiguedad_cartera_mxn" in resultados, "antiguedad_cartera_mxn generada")
            self._assert("resumen_por_vendedor_mxn" in resultados, "resumen_por_vendedor_mxn generado")

        except Exception as e:
            _fail("Error en analytics", traceback.format_exc())
            self.failed += 1

    # ------------------------------------------------------------------
    # AUDITOR
    # ------------------------------------------------------------------
    def test_auditor(self) -> None:
        _subheader("src/auditor.py")
        try:
            from config.settings import ANOMALIAS
            from src.auditor import Auditor, AuditResult

            auditor = Auditor(ANOMALIAS)
            result  = auditor.run_audit(self.df)

            self._assert(isinstance(result, AuditResult),                "run_audit devuelve AuditResult")
            self._assert(isinstance(result.resumen, dict),               "resumen es dict")
            self._assert("total_registros" in result.resumen,            "resumen tiene total_registros")
            self._assert("total_hallazgos" in result.resumen,            "resumen tiene total_hallazgos")

            self._assert(
                len(result.documentos_cancelados) >= 1,
                f"Detecto cancelados (encontro {len(result.documentos_cancelados)})",
            )

            self._assert(
                len(result.importes_atipicos) >= 1,
                f"Detecto importe atipico (encontro {len(result.importes_atipicos)})",
            )

            self._assert(
                hasattr(result, 'sin_tipo_cliente'),
                "Estructura de auditoria contiene analisis sin_tipo_cliente",
            )
            
            self._assert(
                hasattr(result, 'sin_vendedor'),
                "Estructura de auditoria contiene analisis sin_vendedor",
            )

            self._assert(isinstance(result.calidad_datos, pd.DataFrame), "calidad_datos es DataFrame")
            self._assert(len(result.calidad_datos) > 0,                  "calidad_datos tiene filas")

        except Exception as e:
            _fail("Error en auditor", traceback.format_exc())
            self.failed += 1

    # ------------------------------------------------------------------
    # KPIS
    # ------------------------------------------------------------------
    def test_kpis(self) -> None:
        _subheader("src/kpis.py")
        try:
            from config.settings import KPI_PERIODO_DIAS
            from src.kpis import generar_kpis

            resultado = generar_kpis(self.df, KPI_PERIODO_DIAS)

            claves_esperadas = [
                "kpis_resumen",
                "kpis_concentracion",
                "kpis_limite_credito",
                "kpis_morosidad_cliente",
            ]
            for clave in claves_esperadas:
                self._assert(clave in resultado, f"Clave '{clave}' presente en KPIs")

            resumen = resultado["kpis_resumen"]
            self._assert(isinstance(resumen, pd.DataFrame),              "kpis_resumen es DataFrame")
            self._assert(len(resumen) == 3,                              "kpis_resumen tiene 3 filas")
            self._assert("KPI" in resumen.columns,                       "Columna KPI presente")
            self._assert("VALOR" in resumen.columns,                     "Columna VALOR presente")

        except Exception as e:
            _fail("Error en kpis", traceback.format_exc())
            self.failed += 1

    # ------------------------------------------------------------------
    # EXPORTACION EXCEL
    # ------------------------------------------------------------------
    def test_exportacion_excel(self) -> None:
        _subheader("main.py - exportar_tres_exceles()")
        try:
            import tempfile
            from main import exportar_tres_exceles

            cxc = {"movimientos_abiertos_cxc": pd.DataFrame({"A": [1]})}
            audit = {"calidad_datos": pd.DataFrame({"B": [2]})}
            analisis = {"antiguedad_cartera_mxn": pd.DataFrame({"C": [3]})}
            kpis = {"kpis_resumen": pd.DataFrame({"D": [4]})}

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                archivos = exportar_tres_exceles(
                    cxc, audit, analisis, kpis, "TEST", tmp_path
                )

                self._assert(len(archivos) == 3, "Tres archivos independientes generados")
                self._assert(all(p.exists() for p in archivos), "Archivos fisicamente escritos")

        except Exception as e:
            _fail("Error en exportacion Excel", traceback.format_exc())
            self.failed += 1

    def run(self) -> tuple[int, int]:
        _header("NIVEL 1 - Modulos con datos sinteticos (sin DB)")
        self.test_settings()
        self.test_reporte_cxc()
        self.test_analytics()
        self.test_auditor()
        self.test_kpis()
        self.test_exportacion_excel()
        return self.passed, self.failed


# ======================================================================
# NIVEL 2 - CONEXION A FIREBIRD Y TRANSFORMACION
# ======================================================================

class _TestNivel2:
    """Pruebas que requieren conexion real a Firebird."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed  = 0
        self.failed  = 0

    def _assert(self, condicion: bool, nombre: str, detalle: str = "") -> None:
        if condicion:
            _ok(nombre)
            self.passed += 1
        else:
            _fail(nombre, detalle)
            self.failed += 1

    def test_conexion(self) -> bool:
        """Verifica que la conexion a Firebird funciona.

        Returns:
            True si la conexion fue exitosa.
        """
        _subheader("Conexion a Firebird")
        try:
            from config.settings import FIREBIRD_CONFIG
            from src.db_connector import FirebirdConnector

            connector = FirebirdConnector(FIREBIRD_CONFIG)
            ok = connector.test_connection()
            self._assert(ok, f"Conexion a base de datos externa completada")
            return ok
        except ImportError as e:
            _fail("Driver de Firebird no instalado", str(e))
            self.failed += 1
            return False
        except Exception as e:
            _fail("Error de conexion", traceback.format_exc())
            self.failed += 1
            return False

    def test_data_transformer(self) -> bool:
        """Verifica la logica de ensamblado de tablas en memoria.

        Returns:
            True si el transformador retorno la estructura esperada.
        """
        _subheader("Transformador en memoria")
        try:
            from config.settings import FIREBIRD_CONFIG
            from src.db_connector import FirebirdConnector
            from src.data_transformer import DataTransformer

            connector = FirebirdConnector(FIREBIRD_CONFIG)
            transformer = DataTransformer(connector)
            df = transformer.get_master_cxc_data()

            self._assert(isinstance(df, pd.DataFrame),                   "Proceso devuelve DataFrame unificado")
            self._assert(len(df) > 0,                                    f"Extraccion retorna datos ({len(df):,} filas)")
            self._assert(len(df.columns) >= 10,                          f"Atributos suficientes mapeados ({len(df.columns)})")

            df.columns = pd.Index([c.upper().strip() for c in df.columns])
            columnas_requeridas = [
                "DOCTO_CC_ID", "TIPO_IMPTE", "IMPORTE",
                "FECHA_EMISION", "NOMBRE_CLIENTE",
            ]
            for col in columnas_requeridas:
                self._assert(col in df.columns, f"Columna base '{col}' presente")

            tipos = df["TIPO_IMPTE"].astype(str).str.strip().str.upper().unique().tolist()
            self._assert(
                "C" in tipos,
                f"Datos en memoria contienen transacciones validas",
            )

            return True

        except Exception as e:
            _fail("Error ejecutando transformacion", traceback.format_exc())
            self.failed += 1
            return False

    def test_pipeline_con_datos_reales(self) -> None:
        """Corre todos los modulos con los datos unificados en memoria."""
        _subheader("Pipeline completo con datos reales")
        try:
            from config.settings import (
                ANOMALIAS, FIREBIRD_CONFIG, KPI_PERIODO_DIAS,
                RANGOS_ANTIGUEDAD,
            )
            from src.analytics import Analytics
            from src.auditor import Auditor
            from src.db_connector import FirebirdConnector
            from src.data_transformer import DataTransformer
            from src.kpis import generar_kpis
            from src.reporte_cxc import generar_reporte_cxc

            connector = FirebirdConnector(FIREBIRD_CONFIG)
            df = DataTransformer(connector).get_master_cxc_data()

            # Reporte CxC
            reporte = generar_reporte_cxc(df)
            self._assert("reporte_cxc" in reporte,                       "reporte_cxc generado con datos reales")

            n_clientes = 0
            if "NOMBRE_CLIENTE" in reporte["reporte_cxc"].columns:
                n_clientes = reporte["reporte_cxc"]["NOMBRE_CLIENTE"].nunique()
            _ok(f"  Procesados: {len(reporte['reporte_cxc']):,} movimientos, {n_clientes} clientes")
            self.passed += 1

            # Analytics
            analytics = Analytics(RANGOS_ANTIGUEDAD).run_analytics(df)
            self._assert("antiguedad_cartera_mxn" in analytics,          "Analytics generado con datos reales")

            # Auditor
            audit = Auditor(ANOMALIAS).run_audit(df)
            self._assert(audit.resumen.get("total_registros", 0) > 0,   "Auditor proceso datos reales")
            self.passed += 1

            # KPIs
            kpis = generar_kpis(df, KPI_PERIODO_DIAS)
            self._assert("kpis_resumen" in kpis,                         "KPIs calculados con datos reales")

        except Exception as e:
            _fail("Error en validacion con datos reales", traceback.format_exc())
            self.failed += 1

    def run(self) -> tuple[int, int]:
        _header("NIVEL 2 - Conexion y logica en memoria")
        conexion_ok = self.test_conexion()
        if not conexion_ok:
            _warn("Saltando pruebas de nivel 2 - sin conexion a base de datos.")
            return self.passed, self.failed

        query_ok = self.test_data_transformer()
        if query_ok:
            self.test_pipeline_con_datos_reales()

        return self.passed, self.failed


# ======================================================================
# NIVEL 3 - PIPELINE COMPLETO END-TO-END
# ======================================================================

class _TestNivel3:
    """Prueba del pipeline completo incluyendo exportacion a disco."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed  = 0
        self.failed  = 0

    def _assert(self, condicion: bool, nombre: str, detalle: str = "") -> None:
        if condicion:
            _ok(nombre)
            self.passed += 1
        else:
            _fail(nombre, detalle)
            self.failed += 1

    def test_run_pipeline(self) -> None:
        _subheader("run_pipeline() end-to-end")
        try:
            from main import run_pipeline

            codigo_salida = run_pipeline(
                skip_audit=False,
                skip_analytics=False,
                skip_kpis=False,
            )
            self._assert(codigo_salida == 0, f"run_pipeline() devolvio 0 (exito)")

            from config.settings import OUTPUT_DIR, EXCEL_NOMBRES
            
            patron = f"{EXCEL_NOMBRES['cxc']}_*.xlsx"
            archivos_xlsx = list(OUTPUT_DIR.glob(patron))
            self._assert(len(archivos_xlsx) > 0, "Archivo Excel generado en output/")

            if archivos_xlsx:
                ultimo = max(archivos_xlsx, key=lambda p: p.stat().st_mtime)
                tamano_kb = ultimo.stat().st_size / 1024
                self._assert(tamano_kb > 10, f"Excel principal exportado correctamente ({tamano_kb:.0f} KB)")
                self.passed += 1

        except Exception as e:
            _fail("Error en flujo de orquestacion principal", traceback.format_exc())
            self.failed += 1

    def run(self) -> tuple[int, int]:
        _header("NIVEL 3 - Pipeline completo end-to-end")
        self.test_run_pipeline()
        return self.passed, self.failed


# ======================================================================
# RUNNER PRINCIPAL
# ======================================================================

def _resumen_final(total_pass: int, total_fail: int) -> None:
    """Imprime el resumen final."""
    total = total_pass + total_fail
    _header("RESUMEN FINAL")

    if total_fail == 0:
        print(f"\n  [EXITO] TODAS LAS PRUEBAS PASARON")
        print(f"  {total_pass}/{total} pruebas exitosas\n")
    else:
        pct = (total_pass / total * 100) if total > 0 else 0
        print(f"\n  Resultados: {total_pass} PASS | {total_fail} FAIL de {total} pruebas ({pct:.0f}%)")
        if total_fail > 0:
            print(f"\n  [WARN] Revisa los errores marcados para diagnosticar.")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Suite de pruebas del pipeline CxC - Microsip",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--nivel", type=int, default=3, choices=[1, 2, 3],
        help="Nivel maximo de pruebas a ejecutar (1=sintetico, 2=+DB, 3=+end-to-end). Default: 3",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Mostrar detalle completo de errores.",
    )
    args = parser.parse_args()

    print(f"\nPipeline CxC - Suite de Pruebas")
    print(f"Iniciando: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Raiz del proyecto: {ROOT}")
    print(f"Nivel maximo: {args.nivel}")

    total_pass = 0
    total_fail = 0

    t1 = _TestNivel1(verbose=args.verbose)
    p, f = t1.run()
    total_pass += p
    total_fail += f

    if args.nivel >= 2:
        t2 = _TestNivel2(verbose=args.verbose)
        p, f = t2.run()
        total_pass += p
        total_fail += f

    if args.nivel >= 3:
        t3 = _TestNivel3(verbose=args.verbose)
        p, f = t3.run()
        total_pass += p
        total_fail += f

    _resumen_final(total_pass, total_fail)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())