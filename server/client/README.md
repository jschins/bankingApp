# boekhouding-client

Thin BFF + frontend. All data comes from the hub (no local workspace copies). See [`../readme.md`](../readme.md) for roles and the **Add person** hub wizard.

## Configuration (no config file)

Defaults are hardcoded. Override only via environment variables when needed.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SERVER_URL` | `http://127.0.0.1:8200` | Hub base URL |
| `PORT` | `8300` | Client listen port |
| `CLIENT_AUTH` | on (`true`) | Browser login; set `0`/`false` to disable |
| `CLIENT_SESSION_SECRET` | insecure dev string | Cookie signing secret — **set in production** |
| `CENTRALE_API_KEY` | empty | Optional hub Bearer token |
| `CENTRALE_SYNC` | on | Set `0`/`false` to disable hub sync |
| `CLIENT_BOOTSTRAP_WORKSPACE` | first hub workspace / `dkg` | Workspace used before login (auth on) |
| `CLIENT_ACCESS` | `local` | Only when auth off |
| `CLIENT_WORKSPACE` | empty | Only when auth off |
| `CLIENT_PERSON` | empty | Only when auth off |

There is **no** `client_config.json`.

### Multi-user login (default)

The client listens on **`0.0.0.0`** when auth is on so other PCs can open `http://<server-lan-ip>:8300`. Allow inbound TCP on that port in the host firewall. On the same machine as the hub, leave `SERVER_URL` at `http://127.0.0.1:8200`.

Users live in hub **`workspaces/users.db`**. Login password equals the username.

```powershell
cd server\hub
uv run python scripts/user_admin.py list
```

## Run

```powershell
# hub must be running on :8200 first
cd server\client
uv sync
# optional for production:
# $env:CLIENT_SESSION_SECRET = "long-random-string"
uv run client
```

## Onefile build

```powershell
cd server\client
uv sync --group build
uv run python scripts/build_onefile.py
```

Output: `dist/boekhouding-client.exe`. Set `CLIENT_SESSION_SECRET` in the environment when running the exe.

## Production notes (Caddy / Tailscale)

Public HTTPS is terminated by Caddy (e.g. Lightsail) and reverse-proxied to this client on `:8300`. Hub stays on the home server at `127.0.0.1:8200`. Set `CLIENT_SESSION_SECRET` on the host that runs the client.
