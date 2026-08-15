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
      <person>/data/
        categorized_transactions.json
        personal_categories.json
        category_totals.json
        downloaded_transactions.json
      <person>/secret/
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

## Client roles (`client_config.json`)

| Role | Rights | Config | “Add person” |
|------|--------|--------|--------------|
| Personal user | Own data only | `"person": "<short>"` set | Hidden |
| Local administrator | One workspace, all people in it | `"person"` empty/absent; `"access"` empty/absent → defaults to `["workspace"]` | Shown |
| Central administrator | All listed workspaces | `"person"` empty/absent; `"access": ["dkg","jl",…]` | Shown |

Config keys (no legacy aliases):

| Key | Meaning |
|-----|---------|
| `server_url` | Hub base URL (e.g. `http://192.168.x.x:8200`) |
| `port` | Client BFF listen port |
| `workspace` | Fixed identity (UI title / hub session label) |
| `access` | Allowed data workspaces; ignored when `person` is set |
| `person` | Empty/absent = all people in the workspace; set = only that short |
| `api_key` | Optional hub Bearer token |
| `enabled` | Hub sync on/off |

Priority: when `person` is set, `access` is ignored and the client locks to the single `workspace` identity.

## Processes

| Process | Port | Config |
|---------|------|--------|
| Hub | 8200 | `CENTRALE_DATA_ROOT` → `server/workspaces` (default); Enable Banking callback |
| Client | 8300 / 8301 / 8302 | `client_config.json` as above |

**Hub must be running** for the UI to load data. Clients never fall back to a local workspace copy.

**Nobody needs to work on the server machine.** Administrators use a LAN client (or open the hub wizard in a browser). The hub listens on `0.0.0.0:8200`; firewall must allow TCP 8200.

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
3. Wizard collects folder name, person alias (short), and bank-account name; creates `workspaces/<ws>/<folder>/{data,secret}/` with empty data stubs + draft `profile.json`.
4. Administrator creates an Enable Banking application at [https://enablebanking.com/cp/applications](https://enablebanking.com/cp/applications) and downloads the `.pem`.
5. Wizard uploads the PEM into `secret/`, sets `app_id` / `key_file` from the PEM filename stem, then fetches transactions from **1 Jan of the current year** through **today**.

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
