# Francisco Carriedo

**Data & Analytics Engineer · Actuary**

I build production data pipelines that turn messy enterprise data into decisions. My background is actuarial science, so I approach data the way an actuary approaches risk: with rigor, validation, and an obsession for getting the numbers right. I care less about chasing buzzwords and more about shipping systems that are tested, typed, and reliable.

## What I actually do

- **Data engineering with discipline.** Python pipelines over ERP systems (Microsip / Firebird), with strict typing (`mypy --strict`), comprehensive test suites, and idempotent runs. If it isn't tested, I don't trust it in production.
- **Analytics that reach the business.** I model data, build Power BI dashboards, and automate regulatory and financial reporting — from CNBV banking reports at Capgemini to accounts-receivable analytics today.
- **Risk-aware by training.** My actuarial foundation (statistics, probability, risk modeling) shapes how I validate data: every transformation gets checked against a source of truth.
- **Infrastructure I run myself.** Fedora daily driver, self-hosted hardened Linux servers (Fail2Ban, WireGuard), Docker/Podman containers. I like understanding the stack from the database up to the dashboard.

## Featured project

### [cxc-report-engine](https://github.com/frnc-crrd/cxc-report-engine) — Accounts-Receivable audit & analytics pipeline

A production pipeline that extracts receivables from the Microsip ERP (Firebird), computes balances, detects anomalies, and distributes formatted reports — then publishes to Power BI via Parquet.

- **286 passing tests** (pytest) and **strict static typing** (`mypy --strict`, pydantic schemas)
- **Idempotent by design** — retries never produce duplicates; Monday's run doesn't depend on Friday's state
- **9-step modular architecture**: Firebird extraction → transformation → balances → anomaly detection (z-score) → aging/ABC analysis → financial KPIs (DSO, CEI, delinquency) → styled Excel → per-collector PDFs → Parquet export
- **Production-grade touches**: exponential retries (tenacity), structured JSON logging, secrets kept out of code, scheduled execution via systemd timers, email distribution through Microsoft Graph API

*Stack: Python 3.12 · pandas · Firebird · PostgreSQL · pydantic · openpyxl · uv · mise · Docker/Podman*

## Tech I work with

| Domain | Tools |
| :--- | :--- |
| **Languages** | Python (advanced), SQL, Bash, TypeScript, C# (.NET) |
| **Data & BI** | pandas, Power BI (DAX, Gateway), Parquet, ETL/ELT (Ab Initio), MicroStrategy |
| **Databases** | SQL Server, PostgreSQL, Firebird, DynamoDB |
| **AI applied** | RAG architectures, LLM integration (Azure OpenAI, Gemini), document intelligence, AI-assisted dev (Claude Code) |
| **Infra & DevOps** | Docker/Podman, Linux (hardening), GitHub Actions (CI/CD), CodeQL, AWS (Lambda, Textract), Azure |
| **Quality** | pytest, mypy strict, Ruff, type-driven design |

## Background

Licensed actuary (UANL) with 3+ years across banking, insurance, and consumer-goods data: regulatory reporting at Capgemini (Banamex, Citibanamex), a 1st-place finish at the Capgemini & Microsoft Hackathon 2023 as RAG-pipeline architect, and end-to-end analytics today.

---

📍 Mexico · [LinkedIn](https://www.linkedin.com/in/carriedo) · [Email](mailto:fco.carriedo@outlook.com)
