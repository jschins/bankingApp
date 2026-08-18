# Boekhouding — architecture (built from scratch)

Always-on **hub** + identical **client** BFFs.  
**Single data location:** `server/workspaces/` on the hub only. Clients do **not** mirror workspace JSON (offline cache = future).

## Layout

```text
bankingApp/server/
  readme.md
    workspaces/                 # DATA ONLY — hub data_root (secrets included)
    categories.json           # shared by all workspaces
    dkg/
      <person>/2026/
        categorized_transactions.json
        category_totals.json
        downloaded_transactions.json
      <person>/secret/
        personal_categories.json
    jl/
      ...
  hub/                        # :8200 — sync, calculation, domain UI API, consent callback, add-person wizard
  client/                     # identical BFF + frontend :8300+ (thin hub proxy)
```

| Folder | Role |
|--------|------|
| `workspaces/` | JSON + secrets; only the hub reads/writes this tree |
| `hub/` | FastAPI: file store, recalculate, matrix/settings/transactions/refresh, add-person wizard |
| `client/` | Same binary everywhere; config selects identity / access / person; proxies to hub |

## Client roles (`client_config.json`)

| Role | Rights | Config | “Add person” | Title |
|------|--------|--------|--------------|-------|
| Central | All hub workspaces | `"access": "central"` | Shown | `Centrale Boekhouding` |
| Local | One workspace, all people | `"access": "local"`, `"workspace": "<ws>"` | Shown | `Boekhouding {workspace}` |
| Personal | One person only | `"access": "personal"`, `"workspace": "<ws>"`, `"person": "<short>"` | Hidden | `Boekhouding {workspace}/{person}` |

Config keys (no legacy aliases):

| Key | Meaning |
|-----|---------|
| `server_url` | Hub base URL — use `http://127.0.0.1:8200` on the hub PC; LAN/Tailscale IP from other machines |
| `port` | Client BFF listen port |
| `access` | `central` \| `local` \| `personal` |
| `workspace` | Required for local/personal; optional starting workspace for central |
| `person` | Required when `access` is `personal` (person short) |
| `api_key` | Optional hub Bearer token |
| `enabled` | Hub sync on/off |

## Processes

| Process | Port | Config |
|---------|------|--------|
| Hub | 8200 | `CENTRALE_DATA_ROOT` → `server/workspaces` (default); Enable Banking callback |
| Client | 8300 / 8301 / 8302 | `client_config.json` as above |

**Hub must be running** for the UI to load data. Clients never fall back to a local workspace copy.

**Nobody needs to work on the server machine.** Administrators use a LAN client (or open the hub wizard in a browser). The hub listens on `0.0.0.0:8200`; firewall must allow TCP 8200.

## Network

