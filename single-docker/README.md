# single-docker

Docker-packaged **single-person** bookkeeping: fetch bank transactions, renew
consent, edit keyword terms, calculate personal totals, view category-organised
transactions, and optionally upload data to `bankingApp-server`.

Same logic as [`../single-person/`](../single-person/), but with a **FastAPI
backend** and **React web UI** (styled like `bankingApp-admin`, without the
multi-person O-table). Runtime layout:

- **`secret/`** — profile + `.pem` (outside the container mount; never in git)
- **`data/`** — `{person}_config.json`, shared `categories.json`, and all other
  `{person}_*` JSON files

The project has its **own `pyproject.toml` and virtual environment** (`.venv/`),
but stays in the central **`bankingApp` git repository**.

---

## Web UI

Build the frontend once (`cd frontend && npm install && npm run build`), then
start the app. FastAPI serves `frontend/dist/` at the root URL.

| Window | URL | Purpose |
|--------|-----|---------|
| Transacties | http://localhost:8200/ | Sidebar: fetch form + **Categorie \| Bedrag** totals; main panel: transaction table for the clicked category |
| Termen | http://localhost:8200/?view=terms | Keyword editor: **Categorie \| Algemeen \| {person}** |

**Shortcuts:** **Alt+T** → termen window; **Alt+M** → transacties window.
Open each in its own browser tab; fixed window names reuse the same tab.

**Transacties sidebar**

- **date-from / date-to** — passed to the bank API for the fetch
- **new year** — when checked, `{person}_categorized_transactions.json` is
  **replaced** with only this fetch (no merge, modifications cleared). When
  unchecked, new transactions are **appended** (deduped by `id`) and existing
  modifications are kept
