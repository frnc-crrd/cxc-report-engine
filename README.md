# CxC Report Engine

Pipeline de auditoria y analitica de **Cuentas por Cobrar** sobre el ERP
**Microsip**. Extrae la cartera desde Firebird, calcula saldos, detecta
anomalias, genera reportes en Excel/PDF, distribuye los reportes por
cobrador y publica los datos a Power BI via Parquet.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Type Checked](https://img.shields.io/badge/type%20checked-mypy%20strict-success.svg)](https://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/tests-286%20passing-brightgreen.svg)](#tests)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

---

## Tabla de contenidos

- [Resumen](#resumen)
- [Arquitectura](#arquitectura)
- [Caracteristicas](#caracteristicas)
- [Requisitos](#requisitos)
- [Instalacion](#instalacion)
- [Configuracion](#configuracion)
- [Uso](#uso)
- [Ejecucion automatica](#ejecucion-automatica)
- [Tests](#tests)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Licencia](#licencia)

---

## Resumen

El motor recorre la base de datos de Microsip y produce, en una sola
ejecucion, los siguientes artefactos:

| Salida | Formato | Destinatario |
|---|---|---|
| Reporte general de CxC | XLSX | Direccion / contabilidad |
| Analisis de cartera | XLSX | Direccion / contabilidad |
| Auditoria de anomalias | XLSX | Auditoria interna |
| Reporte individual por cobrador | XLSX + PDF | Cada cobrador |
| Dataset Parquet | Parquet | Power BI |
| Envio automatico de correos | Microsoft Graph API | Configurable por ruta |

Todo el procesamiento es **idempotente**: la ejecucion del lunes no
depende del estado dejado el viernes, y los reintentos automaticos no
producen duplicados.

---

## Arquitectura

El pipeline esta organizado como una cadena secuencial de nueve pasos:

```
                      Firebird (Microsip)
                              |
       1. db_connector        v   conexion con reintentos (tenacity)
       2. data_transformer    -   10 extracciones + JOIN en pandas
       3. reporte_cxc         -   saldos por factura, 6 vistas
       4. auditor             -   anomalias por z-score
       5. analytics           -   aging, pivots, tendencias, ABC
       6. kpis                -   DSO, CEI, morosidad, Pareto
       7. excel_formatter     -   tres Excel con estilos
       8. cobrador_manager    -   sync PostgreSQL (asignaciones)
          reporte_cobrador    -   un Excel + PDF por cobrador
       9. parquet_exporter    -   dataset Power BI
          email_sender        -   envio via Microsoft Graph (OAuth2)
```

Cada paso esta encapsulado en su propio modulo, con tipado estricto
(`mypy --strict`) y cobertura de pruebas.

### Decisiones de diseno

- **Joins en pandas, no en SQL.** Firebird devuelve tablas individuales
  y `data_transformer.py` orquesta los `LEFT JOIN`. Esto reduce la carga
  del servidor de produccion y simplifica el versionado de la logica.
- **`CLIENTE_ID` nunca aparece en los reportes.** Se usa solo para
  sincronizar PostgreSQL; los archivos entregables muestran `NOMBRE_CLIENTE`.
- **Asignaciones persistentes.** El upsert con
  `ON CONFLICT (cliente_id) DO UPDATE SET nombre_cliente = EXCLUDED.nombre_cliente`
  refresca el nombre pero **nunca** sobrescribe la asignacion de cobrador.
- **Doble moneda en paralelo.** Analytics y KPIs procesan MXN y USD por
  separado en todas las vistas.
- **Auto-restore desde CSV.** Las asignaciones se exportan e importan
  como CSV (CP1252) para sobrevivir a reinicios del contenedor.

---

## Caracteristicas

- Pipeline completo en una sola invocacion (`python main.py`).
- Reintentos exponenciales con `tenacity` en la conexion a Firebird y en
  el envio de correos.
- Logs estructurados en JSON (`--log-json`) para Elastic / Grafana / Loki.
- Soporte de ejecucion programada con `systemd timer` + `rtcwake` para
  encender la maquina, correr el pipeline y apagarla automaticamente.
- Salida estilizada con `rich` en consola (modo interactivo) y JSON
  plano en modo desatendido.
- Excel con celdas protegidas, formato de moneda, bandas por grupo
  factura+abonos y password configurable.
- PDF apaisado (A4) por cobrador, con semaforo de mora y totales
  multi-moneda.
- Envio de correos via **Microsoft Graph API** con cache de token MSAL.
- CLI auxiliar (`src.cobrador_cli`) para gestionar asignaciones sin
  tocar la base directamente.

---

## Requisitos

- Python 3.12 o superior.
- `uv` (gestor de dependencias y entornos).
- `mise` (gestor de tareas y versiones; opcional pero recomendado).
- Contenedor de Podman o Docker para PostgreSQL.
- Acceso a una base Firebird de Microsip.
- Tenant de Microsoft Entra ID (para el envio de correos por Graph API).

---

## Instalacion

```bash
git clone https://github.com/frnc-crrd/cxc_report_engine.git
cd cxc_report_engine

# Sincroniza dependencias en .venv/
uv sync

# Levanta el contenedor PostgreSQL para las asignaciones
mise run db-up
```

---

## Configuracion

### Variables de entorno (`.env`)

Las credenciales nunca se almacenan en el codigo. Crea un archivo
`.env` en la raiz del proyecto con el siguiente contenido:

```env
# Firebird (Microsip)
FIREBIRD_HOST=10.0.0.10
FIREBIRD_PORT=3050
FIREBIRD_DATABASE=/var/lib/firebird/3.0/data/microsip.fdb
FIREBIRD_USER=SYSDBA
FIREBIRD_PASSWORD=tu-password
FIREBIRD_CHARSET=ISO8859_1

# PostgreSQL (contenedor local)
COBRADOR_DB_PASSWORD=otro-password
COBRADOR_DB_NAME=cobrador
COBRADOR_DB_USER=cxc_admin
COBRADOR_DB_PORT=5432

# Excel (proteccion de hoja)
EXCEL_SHEET_PASSWORD=password-hoja

# Microsoft Graph API (envio de correos)
AZURE_CLIENT_ID=tu-client-id
AZURE_TENANT_ID=consumers
SMTP_USER=tu-correo@dominio.com
```

### Rutas de correo (`config/email_routes.toml`)

El archivo **no se versiona** (contiene PII). Crea tu copia local a
partir de `config/email_routes.toml.example`.

---

## Uso

### Ejecucion manual

```bash
# Pipeline completo
mise run run                       # equivalente a: python main.py

# Solo verificar conexion a Firebird
python main.py --test-connection

# Saltar pasos puntuales
python main.py --skip-audit --skip-analytics --skip-kpis

# Sin escribir archivos (dry run)
python main.py --dry-run

# Solo un cobrador en concreto
python main.py --solo-cobrador "JUAN BRIONES"

# Sin envio de correos
python main.py --skip-email

# Logs en JSON
python main.py --log-json
```

### Gestion de asignaciones (CLI)

```bash
mise run cobrador-pendientes       # clientes sin cobrador
mise run cobrador-listar           # asignaciones por cobrador
mise run cobrador-cobradores       # resumen por cobrador

python -m src.cobrador_cli asignar "CLIENTE EJEMPLO SA" "JUAN BRIONES"
python -m src.cobrador_cli exportar data/private/asignaciones.csv
python -m src.cobrador_cli importar data/private/asignaciones.csv
```

---

## Ejecucion automatica

El proyecto incluye un timer de `systemd` y un script de envoltura que:

1. **Enciende** la computadora a la hora configurada (via `rtcwake` en el
   BIOS/UEFI).
2. **Ejecuta** el pipeline con hasta tres reintentos espaciados 30 minutos.
3. **Programa** el siguiente encendido y apaga el equipo.

```bash
# Instalar el timer
sudo cp scripts/systemd/cxc-pipeline.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cxc-pipeline.timer

# Permitir rtcwake sin password
echo "tu-usuario ALL=(ALL) NOPASSWD: /usr/sbin/rtcwake" | sudo tee /etc/sudoers.d/rtcwake
```

> El BIOS/UEFI debe tener habilitada la opcion **"Wake on RTC Alarm"** o
> equivalente. Verifica con:
> `sudo rtcwake -m off -t $(date -d "+1 minute" +%s)`

---

## Tests

286 pruebas unitarias e integradas con `pytest`.

```bash
mise run test            # suite completa (PostgreSQL opcional, auto-skip)
mise run test-unit       # solo unit tests, sin dependencias externas
mise run test-pipeline   # pipeline nivel 1 con datos sinteticos
mise run typecheck       # mypy --strict
```

Cobertura por modulo:

| Modulo | Pruebas |
|---|---|
| `excel_formatter` | 20 |
| `reporte_cobrador` | 9 |
| `config/schema` | 11 |
| `db_connector` | 7 |
| `auditor` | 33 |
| `kpis` | 25 |
| `analytics` | 32 + 2 |
| `parquet_exporter` | 14 |
| `cobrador_manager` | 19 |
| `cobrador_cli` | 22 |
| Otros | 92 |

Los tests que requieren PostgreSQL se omiten automaticamente si el
contenedor no esta activo. Usa `TEST_COBRADOR_DB_URL` para apuntar a
una base de pruebas independiente.

---

## Estructura del proyecto

```
cxc_report_engine/
|-- config/
|   |-- schema.py              # Validacion pydantic (FirebirdConfig, CobradorConfig)
|   |-- settings.py            # Lectura de .env, rangos de aging, umbrales
|   `-- email_routes.toml.example
|-- db/
|   `-- init.sql               # DDL de PostgreSQL (idempotente)
|-- scripts/
|   |-- pipeline_diario.sh     # Wrapper con reintentos + rtcwake
|   `-- systemd/
|       |-- cxc-pipeline.service
|       `-- cxc-pipeline.timer
|-- src/
|   |-- db_connector.py        # Firebird (driver auto-detection)
|   |-- data_transformer.py    # 10 extracciones + JOINs
|   |-- reporte_cxc.py         # Saldos y 6 vistas
|   |-- auditor.py             # Anomalias por z-score
|   |-- analytics.py           # Aging, pivots, ABC
|   |-- kpis.py                # DSO, CEI, morosidad
|   |-- excel_formatter.py     # Estilos openpyxl
|   |-- pdf_cobrador.py        # PDF apaisado por cobrador
|   |-- cobrador_manager.py    # PostgreSQL (asignaciones)
|   |-- cobrador_cli.py        # CLI de gestion
|   |-- reporte_cobrador.py    # Split por cobrador
|   |-- parquet_exporter.py    # Dataset Power BI
|   |-- email_sender.py        # Microsoft Graph API + MSAL
|   |-- logging_config.py      # Formato texto y JSON
|   `-- utils.py
|-- tests/                     # 286 pruebas
|-- main.py                    # Orquestador
|-- compose.yml                # PostgreSQL via Podman/Docker
|-- mise.toml                  # Tareas (mise run <tarea>)
|-- pyproject.toml             # Dependencias y mypy strict
`-- uv.lock
```

---

## Convenciones

- **Idioma de docstrings y comentarios:** espanol (coincide con la
  terminologia del negocio).
- **Columnas y dataframes:** terminologia Microsip
  (`SALDO_FACTURA`, `DELTA_MORA`, `TIPO_MOVIMIENTO`, etc.).
- **Tipado:** `mypy --strict` obligatorio en `src/`, `config/` y `main.py`.
- **Sin emojis** en codigo, comentarios ni archivos versionados.
- **Comparaciones de saldos:** tolerancia `1e-6` para precision Firebird.
- **Tipo de movimiento `'T'`:** indica reembolso e invierte el signo en
  `CARGOS`/`ABONOS`.

---

## Licencia

Distribuido bajo licencia MIT. Consulta el archivo [`LICENSE`](LICENSE)
para los terminos completos.
