# lokaleBoekhouding

Local multi-person admin (`lokaleBoekhouding.exe`), syncing immediately with [`centraleBoekhouding`](../centraleBoekhouding/).

Central laptop also runs **`centraleAdmin.exe`** (same codebase, `role: central_admin`) — see hub readme.

## Behaviour

1. **Session start:** register with centrale and pull tracked files for this workspace.
2. **Local mutation:** each successful write queues an immediate background up-sync of the affected tracked file(s) (API returns without waiting on the hub). If centrale reports `central_wins`, local content is overwritten from the hub.
3. **Incoming central changes:** a background poll of `/api/events` applies matching files and shows a **15-second notification button** labeled with the file path.
4. **Categories:** when any peer’s `categories.json` changes, centrale merges all peers into a root `categories.json` and pushes the merge to every peer; all peers are notified.
5. **Person files:** only this workspace is notified when centrale changes files under this workspace.
6. **No sync button / no pink lock UI** — notifications only.
7. **Process exit:** best-effort push of tracked files, then end session.

Tracked files (per workspace folder `dkg/` or `jl/`):

- `categories.json`
- `<person>/data/categorized_transactions.json`
- `<person>/data/personal_categories.json`

## Ports

| App | Port | Config |
|-----|------|--------|
| `centraleAdmin` (central matrix UI) | **8300** | `centraleBoekhouding/boekhouding/lokale_config.json` |
| `lokaleBoekhouding` workspace **dkg** | **8301** | `dkg/lokale_config.json` |
| `lokaleBoekhouding` workspace **jl** | **8302** | `jl/lokale_config.json` |
| Sync hub `centraleBoekhouding` | **8400** | hub process |

## Config

Example `dkg/lokale_config.json`:

```json
{
  "enabled": true,
  "centrale_url": "http://192.168.x.x:8400",
  "workspace": "dkg",
  "port": 8301,
  "api_key": ""
}
```

`jl/lokale_config.json` uses `"workspace": "jl"` and `"port": 8302`.

Env overrides: `CENTRALE_URL`, `LOKALE_WORKSPACE`, `PORT`, `CENTRALE_API_KEY`, `CENTRALE_SYNC=0`, `LOKALE_CONFIG`, `LOKALE_ROLE=central_admin`.

## Run / build

```powershell
cd lokaleBoekhouding
uv sync
# dkg (default when dkg/lokale_config.json is found):
uv run lokale-boekhouding
# jl in dev:
$env:LOKALE_WORKSPACE="jl"; uv run lokale-boekhouding
uv run --group build python scripts/build_onefile.py
# → dkg/lokaleBoekhouding.exe and jl/lokaleBoekhouding.exe

# Central admin UI: build from the hub package
#   cd ../centraleBoekhouding
#   uv run --group build python scripts/build_centrale_admin.py
```

Start the hub (`centraleBoekhouding` on **8400**) first if you want sync.
