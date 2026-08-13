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
  hub/                        # :8400 — sync events + ALL calculation + domain UI API
  client/                     # identical BFF + frontend :8300+ (thin hub proxy)
```

| Folder | Role |
|--------|------|
| `workspaces/` | JSON + secrets; only the hub reads/writes this tree |
| `hub/` | FastAPI: file store, recalculate, matrix/settings/transactions/refresh |
| `client/` | Same binary everywhere; config selects `workspaces[]`; proxies to hub |

## Processes

| Process | Port | Config |
|---------|------|--------|
| Hub | 8400 | `CENTRALE_DATA_ROOT` → `server/workspaces` (default) |
| Client | 8300 / 8301 / 8302 | `client_config.json`: `server_url`, `port`, `workspaces: [...]` |

**Hub must be running** for the UI to load data. Clients never fall back to a local workspace copy.

## Data flow

```text
Frontend → client BFF → hub /api/local/{workspace}/… → workspaces/
```

Clients poll hub events only for notification chips / `data_epoch` (refetch), not to write files.

## Secrets + Refresh

Secrets sit under `workspaces/<ws>/<person>/secret/` on the hub.  
`GET .../capabilities` reports `has_secrets`; `POST .../refresh` runs bank download on the hub.

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

### Stop the hub (:8400)

Open [http://127.0.0.1:8400/](http://127.0.0.1:8400/) and click **Stop hub**.

(`Ctrl+C` in the hub terminal also works.)