- **redirect-code** — shown when consent renewal is needed; paste the full
  redirect URL after bank approval (see [Consent renewal](#consent-renewal))
- **↻ Fetch bank data** — download from bank, categorise, refresh totals

**Termen window** — edit keywords per category. Each edit is saved to disk
immediately; see [Dirty flag and recategorisation](#dirty-flag-and-recategorisation)
for when totals and transaction tables are refreshed.

**Main-panel table** — click a **Bedrag** in the sidebar to drill down. Column
widths are configured in `frontend/src/index.css` (`.p-table col.col-*` rules:
minimum width + viewport percentage). The **Beschrijving** column grows to fit
one line; the main panel scrolls horizontally and vertically when needed.
Category cells are editable inline. In category **18 Overige uitgaven**, clicking
**Beschrijving** opens the keyword-assignment dialog instead of inline edit.

### Dirty flag and recategorisation

The UI has two browser tabs (**Transacties** and **Termen**). Recategorising
every transaction (`POST /api/recalculate`) is expensive, so the frontend only
does it when something may have changed. Switching tabs with **Alt+T** /
**Alt+M** without editing anything does **not** trigger recategorisation.

The tabs communicate via a `BroadcastChannel` (`single-docker`). Message
`recalculated` means “keywords or data changed; reload the transacties view.”

#### Termen tab

- Each keyword edit calls `PUT /api/settings/…` (file saved on the server).
- A **dirty** flag is set after a successful save.
- When you **leave** the termen tab (hide the tab or close the window) **and**
  dirty is true:
  1. `POST /api/recalculate` runs
  2. dirty is cleared
  3. `recalculated` is broadcast to the transacties tab
- If you only switch **Alt+T** / **Alt+M** without changing any terms, dirty
  stays false and nothing is recalculated.

#### Transacties tab

Two refresh paths:

| Path | API | When |
|------|-----|------|
| **Reload** | `GET /api/totals` (+ `GET /api/transactions/…` if a category is open) | Data already recategorised; just refresh the display |
| **Recalculate + reload** | `POST /api/recalculate`, then the same GETs | Local dirty flag is true |

**Transacties dirty flag** is set when you edit a transaction inline (description
or category column). It is cleared after a successful recalculate + reload.

| Event | Behaviour |
|-------|-----------|
| Tab gains focus | Recalculate + reload **only if** transacties dirty |
| `recalculated` message from termen | Reload totals and open category detail (termen already recalculated) |
| Click sidebar **Bedrag** | Load category detail; recalculate first **only if** dirty |
| Inline transaction edit | Set dirty → recalculate + reload immediately |
| Assign keyword (category 18 dialog) | Server recategorises in `POST /api/settings/add-term` → reload only |
| **Fetch bank data** | New categorised data returned from fetch → totals updated directly |

**Category 18 keyword dialog** — saving a term recategorises on the server as
part of the add-term request, then closes the dialog and reloads the detail
table without setting the transacties dirty flag.

API reference: http://localhost:8200/docs

---

## Consent renewal

When `{person}_consent.json` is missing or expired (~90 days), the UI shows a
consent banner.

1. Click **Authorisatie-URL** (or open the URL printed at startup in
   `single-person`) — this calls `POST /auth` at Enable Banking **once**
2. Approve in your banking app
3. Copy the **full redirect URL** from the browser (must contain `?code=...`)
4. Paste into **redirect-code**
5. Click **Fetch bank data** — this exchanges the code for a session and writes
   `{person}_consent.json`

Each redirect URL is **single-use**. If exchange fails with
`WRONG_AUTHORIZATION_CODE`, request a fresh authorisation URL and complete the
bank flow again. Do not click **Authorisatie-URL** more than once before
pasting — a second `/auth` call invalidates the code from the first flow.

Profile field `redirect_url` must **exactly** match the URL registered in the
Enable Banking Control Panel. Country must be ISO 3166-1 alpha-2 (`NL`, not
`NLD`).

---

## Project layout

```
single-docker/
├─ secret/                     # credentials (git-ignored, mounted outside container)
│  ├─ js_profile.json
│  └─ *.pem
├─ data/                       # config + runtime JSON (git-ignored)
│  ├─ js_config.json           # {person}_config.json
│  ├─ categories.json          # shared general keywords + abbreviations
│  ├─ js_consent.json
│  ├─ js_categories.json         # personal keyword overrides
│  ├─ js_categorized_transactions.json
│  ├─ js_downloaded_transactions.json
│  └─ js_category_totals.json
├─ app/
│  ├─ main.py                  # FastAPI
│  ├─ settings.py
│  ├─ paths.py
│  └─ core/                    # categorize.py, single_client.py
├─ frontend/                   # React UI (Vite) → frontend/dist
├─ Dockerfile                  # multi-stage: Node build + Python
└─ docker-compose.yml
```

**Legacy unprefixed names are rejected** (`consent.json`, `profile.json`,
`categorized_transactions.json`, etc.). The app exits with an error if any are
present in `data/`.

---

## Configuration (`data/{person}_config.json`)

Each person has a config file named `{person}_config.json` inside `data/`.
The `{person}` prefix must match `"person"` in the profile JSON.

Example `data/js_config.json` for **local development**:

```json
{
  "profile": "C:/Coding/bankingApp/single-docker/secret/js_profile.json",
  "private_key": "C:/Coding/bankingApp/single-docker/secret/317e65d7-9fdb-48d3-862b-58f0357bf152.pem",
  "data_dir": "data",
  "server_url": "",
  "server_api_key": ""
}
```

| Field | Resolution |
|-------|------------|
| `profile` | Path to `{person}_profile.json` in `secret/` |
| `private_key` | Path to the `.pem` file in `secret/` |
| `data_dir` | Path to the data directory |
| `server_url` / `server_api_key` | Optional upload target |

Path rules:

- `profile`, `private_key`, and `data_dir` may be absolute or relative to the
  **`single-docker/` project root** (the same directory that contains `data/`).
  Example: `"data"` → `single-docker/data/`, `"secret/foo.pem"` →
  `single-docker/secret/foo.pem`, even when the config file lives inside `data/`.

### Config discovery

1. `bankingApp_CONFIG` environment variable (full path), if set
2. Otherwise exactly one `data/*_config.json`
3. If several exist, set `bankingApp_PERSON=js` or `bankingApp_CONFIG`

### Docker config

Mount `secret/` **outside** the app tree. The same relative paths work in Docker
and locally:

```json
{
  "profile": "secret/js_profile.json",
  "private_key": "secret/317e65d7-9fdb-48d3-862b-58f0357bf152.pem",
  "data_dir": "data",
  "server_url": "",
  "server_api_key": ""
}
```

`docker-compose.yml` mounts `./data` → `/app/data` and `./secret` → `/app/secret`.
All project-relative paths resolve under `/app` in the container, the same as
locally under `single-docker/`. Windows absolute paths (`C:/.../secret/...`) are
still accepted and mapped under `app_root`.

---

## Personal file naming

| File | Example for `js` |
|------|------------------|
| Config | `data/js_config.json` |
| Profile / key | `secret/js_profile.json`, `secret/*.pem` |
| Shared categories | `data/categories.json` |
| Personal keywords | `data/js_categories.json` |
| Personal data | `data/js_consent.json`, `data/js_categorized_transactions.json`, … |

`categories.json` holds shared keyword lists and transaction-type **abbreviations**
used in the P-table. Personal overrides live in `{person}_categories.json`.

---

## Local development

```powershell
cd single-docker
uv sync
cd frontend
npm install
npm run build
cd ..
uv run single-docker
```

- UI: http://localhost:8200  
- API: http://localhost:8200/api/health  
- Docs: http://localhost:8200/docs  

### Frontend hot reload

With the API already running on port 8200:

```powershell
cd frontend
npm run dev
```

Open http://localhost:5173 (Vite proxies `/api` to `:8200`).

With a single `data/js_config.json`, no env vars are needed. For multiple people,
set `bankingApp_PERSON=bog` or `bankingApp_CONFIG=...`.

---

## Native executable (Windows / macOS)

Build a self-contained binary (no Docker, no Python on the target machine).
Use the **cross-platform** build script (correct `frontend/dist` bundling on each OS):

```powershell
# Windows
cd single-docker
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

```bash
# macOS / Linux
cd single-docker
chmod +x build_exe.sh
./build_exe.sh
# or: uv run --group build python scripts/build_exe.py
```

The script builds the frontend if needed, then runs PyInstaller. Output is a
**single file**:

| OS | Output |
|----|--------|
| Windows | `dist/bankingApp-single-docker.exe` |
| macOS | `dist/bankingApp-single-docker` |

No `input/`, `output/`, or other folders are created by the build.

**Build on the target OS** — a Windows `.exe` does not run on Mac, and vice versa.

### Deploy

Copy the binary to a folder on the target machine. Beside it, create **`data/`**
and **`secret/`** (same layout as local development):

| Path | Purpose |
|------|---------|
| `secret/js_profile.json` | Enable Banking profile |
| `secret/*.pem` | Private key |
| `data/js_config.json` | Paths (see below) |
| `data/categories.json` | Shared keyword lists |

Use [`config.example.json`](config.example.json) as a template for
`data/js_config.json`.

Example `data/js_config.json` (paths relative to the binary):

```json
{
  "profile": "secret/js_profile.json",
  "private_key": "secret/YOUR-APP-ID.pem",
  "data_dir": "data",
  "server_url": "",
  "server_api_key": ""
}
```

Start the binary — it listens on **http://127.0.0.1:8200** and opens your
browser. Consent and transactions live in `data/` next to the binary.

On macOS, if Gatekeeper blocks an unsigned build: right-click → Open, or
`xattr -cr dist/bankingApp-single-docker`.

To change port: set environment variable `PORT` before starting (default `8200`).

### Troubleshooting `{"detail":"Not found"}` at `/`

The API is running but the **React UI was not bundled** into the executable
(common when PyInstaller was run manually with the wrong `--add-data` separator:
`;` on Windows, `:` on macOS).

1. Open **http://127.0.0.1:8200/api/health** — check `"frontend_ok": true`
2. Rebuild with `scripts/build_exe.py` (not a hand-rolled `pyinstaller` command)
3. As a workaround, copy `frontend/dist/` next to the binary:
   `MybankingApp/frontend/dist/index.html` (and assets) — the app will pick it up

---

## Docker

```powershell
cd single-docker
docker compose up --build
```

| Host | Container |
|------|-----------|
| `./data` | `/app/data` |
| `./secret` | `/app/secret` (read-only) |

Change `bankingApp_CONFIG` in `docker-compose.yml` when switching person
(e.g. `/app/data/bog_config.json`).

---

## API overview

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness + resolved paths |
| GET | `/api/consent/status` | `{ needs_renewal: bool }` |
| POST | `/api/consent/authorize` | Bank authorisation URL |
| POST | `/api/fetch` | Download + categorise (see body below) |
| GET | `/api/totals` | Category totals |
| POST | `/api/recalculate` | Re-apply keywords to all transactions |
| GET | `/api/categories` | Category name list |
| GET | `/api/transactions/{category}` | Drill-down for one category |
| PUT | `/api/transactions/modification` | Save inline edit (description / category) |
| GET | `/api/settings` | Termen table data (general + personal keywords) |
| PUT | `/api/settings/{group}/{category}` | Update keywords (`group`: `general` or person id) |
| POST | `/api/upload` | Push JSON to `bankingApp-server` |

**`POST /api/fetch` body**

```json
{
  "date_from": "2026-01-01",
  "date_to": "2026-01-31",
  "redirect_code": "https://example.com/?code=…",
  "new_year": false
}
```

Returns `{ "transaction_count": N, "totals": { "09 Pension": "123.45", … } }`.

Upload sends standard server filenames (`consent.json`, etc.) under
`/data/{person}/` on `bankingApp-server`.

---

## Migrating from `single-person`

```powershell
uv run python scripts/migrate_from_single_person.py
```

Copies `{person}_*` files into `data/` and profile/pem into `secret/`. Then create
`data/{person}_config.json` with absolute paths into `secret/`.

Source must already use `{person}_profile.json` — legacy `profile.json` is not
accepted.

---

## Git

Ignored: `data/`, `secret/`, `.venv/`, `frontend/node_modules/`, `frontend/dist/`

Never commit `.pem` files, consent, or transaction data.

---

## Related projects

| Project | Role |
|---------|------|
| [`single-person`](../single-person/) | Textual GUI + Windows `.exe` (same fetch/categorise logic) |
| [`bankingApp-admin`](../bankingApp-admin/) | Multi-person household admin |
| [`bankingApp-server`](../bankingApp-server/) | Optional JSON upload target |



Pyinstaller executable:

single-docker > powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

Copy the whole dist/ folder and add the two folders (data and secret)

double-click the executable



## ====================
## pyinstaller executable maken op mac:

cd single-docker    
(single-docker) barry@MacBook-Air-4 single-docker % source .venv/bin/activate    
## compile the frontend
cd frontend
[ npm install  # if not done yet ]
npm run build

cd ..
## compile the backend
(single-docker) barry@MacBook-Air-4 single-docker %
uv run pyinstaller --clean -F -y -n reports_webapp --collect-submodules uvicorn --collect-submodules starlette --collect-submodules pydantic --copy-metadata fastapi --copy-metadata uvicorn --copy-metadata starlette --hidden-import uvicorn.logging  --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.loops.auto --hidden-import uvicorn.lifespan.on --add-data frontend/dist:frontend/dist entry.py

daarna de folders data en secret terugplaatsen

NB zowel de compilaties als git pull laten de secret en data folders ongemoeid, maar HOUD VOOR DE ZEKERHEID een kopie boven bankingApp