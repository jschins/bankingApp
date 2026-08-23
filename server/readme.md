# Boekhouding — architecture (built from scratch)

Always-on **hub** + identical **client** BFFs.  
**Single data location:** `server/workspaces/` on the hub only. Clients do **not** mirror workspace JSON (offline cache = future).

## Layout

```text
bankingApp/server/
  readme.md
    workspaces/                 # DATA ONLY — hub data_root (secrets included)
    categories.json           # shared by all workspaces
    dkg/
      <person>/2026/
        categorized_transactions.json
        category_totals.json
        downloaded_transactions.json
      <person>/secret/
        personal_categories.json
    jl/
      ...
  hub/                        # :8200 — sync, calculation, domain UI API, consent callback, add-person wizard
  client/                     # identical BFF + frontend :8300+ (thin hub proxy)
```

| Folder | Role |
|--------|------|
| `workspaces/` | JSON + secrets; only the hub reads/writes this tree |
| `hub/` | FastAPI: file store, recalculate, matrix/settings/transactions/refresh, add-person wizard |
| `client/` | Same binary everywhere; config selects identity / access / person; proxies to hub |

## Year folders (lazy seed)

Each person has one folder per calendar year: `workspaces/<ws>/<person>/<YYYY>/`.  
`secret/personal_categories.json` is shared across years. Isolation is the year name — opening 2027 must **not** wipe 2026.

The hub does **not** create next year’s folder on 1 January or on matrix/status reads. A missing year is seeded **lazily on first write**, then left alone.

```text
Upload or Refresh targets year Y
    │
    ├─ Y/ already exists?     → leave it unchanged
    │
    └─ else
         find latest year folder < Y
         write empty books + carried opening balance
         continue upload / refresh
```

### What a new year contains

Under `<person>/2027/` (example):

| File | Seeded contents |
|------|-----------------|
| `categorized_transactions.json` | `{"transactions": [], "modifications": []}` |
| `downloaded_transactions.json` | `[]` |
| `category_totals.json` | every name from shared `categories.json` set to `"0.00"`; `account_balances` copied from the **latest previous year** (iban, name, currency, **balance**), with `"files": []` |

Previous year = max of existing year folders that are **strictly less than Y** (not blindly `Y-1`, in case a year was skipped). Opening balance is that year’s first `account_balances[].balance`, or `"0.00"` if there is no previous year (new person uses the same `onbekend` stub as add-person).

Do **not** copy xlsx, transactions, or last year’s `files` list.

## User store `format` (bank upload layout)

Personal users in `workspaces/users.db` have a **`format`** column. It records how uploads for that person are laid out under their year folder.

### 1. `format` is empty / NULL

Means the person is **not** on a single flat CSV layout yet. Either:

- **1a.** there are already **multiple bank/format subfolders** under the year folder, or  
- **1b.** the person has a **`secret/`** folder (Enable Banking / PEM flow).

### 2. `format` has a single value (e.g. `ing`, `natwest`)

Means: all existing uploads in the year folder (which has **no** bank subfolders) were loaded with that format.

If a **new** file is uploaded whose detected format **differs** from the stored `format`:

1. **2a.** Create a subfolder named after the **existing** format; move all current files from the year folder into it.  
2. **2b.** Create a subfolder named after the **newly detected** format; write the new upload there.  
3. Write **consolidated** results of the two folders back into the year folder (matrix / totals / categorized store at the year root).

### First upload and add-person

- **First upload** for a person with empty `format` and a flat year folder: detect the file’s format and **write it into `users.db.format`**.  
- **Add person**: insert a **new row** in `users.db` (personal login: `username` = person folder, `workspace` = center, `person` set, `format` empty until first upload).

### Password

Temporary rule: login password equals the **username** (e.g. user `regional_admin` → password `regional_admin`). There is **no** `password_hash` column.

## Year folder seeding (when it runs)

`ensure_year_folder()` in `hub/app/yearpath.py` (and the client copy). If `person/Y` exists, it returns immediately.

| Write | Why seed first |
|-------|----------------|
| Excel upload to year Y | **Before** the SALDO check, so `previous_balance + D − E == SALDO` uses last year’s closing figure instead of missing totals (`0.00`) |
| Refresh / Refresh All | When the active year folder is missing: empty books, then append; the bank API then overwrites `account_balances` with live figures |
| Add person | Same seeder instead of a bare `{}` totals file (current calendar year) |

No extra UI: the upload **Year** field (placeholder = current year) and Refresh already name the target. First use of 2027 creates it.

