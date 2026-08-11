# lokaleBoekhouding

Local multi-person admin (`lokaleBoekhouding.exe`), forked from `boekh-multiperson`, with sync against [`centraleBoekhouding`](../centraleBoekhouding/).

## Behaviour

1. **Start (local login):** register session on centrale, **pull** selected files into `boekhouding/`.
2. **While running:** poll the centrale lock flag every ~2s. If the central admin is logged in (or was while this session is still open), the UI turns **light pink** with a **large red caution** that local edits will be overwritten.
3. **Logout:** button **Logout & sync to centrale**, or process exit — **push** selected files, clear session and sticky warning.

Selected files (workspace e.g. `dkg` on the server):

- `boekhouding/categories.json`
- `boekhouding/<person>/data/categorized_transactions.json`
- `boekhouding/<person>/data/personal_categories.json`

## Config

Place `boekhouding/lokale_config.json` next to the exe (already in this tree for dev):

```json
{
  "enabled": true,
  "centrale_url": "http://192.168.x.x:8400",
  "workspace": "dkg",
  "api_key": ""
}
```

Env overrides: `CENTRALE_URL`, `LOKALE_WORKSPACE`, `CENTRALE_API_KEY`, `CENTRALE_SYNC=0` to disable.

## Run / build

```powershell
cd lokaleBoekhouding
uv sync
uv run lokale-boekhouding
# build:
uv run --group build python scripts/build_onefile.py
# → boekhouding/lokaleBoekhouding.exe
```

Port default **8300**. Start `centraleBoekhouding` first if you want sync.
