"""Tests unitarios para src/auditor.py.

Cubre: AuditResult (estructura y defaults), deteccion de importes atipicos
(Z-score), sin tipo de cliente, sin vendedor, documentos cancelados,
calidad de datos, atipicos en deltas, y el metodo run_audit completo.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.auditor import AuditResult, Auditor

# ------------------------------------------------------------------
# Configuracion de prueba (umbrales pequenos para datos sinteticos)
# ------------------------------------------------------------------

CONFIG_BASE: dict[str, int | float] = {
    "importe_zscore_umbral": 3.0,
    "delta_recaudo_zscore_umbral": 3.0,
    "delta_mora_zscore_umbral": 3.0,
    "dias_vencimiento_critico": 30,
}


# ==================================================================
# AuditResult — estructura y defaults
# ==================================================================


def test_audit_result_defaults_son_dataframes_vacios() -> None:
    resultado = AuditResult()
    for campo in [
        "importes_atipicos",
        "recaudos_atipicos",
        "moras_atipicas",
        "sin_tipo_cliente",
        "sin_vendedor",
        "documentos_cancelados",
        "calidad_datos",
    ]:
        df = getattr(resultado, campo)
        assert isinstance(df, pd.DataFrame), f"{campo} debe ser DataFrame"
        assert df.empty, f"{campo} debe estar vacio por defecto"


def test_audit_result_resumen_es_dict_vacio_por_defecto() -> None:
    resultado = AuditResult()
    assert isinstance(resultado.resumen, dict)
    assert resultado.resumen == {}


def test_audit_result_instancias_independientes() -> None:
    r1 = AuditResult()
    r2 = AuditResult()
    r1.resumen["x"] = 1
    assert "x" not in r2.resumen, "Las instancias deben tener dicts independientes"


# ==================================================================
# _detectar_importes_atipicos
# ==================================================================


def _df_ventas(n_normal: int = 20, importe_outlier: float = 9_000_000.0) -> pd.DataFrame:
    """Crea un DataFrame con ventas normales y un outlier claro."""
    normales = pd.DataFrame({
        "TIPO_IMPTE": ["C"] * n_normal,
        "IMPORTE": [1000.0] * n_normal,
        "NOMBRE_CLIENTE": [f"CLIENTE_{i}" for i in range(n_normal)],
    })
    outlier = pd.DataFrame({
        "TIPO_IMPTE": ["C"],
        "IMPORTE": [importe_outlier],
        "NOMBRE_CLIENTE": ["OUTLIER"],
    })
    return pd.concat([normales, outlier], ignore_index=True)


def test_detectar_importes_atipicos_identifica_outlier() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = _df_ventas()
    resultado = auditor._detectar_importes_atipicos(df)
    assert not resultado.empty
    assert "ZSCORE_IMPORTE" in resultado.columns
    assert "MOTIVO" in resultado.columns
    assert resultado["NOMBRE_CLIENTE"].iloc[0] == "OUTLIER"


def test_detectar_importes_atipicos_no_afecta_cobros() -> None:
    """Solo filas con TIPO_IMPTE=='C' entran al calculo."""
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "TIPO_IMPTE": ["R", "R", "R"],
        "IMPORTE": [1.0, 2.0, 9_000_000.0],
    })
    resultado = auditor._detectar_importes_atipicos(df)
    assert resultado.empty


def test_detectar_importes_atipicos_sin_columnas_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"NOMBRE": ["A"]})
    assert auditor._detectar_importes_atipicos(df).empty


def test_detectar_importes_atipicos_menos_de_3_filas_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"TIPO_IMPTE": ["C", "C"], "IMPORTE": [100.0, 200.0]})
    assert auditor._detectar_importes_atipicos(df).empty


def test_detectar_importes_atipicos_std_cero_devuelve_vacio() -> None:
    """Cuando todos los importes son iguales std==0, no hay atipicos."""
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "TIPO_IMPTE": ["C"] * 10,
        "IMPORTE": [500.0] * 10,
    })
    assert auditor._detectar_importes_atipicos(df).empty


def test_detectar_importes_atipicos_umbral_configurable() -> None:
    """Un umbral bajo detecta mas atipicos que el default de 3.0."""
    auditor_estricto = Auditor({**CONFIG_BASE, "importe_zscore_umbral": 1.0})
    df = _df_ventas()
    resultado = auditor_estricto._detectar_importes_atipicos(df)
    assert len(resultado) >= 1


# ==================================================================
# _detectar_sin_tipo_cliente
# ==================================================================


def test_detectar_sin_tipo_cliente_identifica_nulos() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "TIPO_CLIENTE": ["CONTADO", None, "CREDITO", None],
        "NOMBRE_CLIENTE": ["A", "B", "C", "D"],
    })
    resultado = auditor._detectar_sin_tipo_cliente(df)
    assert len(resultado) == 2
    assert "MOTIVO" in resultado.columns
    assert set(resultado["NOMBRE_CLIENTE"]) == {"B", "D"}


def test_detectar_sin_tipo_cliente_sin_columna_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"IMPORTE": [100.0]})
    assert auditor._detectar_sin_tipo_cliente(df).empty


def test_detectar_sin_tipo_cliente_todos_asignados_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"TIPO_CLIENTE": ["CONTADO", "CREDITO"]})
    assert auditor._detectar_sin_tipo_cliente(df).empty


# ==================================================================
# _detectar_sin_vendedor
# ==================================================================


def test_detectar_sin_vendedor_identifica_nulos() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "VENDEDOR": ["JUAN", None, "PEDRO"],
        "FOLIO": [1, 2, 3],
    })
    resultado = auditor._detectar_sin_vendedor(df)
    assert len(resultado) == 1
    assert resultado["FOLIO"].iloc[0] == 2
    assert "MOTIVO" in resultado.columns


def test_detectar_sin_vendedor_sin_columna_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"IMPORTE": [1.0]})
    assert auditor._detectar_sin_vendedor(df).empty


# ==================================================================
# _analizar_cancelados
# ==================================================================


def test_analizar_cancelados_detecta_valor_S() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"CANCELADO": ["S", "N", "S", None]})
    resultado = auditor._analizar_cancelados(df)
    assert len(resultado) == 2
    assert "MOTIVO" in resultado.columns


def test_analizar_cancelados_valores_canonicos() -> None:
    """Todos los valores de CANCELADO_VALUES deben ser detectados."""
    auditor = Auditor(CONFIG_BASE)
    valores = ["S", "SI", "s", "si", 1, True, "1"]
    df = pd.DataFrame({"CANCELADO": valores + ["N", "NO", 0, False]})
    resultado = auditor._analizar_cancelados(df)
    assert len(resultado) == len(valores)


def test_analizar_cancelados_calcula_dias_cuando_hay_fechas() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "CANCELADO": ["S"],
        "FECHA_HORA_CREACION": [pd.Timestamp("2025-01-01")],
        "FECHA_HORA_CANCELACION": [pd.Timestamp("2025-01-11")],
    })
    resultado = auditor._analizar_cancelados(df)
    assert "DIAS_HASTA_CANCELACION" in resultado.columns
    assert resultado["DIAS_HASTA_CANCELACION"].iloc[0] == 10


def test_analizar_cancelados_sin_fechas_no_tiene_columna_dias() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"CANCELADO": ["S", "SI"]})
    resultado = auditor._analizar_cancelados(df)
    assert "DIAS_HASTA_CANCELACION" not in resultado.columns


def test_analizar_cancelados_sin_columna_cancelado_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"IMPORTE": [100.0]})
    assert auditor._analizar_cancelados(df).empty


# ==================================================================
# _evaluar_calidad_datos
# ==================================================================


def test_evaluar_calidad_datos_estructura() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({
        "COL_A": [1.0, None, 3.0],
        "COL_B": ["X", "Y", "Z"],
    })
    reporte = auditor._evaluar_calidad_datos(df)
    assert set(reporte.columns) >= {
        "COLUMNA", "TIPO_DATO", "TOTAL_REGISTROS", "NULOS", "PCT_NULOS", "VALORES_UNICOS"
    }
    assert len(reporte) == 2


def test_evaluar_calidad_datos_pct_nulos_correcto() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"COL": [1.0, None, None, None]})
    reporte = auditor._evaluar_calidad_datos(df)
    fila = reporte[reporte["COLUMNA"] == "COL"].iloc[0]
    assert fila["NULOS"] == 3
    assert fila["PCT_NULOS"] == 75.0


def test_evaluar_calidad_datos_sin_nulos() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"COL": [1, 2, 3, 4]})
    reporte = auditor._evaluar_calidad_datos(df)
    assert reporte["NULOS"].iloc[0] == 0
    assert reporte["PCT_NULOS"].iloc[0] == 0.0


# ==================================================================
# _detectar_atipicos_delta (DELTA_MORA / DELTA_RECAUDO)
# ==================================================================


def _df_reporte_con_delta(columna: str) -> pd.DataFrame:
    """DataFrame con 20 valores normales y 1 outlier en la columna delta."""
    normales = [5.0] * 20
    return pd.DataFrame({
        "TIPO_IMPTE": ["C"] * 21,
        columna: normales + [9999.0],
        "NOMBRE_CLIENTE": [f"C{i}" for i in range(21)],
    })


def test_detectar_atipicos_delta_mora() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = _df_reporte_con_delta("DELTA_MORA")
    resultado = auditor._detectar_atipicos_delta(df, "DELTA_MORA", "delta_mora_zscore_umbral")
    assert not resultado.empty
    assert "ZSCORE_DELTA_MORA" in resultado.columns
    assert "MOTIVO" in resultado.columns


def test_detectar_atipicos_delta_recaudo() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = _df_reporte_con_delta("DELTA_RECAUDO")
    resultado = auditor._detectar_atipicos_delta(df, "DELTA_RECAUDO", "delta_recaudo_zscore_umbral")
    assert not resultado.empty
    assert "ZSCORE_DELTA_RECAUDO" in resultado.columns


def test_detectar_atipicos_delta_columna_faltante_devuelve_vacio() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = pd.DataFrame({"TIPO_IMPTE": ["C"], "IMPORTE": [100.0]})
    assert auditor._detectar_atipicos_delta(df, "DELTA_MORA", "delta_mora_zscore_umbral").empty


def test_detectar_atipicos_delta_elimina_band_group() -> None:
    """_BAND_GROUP no debe aparecer en el resultado."""
    auditor = Auditor(CONFIG_BASE)
    df = _df_reporte_con_delta("DELTA_MORA")
    df["_BAND_GROUP"] = 1
    resultado = auditor._detectar_atipicos_delta(df, "DELTA_MORA", "delta_mora_zscore_umbral")
    assert "_BAND_GROUP" not in resultado.columns


# ==================================================================
# run_audit — integracion
# ==================================================================


def _df_maestro_completo() -> pd.DataFrame:
    """DataFrame maestro sintetico con todas las columnas relevantes."""
    n = 15
    return pd.DataFrame({
        "TIPO_IMPTE": ["C"] * n,
        "IMPORTE": [1000.0] * (n - 1) + [9_000_000.0],
        "TIPO_CLIENTE": ["CREDITO"] * (n - 2) + [None, None],
        "VENDEDOR": ["JUAN"] * (n - 1) + [None],
        "CANCELADO": ["N"] * (n - 1) + ["S"],
        "NOMBRE_CLIENTE": [f"CLI_{i}" for i in range(n)],
    })


def test_run_audit_devuelve_audit_result() -> None:
    auditor = Auditor(CONFIG_BASE)
    resultado = auditor.run_audit(_df_maestro_completo())
    assert isinstance(resultado, AuditResult)


def test_run_audit_resumen_tiene_claves_esperadas() -> None:
    auditor = Auditor(CONFIG_BASE)
    resultado = auditor.run_audit(_df_maestro_completo())
    claves_esperadas = {
        "fecha_auditoria",
        "total_registros",
        "importes_atipicos",
        "recaudos_atipicos",
        "moras_atipicas",
        "sin_tipo_cliente",
        "sin_vendedor",
        "cancelados",
        "total_hallazgos",
    }
    assert claves_esperadas <= set(resultado.resumen.keys())


def test_run_audit_total_hallazgos_es_suma_parciales() -> None:
    auditor = Auditor(CONFIG_BASE)
    resultado = auditor.run_audit(_df_maestro_completo())
    r = resultado.resumen
    esperado = (
        r["importes_atipicos"]
        + r["recaudos_atipicos"]
        + r["moras_atipicas"]
        + r["sin_tipo_cliente"]
        + r["sin_vendedor"]
        + r["cancelados"]
    )
    assert r["total_hallazgos"] == esperado


def test_run_audit_sin_df_reporte_deltas_vacios() -> None:
    auditor = Auditor(CONFIG_BASE)
    resultado = auditor.run_audit(_df_maestro_completo(), df_reporte=None)
    assert resultado.recaudos_atipicos.empty
    assert resultado.moras_atipicas.empty


def test_run_audit_con_df_reporte_rellena_deltas() -> None:
    auditor = Auditor(CONFIG_BASE)
    df_reporte = _df_reporte_con_delta("DELTA_MORA")
    df_reporte["DELTA_RECAUDO"] = [3.0] * 20 + [8888.0]
    resultado = auditor.run_audit(_df_maestro_completo(), df_reporte=df_reporte)
    assert not resultado.moras_atipicas.empty
    assert not resultado.recaudos_atipicos.empty


def test_run_audit_df_reporte_vacio_no_rellena_deltas() -> None:
    auditor = Auditor(CONFIG_BASE)
    resultado = auditor.run_audit(
        _df_maestro_completo(),
        df_reporte=pd.DataFrame(),
    )
    assert resultado.recaudos_atipicos.empty
    assert resultado.moras_atipicas.empty


def test_run_audit_calidad_datos_tiene_fila_por_columna() -> None:
    auditor = Auditor(CONFIG_BASE)
    df = _df_maestro_completo()
    resultado = auditor.run_audit(df)
    assert len(resultado.calidad_datos) == len(df.columns)
