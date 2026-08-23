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

Users are stored in **`server/workspaces/users.db`** on the hub. Hash new passwords with:

```powershell
uv run python scripts/hash_password.py yourpassword
```

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
