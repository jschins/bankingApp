# bankingApp-admin

Web-based bookkeeping app. A **Python / FastAPI back-end** fetches bank
transactions, distills them into compact per-person records, categorises them
against keyword lists, and serves consolidation/drill-down tables to a **React +
TypeScript front-end**.

## How data gets here

`POST /api/refresh` (the **Refresh data** button) runs the whole pipeline:

1. It invokes `psd2-api`'s `collect` as a subprocess. For each person with valid
   consent on `bankingApp-server`, `collect` fetches their transactions from the bank
   and writes the raw files **directly into this app's local storage** under
   `LOCAL_STORAGE_DIR` (default `./storage`), as `storage/<person>/transactions_*.json`
   (+ `details_*`, `balances_*`). Raw bank data never travels through `bankingApp-server`.
2. The back-end **distills** those raw files into each person's
   `<person>_transactions.json` (new ids appended; existing ids and local
   `modifications` kept) and then **deletes the raw sources** — the simplified
   file is the durable local record. Pass `?delete_after=false` to keep the raw
   files for debugging.

`POST /api/reload` (and startup) re-distills any raw files already on disk; it
never deletes local files. The per-person `consent.json` handles live only on
`bankingApp-server` (consumed there by `psd2-api collect`) and are **not** mirrored
into local storage, so bankingApp-admin holds only its own artifacts
(`<person>_transactions.json`, `<person>_categories.json`). All file-serving
requests read **live from disk** (no in-memory cache). If `bankingApp-server` is
unreachable, reload still succeeds and serves whatever is on disk.

```
bank (Enable Banking) ──psd2-api collect──▶ bankingApp-admin local storage
                                                   │ distill + categorise
                                                   ▼
                          back-end (FastAPI + pandas) ──/api──▶ React + TS front-end
```

## Data model & tables

The app borrows bankingApp-editor's nomenclature:

- **Raw bank file** — `storage/<person>/transactions*.json` as delivered by the
  bank API (verbose, one big array).
- **Simplified transactions** — `storage/<person>/<person>_transactions.json`,
  built from the raw file. Each record is distilled to
  `id, amount, currency, type, name, IBAN, description, date, category`:
  - `amount` = `transaction_amount.amount` signed by `credit_debit_indicator`
    (`CRDT` → `+`, `DBIT` → `-`).
  - `name` = `creditor.name` for outgoing (DBIT) / `debtor.name` for incoming (CRDT).
  - `description` = all `remittance_information` lines concatenated.
  - `date` = `booking_date` reformatted `YYYY-MM-DD` → `DD-MM-YYYY`.
  - `category` = integer code deduced from keyword lists (see below).
  - User edits are stored separately under a `modifications` array (keyed by
    `id`), which is preserved across rebuilds and overlays the originals.
