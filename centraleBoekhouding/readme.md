# Centrale boekhouding — lock + selected-file sync

Executable: **`centraleBoekhouding.exe`**. Runs continually on the distant laptop (“server”).

Local counterpart: [`lokaleBoekhouding`](../lokaleBoekhouding/) → **`lokaleBoekhouding.exe`**.

---

## Network approach (LAN first)

Building and testing on the **same LAN** first is intentional. The product is the lock flag and the selected-file sync; Tailscale only changes how the machines reach each other later.

1. **Now:** same Wi‑Fi / LAN — use the server’s LAN IP (e.g. `http://192.168.x.x:8400`).
2. **Later:** Tailscale (or similar) for access when not on the same network — swap the URL; protocol and exe stay the same.

Still use a shared API key even on LAN. Prefer not syncing `.pem` files.

---

## Roles

| Role | Machine | Program |
|------|---------|---------|
| Central administrator | Distant laptop | `centraleBoekhouding.exe` (login / logout) |
| Local administrator | Local laptop | `lokaleBoekhouding.exe` (workspace e.g. `dkg`) |

---

## Locking flag

- Central admin **login** → `central_admin_logged_in = true`.
- Central admin **logout** → `central_admin_logged_in = false` (and any in-memory central edits are flushed to disk when that UI exists).
- While the flag is true, `lokaleBoekhouding.exe` shows a **light-pink background** and a **large red warning**: local changes will be overwritten when the central admin logs out.
- The warning / pink background clear only after **both** the central admin and the local admin have logged out (local sticky “saw lock” until local logout).

---

## Selected files (sync set)

Per local workspace on the server (example workspace `dkg`):

```text
centraleBoekhouding/          # deploy root next to the exe (or data root)
  dkg/
    categories.json
    juleon_schins/data/categorized_transactions.json
    juleon_schins/data/personal_categories.json
    anton_schins/data/…
    eugen_graas/data/…
```

| Direction | When | Files |
|-----------|------|--------|
| Server → local | Local admin **login** (app start) | All of the above for that workspace |
| Local → server | Local admin **logout** | Same set |

Local disk layout for `lokaleBoekhouding` stays:

```text
boekhouding/
  categories.json
  juleon_schins/data/categorized_transactions.json
  juleon_schins/data/personal_categories.json
  …
```

---

## HTTP API (port **8400**)

| Method | Path | Role |
|--------|------|------|
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/lock` | `{ central_admin_logged_in, local_sessions }` |
| `POST` | `/api/central/login` | Set flag true |
| `POST` | `/api/central/logout` | Set flag false |
| `POST` | `/api/local/{workspace}/login` | Register local session |
| `POST` | `/api/local/{workspace}/logout` | End local session |
| `GET` | `/api/local/{workspace}/files` | Download selected JSON |
| `PUT` | `/api/local/{workspace}/files` | Upload selected JSON |

Optional header: `Authorization: Bearer <CENTRALE_API_KEY>` when `CENTRALE_API_KEY` is set.

Simple central UI: open `http://<host>:8400/` for login/logout.

---

## Run / build

```powershell
cd centraleBoekhouding
uv sync
uv run centrale-boekhouding
# or
uv run --group build python scripts/build_onefile.py
# → dist/centraleBoekhouding.exe
```

Env: `HOST` (default `0.0.0.0`), `PORT` (default `8400`), `CENTRALE_API_KEY`, `CENTRALE_DATA_ROOT` (default: folder containing the exe, or project root in dev).
