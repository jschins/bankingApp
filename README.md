# bankingApp

Several projects
1. bankingApp-editor 
2. single-person
3. the rest (see below)


A small, self-hosted **household bookkeeping system** for a family. It pulls bank
transactions (via the bank's sanctioned PSD2 / Open Banking API), distills and
categorises them, and presents consolidation and drill-down tables in a web UI.

It is designed to run on a **trusted home LAN** — no public internet exposure,
no third-party data processors beyond the regulated banking aggregator.

## Components

| Project | What it is | Stack |
| --- | --- | --- |
| **`bankingApp-server`** | Minimal JSON file store. Holds the small per-person **consent records** that link a family member's freshly-authorized bank session to the admin. Runs permanently on one machine. | FastAPI |
| **`psd2-api`** | The bank connector (Enable Banking / PSD2). Provides ① a per-person **executable** family members run to (re-)authorize access, and ② an admin **`collect`** command that fetches transactions. | Python CLI + PyInstaller |
| **`bankingApp-admin`** | The bookkeeping app: back-end fetches + distills + categorises transactions; front-end shows the O/P/S tables and a one-click **Refresh**. Can run on several machines on the LAN. | FastAPI + React/TypeScript (Vite) + pandas |
| **`bankingApp-editor`** | **Legacy / obsolete.** Old code that worked from PDF statements. Kept for reference only; not part of the live flow. | — |

## How it fits together

```
  Family member's laptop                         bankingApp-server (one always-on PC)
  ┌─────────────────────────┐                    ┌──────────────────────────────┐
  │ bankingApp-reauthorize-X.exe  │   consent record   │ storage/<person>/consent.json │
  │  • SCA in the bank app   │ ───── HTTP PUT ───▶ │  (no transactions ever here)  │
  └─────────────────────────┘                    └──────────────────────────────┘
                                                          ▲ HTTP GET consent
                                                          │
  Admin machine(s) on the LAN                             │
  ┌─────────────────────────────────────────────┐        │
  │ bankingApp-admin (FastAPI + React)                │        │
  │   POST /api/refresh ──▶ psd2-api `collect` ──┼────────┘
  │         │                    │  fetch from bank (Enable Banking)
  │         │                    ▼
  │         │            storage/<person>/transactions_*.json (raw, written locally)
  │         ▼
  │   distill ▶ <person>_transactions.json (simplified + categorised); raw deleted
  │   React UI ◀── /api ── back-end (O / P / S tables)
  └─────────────────────────────────────────────┘
```

Key design points:

- **Consent vs. data are separated.** A family member's executable only ever
  uploads a tiny *consent record* (session id + account `uid`s + validity) — never
  transactions. The administrator fetches the actual transactions, as they are
  entitled to do once consent is granted. This is also necessary because each
  re-authorization mints fresh account `uid`s.
- **Raw bank data never travels through `bankingApp-server`.** The `collect` step runs
  on the admin machine and writes raw files **straight into `bankingApp-admin`'s
  storage**, where they are distilled into `<person>_transactions.json` and then
  deleted. `bankingApp-server` only ever holds `consent.json`.
- **Strong Customer Authentication (SCA) cannot be automated** (PSD2 law); a
  human approves in their banking app roughly once per consent period (~90 days).
  Everything around it is automated.

## The re-authorization / refresh cycle

1. **Family member** double-clicks their `bankingApp-reauthorize-<person>.exe`, logs
   in + approves in their bank app, and pastes the redirect URL back. A consent
   record lands on `bankingApp-server`. (Repeat only when consent expires.)
2. **Administrator** clicks **Refresh data** in `bankingApp-admin` (or `POST
   /api/refresh`). For every person with valid consent it fetches transactions,
   writes them into local storage, distills + categorises them into
   `<person>_transactions.json`, and cleans up the raw files.
3. **Administrator** reviews/edits categories in the UI.

To **add a new family member**, see the step-by-step recipe in
[`psd2-api/packaging/README.md`](psd2-api/packaging/README.md#adding-a-new-person-short)
(create their Enable Banking app, drop in a profile + key, build their `.exe`,
hand it over — then just click Refresh).

## Running it (current single-machine setup)

All three live components currently run on one PC. From each project folder:

```powershell
# bankingApp-server (the JSON / consent store)

PS C:\Coding\bankingApp\boekh-server> (Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& .venv\Scripts\Activate.ps1)
(boekh-server) PS C:\Coding\bankingApp\boekh-server> uv sync   
(boekh-server) PS C:\Coding\bankingApp\boekh-server> .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000


# bankingApp-admin back-end
PS C:\Coding\bankingApp\boekh-admin> (Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& .venv\Scripts\Activate.ps1)
(boekh-admin) PS C:\Coding\bankingApp\boekh-admin> uv sync   
(boekh-admin) PS C:\Coding\bankingApp\boekh-admin> .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload

# bankingApp-admin front-end (dev)
cd bankingApp-admin\frontend; npm install; npm run dev    # http://localhost:5173
```

See each project's own `README.md` for setup, configuration, and the full API.

## Configuration & secrets

- **Shared API key** — one secret guards all of `bankingApp-server`'s `/data`
  endpoints (`Authorization: Bearer <hash>`). The same key is configured in
  `bankingApp-server` (`server_api_passphrase` or `API_KEY`), in `bankingApp-admin`
  (`storage_api_key` or `STORAGE_API_KEY`), and in
  `psd2-api`'s `packaging/server.json` (`server_api_passphrase`). The SHA-256 hash
  is sent as the Bearer token.
- **Server address** — `psd2-api/packaging/server.json` holds the bankingApp-server
  URL baked into the family executables. Use the server machine's **hostname**
  (e.g. `http://DESKTOP-SB23T6S:8000`), not its IP, so DHCP changes don't break
  the exes. Run `uv run python -m psd2_api server-url` on the server box to print
  the right value.
- Secrets (`.env`, `*.pem`, `packaging/profiles/`, `packaging/server.json`) are
  git-ignored; only `*.example` templates are committed.

## Remote access & public exposure

The current hub/client stack lives under [`server/`](server/) (hub `:8200`, client `:8300`).
See [`server/readme.md`](server/readme.md) for `hub_ips`, upload grants, and reverse-proxy
headers. By default the system is meant for a **trusted home LAN** (or Tailscale between
machines). You do **not** need Tailscale on every laptop if you expose the app on the
public web the way a typical self-hosted site does (domain + HTTPS reverse proxy).

Three levels of exposure:

| Level | What users get | Where hub + `workspaces/` live | Typical setup |
| --- | --- | --- | --- |
| **1 — Upload only** | HTTPS upload page + CSV/Excel import | Home PC | Caddy/nginx on e.g. `deoudegracht.nl` proxies only `/upload` and `/upload/api/upload*` to `http://<home>:8200` |
| **2 — Full UI, data at home** | Full bookkeeping UI over HTTPS | Home PC | Same proxy (or a small VPS in front) forwards to client `:8300` with `auth_enabled`; client talks to hub on `127.0.0.1:8200` |
| **3 — Full UI on a VPS** | Full bookkeeping UI over HTTPS | Cloud VPS (e.g. Hetzner) | Hub + client + `workspaces/` run on the VPS; Caddy terminates TLS (optional HTTP basic auth) |

```text
Level 1 (upload only):
  Browser → https://yourdomain/upload?t=… → proxy → home:8200/upload

Level 2 (full UI, data at home):
  Browser → https://yourdomain → proxy → home:8300 (client) → 127.0.0.1:8200 (hub)

Level 3 (full UI on VPS):
  Browser → https://yourdomain → Caddy → client + hub on same VPS
```

### Security: level 2 vs level 3

Both levels expose the **full** UI, so authentication (client login, Caddy basic auth, or both) matters more than at level 1. The difference is **where secrets and bank data live**. At **level 2**, PEM keys and `workspaces/` stay on your home PC; the public internet only reaches the client port through the proxy, and the hub can remain gated by `hub_ips` so `:8200` is not directly world-readable. That reduces cloud exposure but adds operational risk: the home machine must stay on, the proxy must reliably reach it, and a compromise of the public URL still yields whatever the logged-in UI can do (refresh, edit categories, view transactions). At **level 3**, the same data and secrets sit on an internet-facing VPS, so you inherit cloud-hosting duties—patching, backups, firewall, and trusting the provider—while the attack surface is simpler (no home tunnel or port forward). Level 2 keeps custody at home; level 3 trades that for availability and a single public endpoint.

**Without public exposure:** keep hub on the home PC and use LAN IPs or [Tailscale](https://tailscale.com/) in `client_config.json`. That requires Tailscale (or same LAN) on each machine — fine for admins, not for occasional users who should only open a link.

### Chosen deployment: level 3 — `expenses.deoudegracht.nl`

**Requirement:** end users use a **browser only** — no Tailscale, no exe, no VPN. That excludes Tailscale Funnel (`*.ts.net`) and level 2 (home hub + tunnel). **Level 3 on a VPS** with a public HTTPS subdomain is the chosen path.

**Public URL:** `https://expenses.deoudegracht.nl` — hub, client, uploads, and (after migration) bank callback on one VPS. Main site `https://deoudegracht.nl` stays on Vivawebhost; only DNS adds the subdomain.

```text
https://deoudegracht.nl                       → existing static site (unchanged)
https://expenses.deoudegracht.nl              → VPS (Caddy → client :8300, hub :8200)
https://expenses.deoudegracht.nl/upload       → same VPS (upload grants; POST OK)
https://expenses.deoudegracht.nl/banking-callback.html → bank OAuth → hub consent API
```

#### 1. Hetzner Cloud — create the server

Console: [console.hetzner.cloud](https://console.hetzner.cloud). You need the server’s **public IPv4** for DNS; end users never see Hetzner — only `https://expenses.deoudegracht.nl`.

**Account and project**

1. Sign up or log in (payment method required; CX22-class servers are roughly €4–6/month).
2. **New project** → e.g. `deoudegracht-expenses` → open that project.

**SSH key (recommended)**

On your admin PC (PowerShell):

```powershell
ssh-keygen -t ed25519 -C "you@deoudegracht" -f "$env:USERPROFILE\.ssh\hetzner_expenses"
Get-Content "$env:USERPROFILE\.ssh\hetzner_expenses.pub"
```

Copy the full `ssh-ed25519 AAAA…` line. In Hetzner: **Security** → **SSH keys** → **Add SSH key** → paste → name e.g. `home-pc`.

If you skip this, Hetzner emails a one-time root password instead.

**Create the server**

1. **Add Server** (inside the project).
2. **Location:** Nuremberg or Falkenstein (Germany).
3. **Image:** Ubuntu 24.04 LTS.
4. **Type:** Shared vCPU **CX22** (2 vCPU, 4 GB RAM) or the current smallest CX tier.
5. **Networking:** enable **Public IPv4** (required). IPv6 optional.
6. **SSH keys:** select the key above.
7. Skip extra volumes/backups for now.
8. **Name:** e.g. `expenses-deoudegracht`.
9. **Create & buy now** (~30 seconds).

**Copy the public IPv4**

**Servers** → your server → note **Public IPv4** (e.g. `46.224.x.x`). Use this everywhere below as `<VPS-IP>`.

**First login**

With SSH key:

```powershell
ssh -i "$env:USERPROFILE\.ssh\hetzner_expenses" root@<VPS-IP>
```

With password from email:

```powershell
ssh root@<VPS-IP>
```

Accept the host-key prompt on first connect.

**Minimal setup on the VPS**

```bash
apt update && apt upgrade -y
timedatectl set-timezone Europe/Amsterdam
```

**Hetzner Cloud Firewall (recommended)**

Console → **Firewalls** → create → inbound rules:

| Protocol | Port | Source |
| --- | --- | --- |
| TCP | 22 | Your home IP (or `0.0.0.0/0` if your IP changes often) |
| TCP | 80 | Any |
| TCP | 443 | Any |

Attach the firewall to the server. Do **not** open **8200** or **8300** publicly — Caddy uses 443 only.

**Verify reachability**

```powershell
ping <VPS-IP>
```

**Hetzner troubleshooting**

| Problem | Fix |
| --- | --- |
| No public IPv4 on the server | Recreate with **Public IPv4** enabled. |
| SSH “Connection refused” | Wait 1–2 minutes after create; check outbound port 22 from your PC. |
| SSH “Permission denied” | Use `-i` with the **private** key (file without `.pub`). |
| Wrong IP in DNS later | Always use the IPv4 from the Hetzner server overview, not a Tailscale or LAN address. |

#### 2. DNS at Vivawebhost (cPanel)

DNS for `deoudegracht.nl` is at **Vivawebhost** — nameservers `ns1-boxer.vivawebhost.com`. Main site today: `deoudegracht.nl` → `78.142.63.230`. Do **not** change WordPress/static files on that host.

1. Open **https://wild.vivawebhost.com:2083** → log in.
2. Search **Zone Editor** (or **Domains** → **Zone Editor**).
3. **Manage** zone for **`deoudegracht.nl`**.
4. **Add Record**:

   | Type | Name / Host | Address |
   | --- | --- | --- |
   | **A** | `expenses` | `<VPS-IP>` (Hetzner public IPv4) |

   cPanel usually wants host `expenses`, not the full FQDN. Optional **AAAA** if the VPS has IPv6.

5. Save. Leave `@` and `www` records pointing at Vivawebhost (`78.142.63.230`).

**Verify DNS** (after 5–30 minutes):

```powershell
nslookup expenses.deoudegracht.nl
```

Must return **`<VPS-IP>`**, not `78.142.63.230`. Caddy on the VPS can obtain Let's Encrypt certificates only after this works.

Do **not** proxy `/upload` through the main static site ([`UPLOAD.md`](UPLOAD.md): that host cannot reverse-proxy POST). Upload links: `https://expenses.deoudegracht.nl/upload?t=…`.

**Progress checklist**

```text
[ ] Hetzner project + server created
[ ] Public IPv4 copied
[ ] SSH login works
[ ] Firewall: 22, 80, 443 only
[ ] cPanel A record: expenses → VPS IP
[ ] nslookup expenses.deoudegracht.nl → VPS IP
[ ] Caddy + hub + client on VPS (next step)
```

#### 3. VPS — app stack (Caddy, hub, client)

1. Install Caddy (or nginx) with TLS for `expenses.deoudegracht.nl`.
2. Run hub (`:8200`) and client (`:8300`) on the VPS; copy `server/workspaces/` there.
3. Proxy public HTTPS to the client; hub on localhost only. Enable `auth_enabled` + `users.json` on the client (browser login for admins; upload uses grant tokens).
4. Serve `banking-callback.html` (from [`single-docker/banking-callback.html`](single-docker/banking-callback.html)) redirecting to `https://expenses.deoudegracht.nl/api/consent/callback` once the hub runs on the VPS.
5. Firewall: allow **443** and **80** (ACME); do not expose `:8200`/`:8300` publicly if Caddy is the front door.

#### Enable Banking (when moving callback to the subdomain)

Register **exactly** in each Enable Banking application and each `secret/profile.json`:

- **Redirect URL:** `https://expenses.deoudegracht.nl/banking-callback.html`
- **Privacy / terms:** keep `https://deoudegracht.nl/privacy.html` and `…/terms.html` on the main site unless you move those too.

Until every app and profile use the new URL, keep `https://deoudegracht.nl/banking-callback.html` on the static site for people not yet migrated.

## Future / multi-family

The architecture is built to scale to several independent households:

- **One `bankingApp-server` per family**, running permanently on a dedicated machine;
  each family has its own `server.json` (its server's hostname + its own API key).
- **`bankingApp-admin` may run on several machines** on the same LAN. An admin box
  reaches its server by hostname: copy that family's `server.json` to it (or set
  `bankingApp_SERVER_URL` / `bankingApp_SERVER_API_KEY` in its `.env`); if no override is
  set it inherits the hostname from `server.json` automatically.
