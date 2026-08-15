# boekhouding-client

Thin BFF + frontend. All data comes from the hub (no local workspace copies). See [`../readme.md`](../readme.md) for roles and the **Add person** hub wizard.

## Config (`client_config.json`)

| Key | Meaning |
|-----|---------|
| `access` | `central` (all workspaces) \| `local` (one workspace) \| `personal` (one person) |
| `workspace` | Required for local/personal; optional for central |
| `person` | Required when `access` is `personal` |
| `server_url` | Hub URL |
| `port` | Client listen port |

## Run

```powershell
# hub must be running on :8200 first
cd server\client
uv sync
uv run client
```

## Onefile build

```powershell
cd server\client
uv sync --group build
uv run python scripts/build_onefile.py
```

Output: `dist/boekhouding-client.exe` (+ `client_config.json` if missing).
