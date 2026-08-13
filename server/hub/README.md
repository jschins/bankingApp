# boekhouding-hub

Always-on hub for `server/workspaces/`. See [`../readme.md`](../readme.md).

## Onefile

```powershell
cd server\hub
uv sync --group build
uv run python scripts/build_onefile.py
```

Writes `../workspaces/server.exe` (run from `workspaces/`).
