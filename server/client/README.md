# boekhouding-client

Identical BFF + frontend. See [`../readme.md`](../readme.md).

## Onefile build

```powershell
cd server\client
uv sync --group build
uv run python scripts/build_onefile.py
# optional: --force-frontend
```

Output: `dist/boekhouding-client.exe` (+ `client_config.json` if missing).