| Where the client runs | `server_url` |
|-----------------------|--------------|
| Same PC as the hub | `http://127.0.0.1:8200` |
| Another PC on the home LAN | `http://<hub-lan-ip>:8200` (e.g. `http://192.168.1.188:8200`) |
| Another PC via [Tailscale](https://tailscale.com/) | `http://<hub-tailscale-ip>:8200` or MagicDNS name |

1. **Same machine:** use `127.0.0.1`, not the PC’s LAN address — see [Hub IP gate](#hub-ip-gate--scoped-upload) below.
2. **Remote (no code change):** install Tailscale on hub + clients; repoint `server_url` to the hub’s Tailscale IP (still `http://…:8200`).
3. **Bank consent callback stays public HTTPS** via `https://deoudegracht.nl/banking-callback.html` → hub. Tailscale does not replace that redirect.

## Hub IP gate + scoped upload

Config: `server/workspaces/upload_acl.json`

```json
{
  "hub_ips": ["127.0.0.1", "100.87.15.71", "192.168.1.188"],
  "grants": [
    {
      "person": "rafael_bidarra",
      "token": "token_rafael_bidarra",
      "center": "dkg"
    }
  ]
}
```

The `"mappings"` and `"info"` keys (if present) are notes for humans only — the hub ignores them.

### `hub_ips` — full hub access

IPs listed in `hub_ips` may use **everything** on port 8200: root page, client APIs, admin UI, sessions, add-person wizard, etc.

- `127.0.0.1` is **always** allowed, even if omitted from the list.
- If `hub_ips` is empty or missing, there is **no** hub-wide IP gate (open to all).
- Exceptions always reachable: bank consent callback, and **`/upload` + `/api/upload`** (any IP; write still needs a grant token).

### `grants` — upload-only write access

Each grant is **not** a user account. It is a token that may write Excel files into one person folder:

| Field | Meaning |
|-------|---------|
| `person` | Folder name under the workspace (`rafael_bidarra`) |
| `center` | Workspace (`dkg`) — files go to `workspaces/<center>/<person>/<year>/` |
| `token` | Upload secret (`Bearer` / `?t=` / form) |

The upload page (`/upload`) is reachable from any IP. Posting a file requires a matching token. The rest of the hub still requires `hub_ips`.

### How the two lists interact

```text
Request arrives
    │
    ├─ Path is /upload or /api/upload              → upload UI/API ✓
    │
    ├─ IP in hub_ips?                              → full hub ✓
    │
    └─ otherwise                                   → 404
```

### `server_url` vs client IP (same PC)

Two things matter: **where** the client connects (`server_url`) and **who** the hub thinks is calling (client IP).

| `server_url` on the hub PC | Hub sees client IP as | Result with typical `hub_ips` |
|----------------------------|----------------------|-------------------------------|
| `http://127.0.0.1:8200` | `127.0.0.1` (loopback) | Allowed — loopback is always in `hub_ips` |
| `http://192.168.1.188:8200` | `192.168.1.188` (LAN) | Allowed only if that LAN IP is in `hub_ips` |

`127.0.0.1` is the **loopback** address (“this computer talking to itself”). Traffic stays on the machine. Even when you dial your own LAN IP, the hub sees the request as coming from that LAN address, not loopback.

**Practical rule:**

- Client and hub on the **same machine** → `server_url`: `http://127.0.0.1:8200`
- Client on **another machine** → `server_url`: hub’s LAN or Tailscale IP, and add **that client PC’s IP** to `hub_ips`

Upload page: [http://127.0.0.1:8200/upload](http://127.0.0.1:8200/upload) (optional `?t=<token>` when the grant has a token). Successful uploads are logged in `workspaces/upload.log`.

More detail: [`hub/README.md`](hub/README.md).

## Data flow

```text
Frontend → client BFF → hub /api/local/{workspace}/… → workspaces/
```

Clients poll hub events only for notification chips / `data_epoch` (refetch), not to write files.

## Secrets + Refresh

Secrets sit under `workspaces/<ws>/<person>/secret/` on the hub.  
`GET .../capabilities` reports `has_secrets`; `POST .../refresh` runs bank download on the hub.

## Add person (hub wizard)

Onboarding for a new bank person lives on the **hub**, not in the client executable.

1. Local/central administrator clicks **Add person** in the client (hidden for personal users).
2. Browser opens `http://<hub>:8200/add-person?workspace=<ws>` (reachable from any admin PC on the LAN).
3. Wizard collects folder name (this is the person identity, max 40 characters) and bank-account name; creates `workspaces/<ws>/<folder>/{data,secret}/` with empty data stubs + draft `profile.json`.
4. Administrator creates an Enable Banking application at [https://enablebanking.com/cp/applications](https://enablebanking.com/cp/applications). The wizard reminds them of the fields to use, for example:
   - Application name: `boekh-<folder>`
   - Redirect URL: `https://deoudegracht.nl/banking-callback.html`
   - Description: `boekhouding`
   - Data protection email: `j.m.schins@gmail.com`
   - Privacy: [https://deoudegracht.nl/privacy.html](https://deoudegracht.nl/privacy.html)
   - Terms: [https://deoudegracht.nl/terms.html](https://deoudegracht.nl/terms.html)
5. After creating the app, link it (country e.g. Netherlands, ASPSP e.g. ING, usage type **personal**), save the `.pem` on the laptop, return to the wizard, and upload it.
6. Wizard stores the PEM in `secret/`, sets `app_id` / `key_file` from the PEM filename stem, then fetches transactions from **1 Jan of the current year** through **today**.

PEM never has to be copied by hand onto the server; the browser uploads it to the hub.

## Future (not now)

- Offline cache of hub payloads on the client
- Monthly automatic bank downloads on the hub
- Filesystem ACLs on `secret/`

## Run (dev)

```powershell
cd server\hub
uv sync
uv run hub

cd server\client
uv sync
uv run client
```

### Hub onefile

```powershell
cd server\hub
uv sync --group build
uv run python scripts/build_onefile.py
```

Output: `server/workspaces/server.exe` — run it from that folder (data root is the exe directory).

### Stop the hub (:8200)

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) and click **Stop hub**.

(`Ctrl+C` in the hub terminal also works.)
