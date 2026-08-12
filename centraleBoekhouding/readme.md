# Centrale boekhouding — immediate file sync + notifications

Executable: **`centraleBoekhouding.exe`** (built into `boekhouding/`). Runs continually as the sync hub.

Local counterpart: [`lokaleBoekhouding`](../lokaleBoekhouding/) → **`lokaleBoekhouding.exe`**.

There is **no** central login and **no** inspection/modification mode. Every tracked-file change is synced **immediately**. Conflict policy: **central always wins**.

---

## Network approach (LAN first)

1. **Now:** same LAN — use the server’s LAN IP (e.g. `http://192.168.x.x:8400`).
2. **Later:** Tailscale — same protocol, different URL.

Optional shared API key via `CENTRALE_API_KEY`. Prefer not syncing `.pem` files.

---

## Roles

| Role | Machine | Program |
|------|---------|---------|
| Central administrator | Distant laptop | `centraleBoekhouding.exe` (hub + notify UI + central file writes) |
| Local administrator | Local laptop | `lokaleBoekhouding.exe` (workspace e.g. `dkg`) |

---

## Tracked files

Per peer workspace (`dkg/`, `jl/`, …):

- `categories.json`
- `<person>/data/personal_categories.json`
- `<person>/data/categorized_transactions.json`

Layout:

```text
centraleBoekhouding/boekhouding/
  categories.json          # MERGED root (all peers)
  dkg/
    categories.json        # peer copy (receives merge)
    juleon_schins/data/…
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

Person-file changes under `dkg/...` notify **only** the `dkg` peer. Central is notified for **every** local change.

---

## Sync logic

1. Local mutation → immediate `PUT /api/local/{workspace}/file` (`source=local`).
2. If hub revision is ahead → response `central_wins` and local overwrites from hub content.
3. Central mutation → `PUT` with `source=central` (admin page form or API).
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
```

Env: `HOST`, `PORT` (default `8400`), `CENTRALE_API_KEY`, `CENTRALE_DATA_ROOT` (default: `boekhouding/` next to the exe / under the project).
