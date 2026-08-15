# boekhouding-client

Thin BFF + frontend. All data comes from the hub (no local workspace copies). See [`../readme.md`](../readme.md) for roles and the **Add person** hub wizard.

## Config (`client_config.json`)

| Key | Meaning |
|-----|---------|
| `workspace` | Identity (title / hub session) |
| `access` | Allowed workspaces (ignored when `person` is set) |
| `person` | Empty/absent = all people; set = personal user (no Add person) |
| `server_url` | Hub URL |

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
