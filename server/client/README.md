# boekhouding-client

Thin BFF + frontend. All data comes from the hub (no local workspace copies). See [`../readme.md`](../readme.md) for roles and the **Add person** hub wizard.

## Config (`client_config.json`)

| Key | Meaning |
|-----|---------|
| `access` | `regional_admin` (all workspaces) \| `local` (one workspace) \| `personal` (one person). Used when `auth_enabled` is false. |
| `workspace` | **Required only for `local` and `personal`.** Ignored for `regional_admin` (use the UI switcher). |
| `person` | Required when `access` is `personal` (folder name, e.g. `juleon_schins`) |
| `server_url` | Hub URL |
| `port` | Client listen port |
| `auth_enabled` | When `true`, one shared client: users log in; credentials live in hub `users.db` |
| `session_secret` | Cookie signing secret (required for real deploys when auth is on) |

### Multi-user login (`auth_enabled`)

When `auth_enabled` is `true`, the client listens on **`0.0.0.0`** (all interfaces) so other PCs can open `http://<server-lan-ip>:8300`. Allow inbound TCP on that port in the host firewall. On the same machine as the hub, set `server_url` to `http://127.0.0.1:8200`.

Logged-in browser users appear on the hub `:8200` session list (hostname from reverse-DNS when possible, username, and client IP).

Users are stored in **`server/workspaces/users.db`** on the hub. Login password equals the username (no `password_hash` column).

Manage users on the hub:

```powershell
cd server\hub
uv run python scripts/user_admin.py list
```

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

## Production Docker + Caddy

`docker-compose.yml` runs the client behind Caddy at
`https://boekhouding.agrolav.nl`. The hub is expected to be running on the
same server on port `8200` by default. Caddy needs ports `80` and `443` open,
and the domain's DNS record must point to this server.

For an automatic GitHub Actions deployment, add a repository secret named
`CLIENT_SESSION_SECRET` under **Settings > Secrets and variables > Actions**.
The deploy workflow writes it to `server/client/.env` on the production server
before starting Compose. Do not commit this file.

For a manual deployment, create `server/client/.env`:

```dotenv
CLIENT_SESSION_SECRET=replace-with-a-long-random-secret
SERVER_URL=http://100.116.99.89:8200
CLIENT_AUTH=true
```

Start or rebuild the deployment:

```bash
cd server/client
docker-compose up -d --build
```

The `client` container is not published directly; Caddy is the public entry
point and stores certificates in Docker volumes. `docker-compose up -d` alone
does not rebuild an image after a source change. A push webhook or CI job must
run `git pull` followed by `docker-compose up -d --build` on the production
server for deployments to happen automatically.