- **Categorisation** — copies bankingApp-editor's rules: cash withdrawals
  (`type == geldautomaat`) start at `08`; then each keyword (general list +
  the person's personal list) is matched on **whole-word** boundaries against
  the `name`/`description` haystack, last match wins; unmatched → `18`.
- **O-table (a.k.a. H-table)** — consolidation: one row per category, one
  column per person, each cell that person's signed total for the category.
- **P-table** — drill-down: every transaction for one person + one category.
  Editable inline (description / category); matched keywords are highlighted.
- **S-table** — settings: editable keyword lists per category, with an
  `Algemeen` (general) column plus one column per person.

## Categories config

`categories/bank_categories.json` is the general config, with three sections:

- `categories` — `"NN Name"` → list of keyword strings (the general lists).
- `abbreviations` — transaction-type → short label (e.g. `Overschrijving` → `OV`),
  used to shrink the P-table `Soort` column.
- `widths` — P-table column widths as fractions of panel width.

Per-person additions live flat in `storage/<person>/<person>_categories.json`.

## Project layout

```
bankingApp-admin/
├─ app/                     # FastAPI back-end (Python)
│  ├─ config.py             # settings (.env): storage URL + API key, CORS, psd2-api dir/window
│  ├─ storage_client.py     # async httpx client: list people/files, fetch JSON
│  ├─ transform.py          # pandas: DataFrame conversion + summaries
│  ├─ export.py             # simplify/categorise transactions; O/P/S table data
│  └─ main.py               # FastAPI app: preload-at-startup + /api endpoints
├─ categories/
│  └─ bank_categories.json  # general categories, abbreviations, column widths
├─ storage/                 # local disk mirror of bankingApp-server (per person)
├─ frontend/                # React + TypeScript (Vite)
│  └─ src/                  # App.tsx, api.ts, types.ts
├─ pyproject.toml
└─ .env.example
```

## Back-end (Python)

Install (using uv):

```powershell
uv pip install -e .            # add "[dev]" for tests/lint: uv pip install -e ".[dev]"
```

Configure: copy `.env.example` to `.env` and set the storage server URL and the
shared API key (`STORAGE_API_KEY`) used to authenticate against `bankingApp-server`
(it must match `API_KEY` in `bankingApp-server`'s `.env` or be derived from
`SERVER_API_PASSPHRASE`).

Run:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

### API

Interactive docs at `http://localhost:8100/docs`.

| Method | Path                                      | Description                                              |
| ------ | ----------------------------------------- | -------------------------------------------------------- |
| GET    | `/api/health`                             | Liveness probe (includes on-disk file count)             |
| GET    | `/api/files`                              | List all JSON files on local disk                        |
| GET    | `/api/files/{id}`                         | Raw JSON contents of one file (`id` = `person/name`)     |
| GET    | `/api/files/{id}/summary`                 | pandas-derived summary (rows, numeric stats, preview)    |
| DELETE | `/api/files/{id}`                         | Delete one file from local disk (not the server)         |
| POST   | `/api/reload`                             | Sync consent from storage, re-distill local raw, re-read disk |
| POST   | `/api/refresh`                            | Fetch everyone's bank data into local storage, distill, clean up (`?delete_after=true`) |
| GET    | `/api/categories`                         | Category names + the O/H consolidation table (+ abbreviations, widths) |
| POST   | `/api/recalculate`                        | Re-categorise everyone's transactions, return fresh O-table |
| GET    | `/api/settings`                           | Keyword lists per category (general + per person) — S-table data |
| PUT    | `/api/settings/{group}/{category}`        | Save a keyword list for one category (`group` = `general` or a person short) |
| POST   | `/api/transactions/{short}`               | (Re)build `<short>_transactions.json` from the raw bank file |
| GET    | `/api/transactions/{short}/{category}`    | One person's transactions for a category — P-table data  |
| PUT    | `/api/transactions/{short}/modification`  | Record a modified transaction (overlays the original by `id`) |

On startup and on `POST /api/reload` the back-end contacts `bankingApp-server` (so an
unreachable server is reported) sending the shared API key as `Authorization:
Bearer <key>`, but it does **not** copy anything down: the only thing the server
holds is each person's `consent.json`, which bankingApp-admin has no use for locally
(`psd2-api collect` reads it straight from the server). Raw transactions are
written into local storage by `collect`. Files are identified by `person/name`.

The simplified transactions file can also be built from the CLI:

```powershell
uv run python -m app.export ./storage/js   # one person
uv run python -m app.export ./storage      # everyone
```

## Front-end (React + TypeScript)

```powershell
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The Vite dev server proxies `/api` to the back-end on `http://localhost:8100`,
so no CORS configuration is needed in development. For a production build:

```powershell
npm run build        # outputs to frontend/dist
```

In production, serve `frontend/dist` from any static host and point it at the
back-end; set `CORS_ORIGINS` in the back-end `.env` to the front-end's origin.

### Using the UI

- The app has **two separate windows** (browser tabs): the **tables** window
  (O-table + drill-down) and the **settings** window. Switch between them with
  the browser's native `Ctrl+Tab`, or jump straight to one with `Alt+O` (tables)
  / `Alt+S` (settings) — which open the tab if it isn't already open. Each
  sidebar also has a button linking to the other window. The settings window
  lives at `?view=settings`.
- Editing keyword lists in the settings window applies (re-categorises) when you
  leave that window; the tables window then refreshes its totals automatically.
- **Refresh data** (button in the tables window) runs the full pipeline: fetch
  everyone's latest transactions from their bank, distill, and categorise.
- **O-table** shows the consolidation. Click an amount to drill into the
  **P-table** for that person + category; the sidebar then shows that person's
  per-category column (with a dropdown to switch person).
- In the **P-table** you can edit a transaction's description or category inline;
  edits are persisted as modifications and the totals recompute.

## Notes

- Secrets live in `.env` (git-ignored). Never commit credentials.
- `transform.py` is schema-agnostic: it coerces list-of-records or objects with a
  `transactions`/`items`/`data` array into a DataFrame, so it works across the
  different JSON files without per-file code.
- File-serving endpoints read live from disk; category/transaction endpoints read
  the simplified per-person files (rebuilt on reload / recalculate).
