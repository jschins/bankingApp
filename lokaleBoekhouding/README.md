# lokaleBoekhouding

Local multi-person admin (`lokaleBoekhouding.exe`), syncing immediately with [`centraleBoekhouding`](../centraleBoekhouding/).

## Behaviour

1. **Session start:** register with centrale and pull tracked files for this workspace.
2. **Local mutation:** each successful write immediately up-syncs the affected tracked file(s). If centrale reports `central_wins`, local content is overwritten from the hub.
3. **Incoming central changes:** a background poll of `/api/events` applies matching files and shows a **15-second notification button** labeled with the file path.
4. **Categories:** when any peer’s `categories.json` changes, centrale merges all peers into a root `categories.json` and pushes the merge to every peer; all peers are notified.
5. **Person files:** only this workspace is notified when centrale changes files under this workspace.
6. **No sync button / no pink lock UI** — notifications only.
7. **Process exit:** best-effort push of tracked files, then end session.

Tracked files:

- `boekhouding/categories.json`
- `boekhouding/<person>/data/categorized_transactions.json`
- `boekhouding/<person>/data/personal_categories.json`

## Config

`boekhouding/lokale_config.json`:

```json
{
  "enabled": true,
  "centrale_url": "http://192.168.x.x:8400",
  "workspace": "dkg",
  "api_key": ""
}
```

Env overrides: `CENTRALE_URL`, `LOKALE_WORKSPACE`, `CENTRALE_API_KEY`, `CENTRALE_SYNC=0`.

## Run / build

```powershell
cd lokaleBoekhouding
uv sync
uv run lokale-boekhouding
uv run --group build python scripts/build_onefile.py
# → boekhouding/lokaleBoekhouding.exe
```

Port default **8300**. Start `centraleBoekhouding` first if you want sync.