### After seeding

- **Excel:** first 2027 file is checked against carried 2026 saldo; import then adds net and records the xlsx in `files`.
- **Bank:** first 2027 refresh appends into empty transaction files; live balances replace the carried JSON after the fetch.

The per-person **new year overwrite** checkbox is a same-year redo (wipe **this** year’s JSON and refetch — PEM bootstrap). It is not how a calendar year is opened and must not touch previous year folders.

## Client roles (`client_config.json`)

| Role | Rights | Config | “Add person” | Title |
|------|--------|--------|--------------|-------|
| Central | All hub workspaces | `"access": "central"` | Shown | `Centrale Boekhouding` |
| Local | One workspace, all people | `"access": "local"`, `"workspace": "<ws>"` | Shown | `Boekhouding {workspace}` |
| Personal | One person only | `"access": "personal"`, `"workspace": "<ws>"`, `"person": "<short>"` | Hidden | `Boekhouding {workspace}/{person}` |

Config keys (no legacy aliases):

| Key | Meaning |
|-----|---------|
| `server_url` | Hub base URL — use `http://127.0.0.1:8200` on the hub PC; LAN/Tailscale IP from other machines |
| `port` | Client BFF listen port |
| `access` | `central` \| `local` \| `personal` |
| `workspace` | Required for local/personal; optional starting workspace for central |
| `person` | Required when `access` is `personal` (person short) |
| `api_key` | Optional hub Bearer token |
| `enabled` | Hub sync on/off |

## Processes

| Process | Port | Config |
|---------|------|--------|
| Hub | 8200 | `CENTRALE_DATA_ROOT` → `server/workspaces` (default); Enable Banking callback |
| Client | 8300 / 8301 / 8302 | `client_config.json` as above |

**Hub must be running** for the UI to load data. Clients never fall back to a local workspace copy.

**Nobody needs to work on the server machine.** Administrators use a LAN client (or open the hub wizard in a browser). The hub listens on `0.0.0.0:8200`; firewall must allow TCP 8200.

## Network

