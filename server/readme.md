# Boekhouding — architecture (built from scratch)

Always-on **hub** + identical **client** BFFs + data under **`workspaces/`**.  
No gradual migration from the old package trees: this `server/` tree is the product.

## Layout

```text
bankingApp/server/
  readme.md
  workspaces/                 # DATA ONLY (hub data_root)
    categories.json           # merged root
    _categories_deletions.json
    dkg/
      categories.json
      <person>/data/
        categorized_transactions.json
        personal_categories.json
        category_totals.json
        downloaded_transactions.json
      <person>/secret/        # Refresh + consent on hub (ACLs later)
    jl/
      ...
  hub/                        # always-on :8400 — sync, merge, ALL calculation
    app/                      # FastAPI + categorize / totals / matrix
    entry.py
    pyproject.toml
  client/                     # identical BFF + one frontend :8300+
    app/
    frontend/
    entry.py
    pyproject.toml
```

| Folder | Role |
|--------|------|
| `workspaces/` | JSON + secrets; no application code |
| `hub/` | FastAPI sync hub + **calculation** + (later) monthly bank download |
| `client/` | Same binary everywhere: UI + thin API to hub; config selects workspaces |

## Processes

| Process | Port | Config |
|---------|------|--------|
| Hub | 8400 | `CENTRALE_DATA_ROOT` → `server/workspaces` (default in hub) |
| Client | 8300 / 8301 / 8302 | `client_config.json`: `server_url`, `port`, `workspaces: [...]` |

Same client code for “all workspaces” or “only dkg”; only the config file differs (not a security boundary).

## Calculation

Hub owns recalculate / categorize / `category_totals`.  
`category_totals.json` and `downloaded_transactions.json` live in each person `data/` folder so hub calc can reuse the existing boekhouding.exe logic (same inputs/outputs on disk).

Clients push **inputs**; hub writes derived files and emits events; clients pull and display.

## Secrets + Refresh

Secrets sit under `workspaces/<ws>/<person>/secret/`.  
Hub runs Refresh and consent (dev laptop first; filesystem ACLs later).

## Future (not now)

**Monthly automatic bank statement downloads** on the hub: once a month, for each person with valid consent/secrets, fetch transactions into `downloaded_transactions.json`, then run the same categorize/totals pipeline. Scheduling (Task Scheduler / hub background job) to be added later; design the hub Refresh API so that job can call it.

## Run (dev)

```powershell
cd server\hub
uv sync
uv run hub

cd server\client
uv sync
# example: all workspaces
uv run client
```

## Implementation status

- Hub: `data_root` → `workspaces/`; recalculate on input PUT; tracks `category_totals` + `downloaded_transactions`.
- Client: identical BFF; `workspaces[]` in config; Refresh gated by `has_secrets`; recalculate via hub when sync enabled.
- Frontend: one build; capabilities from `/api/centrale/status` (no `VITE_APP_MODE`).
- Not yet: hub-owned Refresh/consent API; monthly scheduled bank download; filesystem ACLs on `secret/`.
