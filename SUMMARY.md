# bankingApp — Project Summary

## Purpose

Self-hosted household bookkeeping system for a family. Pulls bank transactions via the EU PSD2 / Open Banking API (through the [Enable Banking](https://enablebanking.com) aggregator), categorizes them, and presents consolidation and drill-down tables in a web UI. Runs on a trusted home LAN with no public internet exposure.

**Author:** juleon (j.m.schins@gmail.com) | **License:** Apache-2.0

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic |
| Data | Plain JSON files on local disk (no database) |
| Frontend | React 18, TypeScript, Vite |
| Banking API | PSD2 via Enable Banking (RSA256 JWT auth) |
| Packaging | PyInstaller (standalone `.exe`), Docker, uv |
| Tooling | pandas, httpx, requests, Textual (TUI) |

## Architecture

```
┌──────────────────┐      ┌──────────────────┐
│  psd2-api        │      │  bankingApp-admin │
│  (bank connector)│─────▶│  (bookkeeping UI) │
│  CLI / .exe      │      │  port 8100        │
└────────┬─────────┘      └──────────────────┘
         │ consent                      ▲
         ▼                              │ fetch
┌──────────────────┐                    │
│ bankingApp-server│────────────────────┘
│ (JSON file store)│
│ port 8000        │
└──────────────────┘
```

Three cooperating services:

1. **bankingApp-server** — Minimal JSON file store; receives consent records from family members; serves data to the admin app.
2. **psd2-api** — Bank connector CLI and standalone executables. Handles PSD2 consent flow, transaction fetching, and distillation.
3. **bankingApp-admin** — Bookkeeping application with a React frontend. Refreshes data, categorizes transactions, presents O-table (consolidated), P-table (per-person), and S-table (settings/keywords).

Additionally: **single-docker** (Docker-packaged standalone) and **single-person** (Textual TUI) provide single-user variants.

## Key Features

- **PSD2 bank integration** — Connects to ING (NL), AIB (IE) via Enable Banking API with full consent lifecycle management
- **Transaction distillation** — Raw API responses are compressed into compact records; original files deleted after distillation
- **Keyword-based categorization** — Configurable general + per-person keyword lists with priority rules, compound terms, and wildcard matching
- **Multi-person household** — Consolidated (O-table) and per-person (P-table) views; family members only need to run a small `.exe` to re-authorize
- **Inline editing** — Edit descriptions and categories directly in the UI; modifications persist across refreshes
- **Cross-tab communication** — BroadcastChannel syncs recalculation across browser tabs
- **Multiple deployment options** — NSSM Windows service, Docker Compose, systemd on Linux, or standalone PyInstaller executables

## Directory Structure

```
bankingApp/
├── boekh-server/         # JSON file storage service (FastAPI)
├── boekh-admin/          # Bookkeeping app (FastAPI + React/Vite)
│   └── frontend/         # React UI: O-table, P-table, S-table
├── psd2-api/             # PSD2 bank connector (CLI + packaging)
│   └── packaging/        # Per-person .exe builder + profiles
├── single-person/        # Standalone single-user TUI variant
├── single-docker/        # Docker-packaged single-user web variant
│   └── frontend/         # React UI for single-person mode
├── dist_bog/             # Pre-built exe for "bog"
├── dist_js/              # Pre-built exe for "js"
├── README.md             # Architecture overview
├── INSTALL.md            # Installation guide
└── export_zip.py         # Clean archive builder for machine transfer
```

## Build & Run

```powershell
# Development — run each in a separate terminal
cd boekh-server; uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
cd boekh-admin; uv run uvicorn app.main:app --host 127.0.0.1 --port 8100 --reload
cd boekh-admin/frontend; npm run dev

# Docker
cd boekh-server/deploy; docker compose up -d --build
cd single-docker; docker compose up --build

# PyInstaller executable
cd psd2-api; .\packaging\build_exe.ps1 -ProfileDir .\packaging\profiles\<person>
```
