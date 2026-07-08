# Web API Notes

## System overview

The system is split into several components:

- **psd2-api**: the bank connector. A per-person executable lets a family member
  (re-)authorize bank access and uploads a small **consent record** here; an
  admin `collect` command later reads that consent and fetches transactions.
- **bankingApp-server**: this service — a minimal JSON file store. In the current
  design it holds only the per-person **`consent.json`** hand-off records.
- **bankingApp-admin**: fetches transactions (running `psd2-api collect`) **straight
  into its own local storage**, distills + categorises them, and presents the
  data. It reads consent indirectly (via `collect`), not raw data, from here.
- **bankingApp-editor**: old code manipulating data from PDF sources (obsolete).

> **Role in the live flow.** `bankingApp-server` is the secure hand-off point between
> a family member's executable (which `PUT`s a `consent.json`) and the admin's
> collector (which `GET`s it). Raw bank transactions **no longer travel through
> this server** — the collector writes them directly into `bankingApp-admin`. The API
> below is still a generic per-person JSON store, but in practice only
> `consent.json` lives here.

---

## What this server does

`bankingApp-server` is a minimal FastAPI service that **receives and transmits JSON
files**. Each person gets a folder under `storage/` (e.g. `storage/js`,
`storage/as`), and JSON files are stored, listed, retrieved, and deleted per
person. There is no database or PDF handling — that logic lives in the other
components (`psd2-api`, `bankingApp-admin`). Access is protected by a single shared
API key (see [Security](#security)).

## API

| Method   | Path                    | Description                               |
| -------- | ----------------------- | ----------------------------------------- |
| `GET`    | `/`                     | Health check.                             |
| `GET`    | `/data`                 | List people that have stored files.       |
| `GET`    | `/data/{person}`        | List a person's JSON files.               |
| `GET`    | `/data/{person}/{name}` | Return a stored JSON file.                |
| `PUT`    | `/data/{person}/{name}` | Store a JSON file (request body is JSON). |
| `DELETE` | `/data/{person}/{name}` | Delete a stored JSON file.                |

Notes:

- Every `/data` endpoint (both reads and writes) requires the shared API key,
  sent as the header `Authorization: Bearer <key>`. Only `/` (health) is open.
- `PUT` validates that the request body is well-formed JSON before writing it.
- `person` and `name` are sanitized to safe filename segments (no path
  traversal); a `.json` extension is added to `name` if missing.

### Examples

```powershell
# Store a JSON file for person "js"
curl -X PUT http://192.168.1.50:8000/data/js/balances.json `
     -H "Authorization: Bearer YOUR_API_KEY" `
     -H "Content-Type: application/json" `
     --data '{"balance": 100}'

# List js's files, then read one back
curl http://192.168.1.50:8000/data/js -H "Authorization: Bearer YOUR_API_KEY"
curl http://192.168.1.50:8000/data/js/balances.json -H "Authorization: Bearer YOUR_API_KEY"
```

## Running it

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Security

### The exposure to be aware of

This server is deliberately simple, so the only protection it has is what's
described here. With no controls at all, **anyone who can reach the server's IP
and port could read, write, overwrite, and delete any person's JSON files** —
there are no per-person access checks. The factors that affect exposure are:

- It binds to `0.0.0.0:8000`, so it is reachable from any machine that can route
  to it.
- The Windows firewall rule opens TCP 8000 to the LAN, so the practical reach is
  "everyone on the same LAN/Wi-Fi". Port-forwarding it or giving it a public IP
  would expose it to the entire internet.
- Traffic is plain HTTP, so it is unencrypted and readable by anyone who can
  sniff the network.

### Chosen approach: trusted network + a single shared API key

This deployment relies on **two layers**:

1. **Keep it on a trusted network only** — a home LAN or a private VPN/mesh
   (e.g. Tailscale) where only your own devices can reach it. Do not port-forward
   it or expose it on a public IP.
2. **Require a single shared API key for both reading and writing.** One secret
   is shared by the clients (`psd2-api`, `bankingApp-admin`). Every `/data` request
   must include it as `Authorization: Bearer <key>`; requests without a valid key
   get `401 Unauthorized`. The same key guards reads and writes alike. Only the
   `/` health check is left open.

This is not a full user/role system (there is intentionally no per-person
identity); it is one shared secret that gates all data access, which is a good
fit for a small, trusted setup.

### Configuring the key

Set `API_KEY` in `.env` (see `deploy/.env.example`). Generate a strong value,
e.g. in PowerShell:

```powershell
[Convert]::ToBase64String((1..48 | % { Get-Random -Max 256 }))
```

- When `API_KEY` is **set**, the key is enforced on all `/data` endpoints.
- When `API_KEY` is **empty**, the check is disabled — convenient for local
  development, but never do this on a shared network.

Clients then send the header on every data request:

```
Authorization: Bearer <your-api-key>
```

> Note: the API key is sent in clear text over plain HTTP. On an untrusted
> network put the server behind TLS (a VPN, or a reverse proxy such as Caddy)
> so the key and data are encrypted in transit.

---

## Running the server on a LAN

To let other machines (e.g. `psd2-api` or `bankingApp-admin`) reach the API, the
server must (1) listen on all interfaces and (2) have its firewall port open.

### 1. Bind to all interfaces

Start uvicorn with `--host 0.0.0.0` (the default `127.0.0.1` is only reachable
from the server itself):

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. Open the inbound firewall port (Windows)

Run this in an **elevated** PowerShell (Run as Administrator) on the **server**
laptop. Without admin rights it fails with `Access is denied` (Windows error 5):

```powershell
New-NetFirewallRule -DisplayName "bankingApp-server 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Any
```

A successful run prints the new rule, e.g.:

```
DisplayName                   : bankingApp-server 8000
Enabled                       : True
Profile                       : Any
Direction                     : Inbound
Action                        : Allow
PrimaryStatus                 : OK
```

> `deploy/setup-server.ps1` performs this same step (plus disabling sleep/lid
> actions) and must likewise be run as Administrator.

### 3. Point clients at the server's hostname (preferred) or IP

Prefer the server machine's **hostname** over its IP, so a DHCP-reassigned
address doesn't break clients (the family executables bake this value in at build
time). On the server, print the value with:

```powershell
uv run python -m psd2_api server-url     # e.g. http://DESKTOP-SB23T6S:8000
```

Use that in client config — **not** `localhost`, which on a client machine refers
to the client itself. If name resolution is unavailable on some network, fall
back to the IPv4 address from `ipconfig` (e.g. `192.168.1.50`).

### 4. Verify connectivity from a client

```powershell
Test-NetConnection 192.168.1.50 -Port 8000
```

- `TcpTestSucceeded: True` → the API is reachable.
- `PingSucceeded: True`, `TcpTestSucceeded: False` → firewall still blocking the port.
- `PingSucceeded: False` → wrong IP or the machines aren't on the same network.