| Where the client runs | `server_url` |
|-----------------------|--------------|
| Same PC as the hub | `http://127.0.0.1:8200` |
| Another PC on the home LAN | `http://<hub-lan-ip>:8200` (e.g. `http://192.168.1.188:8200`) |
| Another PC via [Tailscale](https://tailscale.com/) | `http://<hub-tailscale-ip>:8200` or MagicDNS name |

1. **Same machine:** use `127.0.0.1`, not the PC’s LAN address — see [Hub IP gate](#hub-ip-gate--scoped-upload) below.
2. **Remote (no code change):** install Tailscale on hub + clients; repoint `server_url` to the hub’s Tailscale IP (still `http://…:8200`).
3. **Bank consent callback stays public HTTPS** via `https://deoudegracht.nl/banking-callback.html` → hub. Tailscale does not replace that redirect.

## Hub IP gate + scoped upload

Config: `server/workspaces/upload_acl.json`

```json
{
  "hub_ips": ["127.0.0.1", "100.87.15.71", "192.168.1.188"],
  "grants": [
    {
      "person": "rafael_bidarra",
      "token": "token_rafael_bidarra",
      "center": "dkg"
    }
  ]
}
```

The `"mappings"` and `"info"` keys (if present) are notes for humans only — the hub ignores them.

### `hub_ips` — full hub access

IPs listed in `hub_ips` may use **everything** on port 8200: root page, client APIs, admin UI, sessions, add-person wizard, etc.

- `127.0.0.1` is **always** allowed, even if omitted from the list.
- If `hub_ips` is empty or missing, there is **no** hub-wide IP gate (open to all).
- Exceptions always reachable: bank consent callback, and **`/upload` + `/api/upload`** (any IP; write still needs a grant token).

### `grants` — upload-only write access

Each grant is **not** a user account. It is a token that may write Excel files into one person folder:

| Field | Meaning |
|-------|---------|
| `person` | Folder name under the workspace (`rafael_bidarra`) |
| `center` | Workspace (`dkg`) — files go to `workspaces/<center>/<person>/<year>/` |
| `token` | Upload secret (`Bearer` / `?t=` / form) |

The upload page (`/upload`) is reachable from any IP. Posting a file requires a matching token. The rest of the hub still requires `hub_ips`.

### How the two lists interact

```text
Request arrives
    │
    ├─ Path is /upload or /api/upload              → upload UI/API ✓
    │
    ├─ IP in hub_ips?                              → full hub ✓
    │
    └─ otherwise                                   → 404
```

### `server_url` vs client IP (same PC)

Two things matter: **where** the client connects (`server_url`) and **who** the hub thinks is calling (client IP).

| `server_url` on the hub PC | Hub sees client IP as | Result with typical `hub_ips` |
|----------------------------|----------------------|-------------------------------|
| `http://127.0.0.1:8200` | `127.0.0.1` (loopback) | Allowed — loopback is always in `hub_ips` |
| `http://192.168.1.188:8200` | `192.168.1.188` (LAN) | Allowed only if that LAN IP is in `hub_ips` |

`127.0.0.1` is the **loopback** address (“this computer talking to itself”). Traffic stays on the machine. Even when you dial your own LAN IP, the hub sees the request as coming from that LAN address, not loopback.

**Practical rule:**

- Client and hub on the **same machine** → `server_url`: `http://127.0.0.1:8200`
- Client on **another machine** → `server_url`: hub’s LAN or Tailscale IP, and add **that client PC’s IP** to `hub_ips`

Upload page: [http://127.0.0.1:8200/upload](http://127.0.0.1:8200/upload) (optional `?t=<token>` when the grant has a token). Successful uploads are logged in `workspaces/upload.log`.

### Public upload link (https)

For convenience you can expose the upload UI via a public HTTPS name:
`https://deoudegracht.nl/upload?t=<token>`.

Important: do **not** reverse-proxy the whole hub to HTTPS. Only proxy the `/upload` URL space (including `/upload/api/upload*`) to the hub’s plain HTTP port `8200`.

When you place a reverse proxy in front, configure it to forward client IP headers (so `upload.log` and the upload page show the real person IP rather than the proxy IP), e.g.:
- `X-Forwarded-For` (preferred)
- `CF-Connecting-IP` and/or `X-Real-IP` (if applicable)

More detail: [`hub/README.md`](hub/README.md).

## Data flow

```text
Frontend → client BFF → hub /api/local/{workspace}/… → workspaces/
```

Clients poll hub events only for notification chips / `data_epoch` (refetch), not to write files.

## Secrets + Refresh

Secrets sit under `workspaces/<ws>/<person>/secret/` on the hub.  
`GET .../capabilities` reports `has_secrets`; `POST .../refresh` runs bank download on the hub.

## Add person (hub wizard)

Onboarding for a new bank person lives on the **hub**, not in the client executable.

1. Local/central administrator clicks **Add person** in the client (hidden for personal users).
2. Browser opens `http://<hub>:8200/add-person?workspace=<ws>` (reachable from any admin PC on the LAN).
3. Wizard collects folder name (this is the person identity, max 40 characters) and bank-account name; creates `secret/` (draft `profile.json`, empty `personal_categories.json`) and seeds the **current year** folder as above.
4. Administrator creates an Enable Banking application at [https://enablebanking.com/cp/applications](https://enablebanking.com/cp/applications). The wizard reminds them of the fields to use, for example:
   - Application name: `boekh-<folder>`
   - Redirect URL: `https://deoudegracht.nl/banking-callback.html`
   - Description: `boekhouding`
   - Data protection email: `j.m.schins@gmail.com`
   - Privacy: [https://deoudegracht.nl/privacy.html](https://deoudegracht.nl/privacy.html)
   - Terms: [https://deoudegracht.nl/terms.html](https://deoudegracht.nl/terms.html)
5. After creating the app, link it (country e.g. Netherlands, ASPSP e.g. ING, usage type **personal**), save the `.pem` on the laptop, return to the wizard, and upload it.
6. Wizard stores the PEM in `secret/`, sets `app_id` / `key_file` from the PEM filename stem, then fetches transactions from **1 Jan of the current year** through **today**.

PEM never has to be copied by hand onto the server; the browser uploads it to the hub.

## Future (not now)

- Offline cache of hub payloads on the client
- Monthly automatic bank downloads on the hub
- Filesystem ACLs on `secret/`

## Run (dev)

```powershell
cd server\hub
uv sync
uv run hub

cd server\client
uv sync
uv run client
```

### Hub onefile

```powershell
cd server\hub
uv sync --group build
uv run python scripts/build_onefile.py
```

Output: `server/workspaces/server.exe` — run it from that folder (data root is the exe directory).

### Stop the hub (:8200)

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) and click **Stop hub**.

(`Ctrl+C` in the hub terminal also works.)
