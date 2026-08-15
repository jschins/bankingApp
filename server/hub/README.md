# boekhouding-hub

Always-on hub for `server/workspaces/`. Full architecture, roles, and add-person flow: [`../readme.md`](../readme.md).

## Listen / LAN

Default `HOST=0.0.0.0` `PORT=8200`. Administrators open the hub from their own PCs (no work on the server required). Allow inbound TCP **8200** on the host firewall.

## Add person wizard

- UI: [http://127.0.0.1:8200/add-person](http://127.0.0.1:8200/add-person) (pass `?workspace=dkg`)
- Client **Add person** opens the same URL using `server_url` from `client_config.json`

## Onefile

```powershell
cd server\hub
uv sync --group build
uv run python scripts/build_onefile.py
```

Writes `../workspaces/server.exe` (run from `workspaces/`).
