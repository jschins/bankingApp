# bankingApp

Several projects
1. bankingApp-editor 
2. single-person
3. the rest (see below)


A small, self-hosted **household bookkeeping system** for a family. It pulls bank
transactions (via the bank's sanctioned PSD2 / Open Banking API), distills and
categorises them, and presents consolidation and drill-down tables in a web UI.

It is designed to run on a **trusted home LAN** — no public internet exposure,
no third-party data processors beyond the regulated banking aggregator.

## Components

| Project | What it is | Stack |
| --- | --- | --- |
| **`bankingApp-server`** | Minimal JSON file store. Holds the small per-person **consent records** that link a family member's freshly-authorized bank session to the admin. Runs permanently on one machine. | FastAPI |
| **`psd2-api`** | The bank connector (Enable Banking / PSD2). Provides ① a per-person **executable** family members run to (re-)authorize access, and ② an admin **`collect`** command that fetches transactions. | Python CLI + PyInstaller |
| **`bankingApp-admin`** | The bookkeeping app: back-end fetches + distills + categorises transactions; front-end shows the O/P/S tables and a one-click **Refresh**. Can run on several machines on the LAN. | FastAPI + React/TypeScript (Vite) + pandas |
| **`bankingApp-editor`** | **Legacy / obsolete.** Old code that worked from PDF statements. Kept for reference only; not part of the live flow. | — |

## How it fits together

```
  Family member's laptop                         bankingApp-server (one always-on PC)
  ┌─────────────────────────┐                    ┌──────────────────────────────┐
  │ bankingApp-reauthorize-X.exe  │   consent record   │ storage/<person>/consent.json │
  │  • SCA in the bank app   │ ───── HTTP PUT ───▶ │  (no transactions ever here)  │
  └─────────────────────────┘                    └──────────────────────────────┘
                                                          ▲ HTTP GET consent
                                                          │
  Admin machine(s) on the LAN                             │
  ┌─────────────────────────────────────────────┐        │
  │ bankingApp-admin (FastAPI + React)                │        │
  │   POST /api/refresh ──▶ psd2-api `collect` ──┼────────┘
  │         │                    │  fetch from bank (Enable Banking)
  │         │                    ▼
  │         │            storage/<person>/transactions_*.json (raw, written locally)
  │         ▼
  │   distill ▶ <person>_transactions.json (simplified + categorised); raw deleted
  │   React UI ◀── /api ── back-end (O / P / S tables)
  └─────────────────────────────────────────────┘
```

Key design points:

- **Consent vs. data are separated.** A family member's executable only ever
  uploads a tiny *consent record* (session id + account `uid`s + validity) — never
  transactions. The administrator fetches the actual transactions, as they are
  entitled to do once consent is granted. This is also necessary because each
  re-authorization mints fresh account `uid`s.
- **Raw bank data never travels through `bankingApp-server`.** The `collect` step runs
  on the admin machine and writes raw files **straight into `bankingApp-admin`'s
  storage**, where they are distilled into `<person>_transactions.json` and then
  deleted. `bankingApp-server` only ever holds `consent.json`.
- **Strong Customer Authentication (SCA) cannot be automated** (PSD2 law); a
  human approves in their banking app roughly once per consent period (~90 days).
  Everything around it is automated.

## The re-authorization / refresh cycle

1. **Family member** double-clicks their `bankingApp-reauthorize-<person>.exe`, logs
   in + approves in their bank app, and pastes the redirect URL back. A consent
   record lands on `bankingApp-server`. (Repeat only when consent expires.)
2. **Administrator** clicks **Refresh data** in `bankingApp-admin` (or `POST
   /api/refresh`). For every person with valid consent it fetches transactions,
   writes them into local storage, distills + categorises them into
   `<person>_transactions.json`, and cleans up the raw files.
3. **Administrator** reviews/edits categories in the UI.

To **add a new family member**, see the step-by-step recipe in
[`psd2-api/packaging/README.md`](psd2-api/packaging/README.md#adding-a-new-person-short)
(create their Enable Banking app, drop in a profile + key, build their `.exe`,
hand it over — then just click Refresh).

## Running it (current single-machine setup)

All three live components currently run on one PC. From each project folder:

```powershell
# bankingApp-server (the JSON / consent store)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# bankingApp-admin back-end
uv run uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload

# bankingApp-admin front-end (dev)
cd bankingApp-admin\frontend; npm install; npm run dev    # http://localhost:5173
```

See each project's own `README.md` for setup, configuration, and the full API.

## Configuration & secrets

- **Shared API key** — one secret guards all of `bankingApp-server`'s `/data`
  endpoints (`Authorization: Bearer <hash>`). The same key is configured in
  `bankingApp-server` (`server_api_passphrase` or `API_KEY`), in `bankingApp-admin`
  (`storage_api_key` or `STORAGE_API_KEY`), and in
  `psd2-api`'s `packaging/server.json` (`server_api_passphrase`). The SHA-256 hash
  is sent as the Bearer token.
- **Server address** — `psd2-api/packaging/server.json` holds the bankingApp-server
  URL baked into the family executables. Use the server machine's **hostname**
  (e.g. `http://DESKTOP-SB23T6S:8000`), not its IP, so DHCP changes don't break
  the exes. Run `uv run python -m psd2_api server-url` on the server box to print
  the right value.
- Secrets (`.env`, `*.pem`, `packaging/profiles/`, `packaging/server.json`) are
  git-ignored; only `*.example` templates are committed.

## Future / multi-family

The architecture is built to scale to several independent households:

- **One `bankingApp-server` per family**, running permanently on a dedicated machine;
  each family has its own `server.json` (its server's hostname + its own API key).
- **`bankingApp-admin` may run on several machines** on the same LAN. An admin box
  reaches its server by hostname: copy that family's `server.json` to it (or set
  `bankingApp_SERVER_URL` / `bankingApp_SERVER_API_KEY` in its `.env`); if no override is
  set it inherits the hostname from `server.json` automatically.
