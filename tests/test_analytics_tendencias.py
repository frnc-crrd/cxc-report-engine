"""Pruebas unitarias para la validacion de tendencias mensuales y S3-D."""

import unittest
import pandas as pd
from src.analytics import Analytics

class TestAnalyticsTendencias(unittest.TestCase):
    """
    Validación de cálculos de tendencias de cobranza asegurando el 
    estado correcto de las facturas (COBRADAS vs PENDIENTES).
    """

    def setUp(self):
        self.analytics = Analytics(rangos_antiguedad=[(None, 30, "0-30")])
        self.df_mock = pd.DataFrame({
            "MONEDA": ["MXN", "MXN", "MXN", "USD"],
            "TIPO_IMPTE": ["C", "C", "C", "C"],
            "CONCEPTO": ["VENTA", "VENTA", "VENTA", "VENTA"],
            "FECHA_EMISION": [
                pd.Timestamp("2026-01-15"), 
                pd.Timestamp("2026-01-20"), 
                pd.Timestamp("2026-02-10"),
                pd.Timestamp("2026-01-05")
            ],
            "IMPORTE": [100.0, 200.0, 150.0, 300.0],
            "IMPUESTO": [16.0, 32.0, 24.0, 0.0],
            "SALDO_FACTURA": [0.0, 232.0, 0.0, 300.0]
        })

    def test_tendencia_mensual_agrupacion(self):
        """Verifica la agrupación estructural por año, mes y evaluación condicional."""
        res = self.analytics._tendencia_mensual(self.df_mock, "MXN")
        
        self.assertFalse(res.empty)
        self.assertIn("ANIO", res.columns)
        self.assertIn("MES", res.columns)
        self.assertIn("ESTADO", res.columns)
        
        enero = res[(res["ANIO"] == 2026) & (res["MES"] == 1)]
        self.assertEqual(len(enero), 2)
        
        cobrada_ene = enero[enero["ESTADO"] == "COBRADAS"].iloc[0]
        self.assertEqual(cobrada_ene["NUM_FACTURAS"], 1)
        
        pendiente_ene = enero[enero["ESTADO"] == "PENDIENTES"].iloc[0]
        self.assertEqual(pendiente_ene["NUM_FACTURAS"], 1)
        self.assertEqual(pendiente_ene["SALDO_PENDIENTE"], 232.0)

    def test_tendencia_ignora_abonos(self):
        """Previene la contaminación del conteo aislando sólo naturaleza de cargos."""
        df_abono = pd.DataFrame({
            "MONEDA": ["MXN"], "TIPO_IMPTE": ["R"], "CONCEPTO": ["PAGO"],
            "FECHA_EMISION": [pd.Timestamp("2026-01-15")],
            "IMPORTE": [100.0], "IMPUESTO": [0.0], "SALDO_FACTURA": [0.0]
        })
        res = self.analytics._tendencia_mensual(df_abono, "MXN")
        self.assertTrue(res.empty)

class TestAnalyticsAliases(unittest.TestCase):
    """S3-D: calcular() no debe exponer claves alias sin sufijo _mxn/_usd."""

    _ALIASES = {
        "cartera_vencida_vs_vigente", "antiguedad_cartera",
        "antiguedad_por_cliente", "resumen_por_cliente",
        "resumen_por_vendedor", "tendencia_mensual",
    }

    def test_calcular_no_retorna_aliases_sin_sufijo(self) -> None:
        """calcular() no debe contener claves alias sin sufijo _mxn o _usd."""
        analytics = Analytics(rangos_antiguedad=[(None, 30, "0-30")])
        resultado = analytics.run_analytics({
            "movimientos_totales_cxc": pd.DataFrame(),
            "registros_por_acreditar_cxc": pd.DataFrame(),
            "registros_cancelados_cxc": pd.DataFrame(),
        })
        encontrados = self._ALIASES & set(resultado.keys())
        self.assertFalse(
            encontrados,
            f"calcular() no debe retornar alias sin sufijo. Encontrados: {encontrados}",
        )


if __name__ == "__main__":
    unittest.main()