# Centrale boekhouding — sync hub + central admin UI

Two processes on the central laptop:

| Program | Port | Role |
|---------|------|------|
| **`centraleBoekhouding.exe`** | **8400** | Sync hub: sessions, file PUT/GET, categories merge/deletions, events |
| **`centraleAdmin.exe`** | **8300** | Matrix / terms UI (lokaleBoekhouding in `central_admin` mode); workspace dropdown; **no** bank Refresh |

Local peers run [`lokaleBoekhouding`](../lokaleBoekhouding/) on **8301** (`dkg`) and **8302** (`jl`).

There is **no** central login. Every tracked-file change is synced **immediately**. Conflict policy: **central always wins**.

---

## Network approach (LAN first)

1. **Now:** same LAN — use the server’s LAN IP (e.g. `http://192.168.x.x:8400` for the hub).
2. **Later:** Tailscale — same protocol, different URL.

Optional shared API key via `CENTRALE_API_KEY`. Prefer not syncing `.pem` files.

---

## Roles

| Role | Machine | Program |
|------|---------|---------|
| Sync hub | Central laptop | `centraleBoekhouding.exe` (:8400) |
| Central administrator UI | Central laptop | `centraleAdmin.exe` (:8300) — workspace ▾ switcher; edits push as `source=central` |
| Local administrator | Local laptop(s) | `lokaleBoekhouding.exe` in `dkg/` (:8301) or `jl/` (:8302) |

Central admin discovers people from **`data/` only** (no secrets / no Refresh).

---

## Tracked files

Per peer workspace (`dkg/`, `jl/`, …):

- `categories.json`
- `<person>/data/personal_categories.json`
- `<person>/data/categorized_transactions.json`

Layout:

```text
centraleBoekhouding/boekhouding/
  lokale_config.json       # centralAdmin: role=central_admin, port=8300
  centraleAdmin.exe
  centraleBoekhouding.exe  # hub
  categories.json          # MERGED root (all peers)
  dkg/
    categories.json
    …/data/…
  jl/
    categories.json
    …
```

### Categories merge

When any peer’s `categories.json` is written (local or central):

1. Centrale records term **deletions** (so a removed term is not brought back by another peer’s older copy).
2. Rebuilds a **merged** `boekhouding/categories.json` (union of abbreviations and category terms, minus recorded deletions).
3. Pushes that merged document into **every** peer’s `categories.json`.
4. Notifies all local peers so they pull the update.

Person-file changes under `dkg/...` notify **only** the `dkg` peer. Central admin (viewer=central) is notified for **every** local change.

---

## Sync logic

1. Local mutation → immediate `PUT /api/local/{workspace}/file` (`source=local`).
2. If hub revision is ahead → response `central_wins` and local overwrites from hub content.
3. Central admin mutation → `PUT` with `source=central`.
4. Both sides poll `/api/events` and show a **15-second notification button** whose label is the changed file path (then it disappears).

---

## HTTP API (port **8400**)

| Method | Path | Role |
|--------|------|------|
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/status` | Sessions / workspaces / latest event id |
| `GET` | `/api/events?viewer=central\|local&workspace=&since_id=` | Change events |
| `POST` | `/api/local/{workspace}/session/start` | Start local session |
| `POST` | `/api/local/{workspace}/session/end` | End local session |
| `GET` | `/api/local/{workspace}/file?path=` | Download one tracked file |
| `PUT` | `/api/local/{workspace}/file` | Upload one tracked file |
| `GET/PUT` | `/api/local/{workspace}/files` | Bulk read/write (still supported) |
| `POST` | `/api/categories/rebuild` | Force merge of peer categories |

Open `http://<host>:8400/` for central notifications and a simple central save form.

---

## Run / build

```powershell
cd centraleBoekhouding
uv sync
uv run centrale-boekhouding
# or
uv run --group build python scripts/build_onefile.py
# → boekhouding/centraleBoekhouding.exe

# Central admin UI (from lokaleBoekhouding sources):
cd ..\lokaleBoekhouding
uv run --group build python scripts/build_centrale_admin.py
# → centraleBoekhouding/boekhouding/centraleAdmin.exe (:8300)
```

Start hub first (:8400), then `centraleAdmin.exe` (:8300).

Env (hub): `HOST`, `PORT` (default `8400`), `CENTRALE_API_KEY`, `CENTRALE_DATA_ROOT` (default: `boekhouding/` next to the exe / under the project).
