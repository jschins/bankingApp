# boekhouding-hub

Always-on hub for `server/workspaces/`. Full architecture, roles, and add-person flow: [`../readme.md`](../readme.md).

## Listen / client `server_url`

Default `HOST=0.0.0.0` `PORT=8200`. Administrators open the hub from their own PCs (no work on the server required). Allow inbound TCP **8200** on the host firewall.

Clients point at the hub via `server_url` in `client_config.json` (still plain HTTP; bank consent callback stays on public HTTPS via deoudegracht):

| Where the client runs | `server_url` |
|-----------------------|--------------|
| Same PC as the hub | `http://127.0.0.1:8200` |
| Another PC on the home LAN | `http://<hub-lan-ip>:8200` (e.g. `http://192.168.178.49:8200`) |
| Another PC via [Tailscale](https://tailscale.com/) | `http://<hub-tailscale-ip>:8200` or MagicDNS name |

Run several clients on one machine with different `port` values (e.g. 8300 / 8301 / 8302). Start the hub on **8200** first.

## Hub IP gate + scoped upload

Config: `server/workspaces/upload_acl.json`

```json
{
  "hub_ips": ["127.0.0.1", "100.87.15.71", "100.11.22.33"],
  "grants": [
    {
      "person": "rafael_bidarra",
      "token": "token_rafael_bidarra",
      "center": "dkg"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `hub_ips` | Full access to **all** of `:8200` (root, clients, admin). Others get 404 on `/`. `127.0.0.1` always included. Omit/`[]` = no hub-wide gate. |
| `person` + `center` | Upload destination: `workspaces/<center>/<person>/data/` |
| `token` | Upload secret (`Bearer` / `?t=` / form) |

`/upload` is reachable from any IP. Writing a file requires a grant token.

Upload page: [http://127.0.0.1:8200/upload](http://127.0.0.1:8200/upload) (optional `?t=<token>`).

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
