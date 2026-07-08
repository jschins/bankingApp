# Installing bankingApp on another laptop

This guide sets up a **self-contained bankingApp instance** (server + admin + psd2-api)
on a single Windows laptop, carrying over the existing users and their data.

The current users are **js, as, eg**.

## 1. Prerequisites

Install these on the new laptop first:

1. **Python 3.11+** — https://www.python.org/ (tick "Add Python to PATH").
2. **uv** (Python env/runner):
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
3. **Node.js LTS** — https://nodejs.org/ (gives `node` + `npm`, for the frontend).
4. *(optional)* **Cursor** — https://www.cursor.com/ for editing/managing the project.
5. *(optional)* **Git** — only if you want version control.

## 2. Transfer the project

Build a clean archive on the source machine (excludes the rebuildable folders and the legacy `bankingApp-editor`):
python export_zip.py

Move `bankingApp-export.zip` to the new laptop via **USB stick** (simplest) or a private
cloud upload. The archive contains secrets (`.env` files, `.pem` keys), so keep it
private and delete it once copied.

> A hotel/guest LAN usually blocks laptop-to-laptop traffic, so a direct network
> copy may not work — use a USB stick, your phone's hotspot, or a direct Ethernet
> cable to make your own private network.

Extract the zip to `C:\Coding\bankingApp` so you have:
`C:\Coding\bankingApp\bankingApp-server`, `...\bankingApp-admin`, `...\psd2-api`.

**What's included (and required):** all source, every `.env`,
`psd2-api\packaging\server.json`, `psd2-api\packaging\profiles\<short>\` (each
`profile.json` + its `.pem`), and both `storage\` trees
(`bankingApp-admin\storage` = transactions/categories/edits,
`bankingApp-server\storage` = consents).

## 3. Recreate virtual environments

Do **not** copy the old `.venv` folders — they hardcode machine paths. Recreate
each one, **separating install from run**: do the install once with `uv sync`, then
later always launch with the venv's own Python (see step 6). This avoids `uv run`
re-syncing (and potentially rewriting packages) on every start, which is what can
corrupt an environment if a file is locked mid-operation.

```powershell
cd C:\Coding\bankingApp\bankingApp-server; uv venv; uv sync
cd C:\Coding\bankingApp\bankingApp-admin;  uv venv; uv sync
cd C:\Coding\bankingApp\psd2-api;     uv venv; uv sync
```

> `uv sync` installs the exact, locked versions from each project's `uv.lock`.
> If a project has no lockfile, use `uv pip install -e .` instead.

## 4. Install the frontend

```powershell
cd C:\Coding\bankingApp\bankingApp-admin\frontend; npm install
```

## 5. Verify configuration (server + admin on this one laptop)

Since everything runs locally, the configs should point at `localhost`:

- `bankingApp-admin\.env`:
  - `STORAGE_BASE_URL=http://localhost:8000`
  - `STORAGE_API_KEY=` should be the same shared key configured in `bankingApp-server\.env`
- `psd2-api\.env`:
  - `bankingApp_SERVER_URL=http://localhost:8000`

## 6. Start the three services

Each in its own terminal. Launch via the **venv's own Python** (`.venv\Scripts\python.exe`)
so startup never triggers a re-sync:

```powershell
cd C:\Coding\bankingApp\bankingApp-server; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd C:\Coding\bankingApp\bankingApp-admin;  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100 --reload
cd C:\Coding\bankingApp\bankingApp-admin\frontend; npm run dev
```

> **Why the two `--host` values differ (and why admin needs no API key):**
> `bankingApp-server` listens on `0.0.0.0` because it must accept family members'
> consent uploads from across the network, so it is protected by the shared
> Bearer key (SHA-256 hash of the passphrase) — use the **Authorize** button at `/docs`.
> `bankingApp-admin` is only used by the local React frontend on this same laptop, so
> it binds to `127.0.0.1` (localhost-only) and is unreachable from the LAN. That
> isolation is the protection; an API key would add nothing (it would have to be
> embedded in the browser app to keep it working, so it wouldn't be secret).

Open the frontend URL Vite prints (usually `http://localhost:5173`). The three
users should appear from the copied data, and **Refresh data** should work because
the consents were copied along.

## 7. Later: rebuilding family re-authorization executables

`psd2-api\packaging\server.json` has a `server_url` with the **server machine's
hostname** baked in. Family executables use it to upload consent during
re-authorization. If this laptop becomes the permanent server, update it before
building new executables:

1. Get this laptop's server URL:
   ```powershell
   cd C:\Coding\bankingApp\psd2-api; .\.venv\Scripts\python.exe -m psd2_api server-url
   ```
2. Put the hostname URL into `packaging\server.json` (`server_url`).
3. Build per the recipe in `psd2-api\packaging\README.md`.

Day-to-day **Refresh data** is unaffected by this — admin-side collection talks to
`localhost`.

## 8. Troubleshooting (Windows)

These cover the failures actually hit while installing on a second laptop.

### Before starting anything: clear stale processes
A leftover `python`/`uvicorn` from an earlier attempt is the usual culprit behind
both **port conflicts** (`errno 10048`, "address already in use") **and** package
corruption — a running process holds file handles inside `.venv`, so the next
install/sync can't replace those files and aborts half-way.

```powershell
# See what owns the port, and any stray interpreters:
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object OwningProcess
Get-Process python,uvicorn -ErrorAction SilentlyContinue
# Stop a leftover (use the PID from above):
Stop-Process -Id <pid> -Force
```

Or just start the server on a free port instead: `... --port 8001`.

### Symptom: `failed to remove directory ... .dist-info` during `uv run`
`uv run` re-syncs the environment before running and tried to replace a package
whose files were **locked** (by a stale process, antivirus, or cloud sync). Fix the
lock cause, then prefer the **install-once / run-with-venv-python** pattern from
steps 3 and 6 so normal starts don't re-sync.

### Symptom: `ImportError: cannot import name 'APIRouter' from 'fastapi'`
The venv is **corrupted** — typically a package half-installed after an aborted
sync (e.g. `fastapi` showing version `None` or missing its `__init__.py`). Don't
patch individual packages; rebuild the environment cleanly:

```powershell
cd C:\Coding\bankingApp\bankingApp-server     # (or bankingApp-admin / psd2-api)
# make sure no process from this venv is running first (see above)
Remove-Item -Recurse -Force .venv
uv venv
uv sync
```

(If you must repair in place instead: `python -m ensurepip --default-pip`, delete
the broken `fastapi` and `fastapi-*.dist-info` folders under
`.venv\Lib\site-packages`, then `python -m pip install --no-cache-dir fastapi==0.138.2`.
A full rebuild is more reliable.)

### Prevention
- **Keep the project out of OneDrive/Dropbox-synced folders.** Real-time sync
  grabbing handles in `site-packages` is a classic cause of "failed to remove
  directory" on Windows. `C:\Coding\...` is fine.
- **Install once, run with the venv Python.** `uv venv` + `uv sync` (step 3), then
  launch with `.\.venv\Scripts\python.exe -m uvicorn ...` (step 6). Reserve
  `uv run` / `uv sync` for when you deliberately want to (re)install.
- If the OS file layer itself seems flaky (e.g. File Explorer misbehaving), repair
  it first from an **admin** terminal: `sfc /scannow` then
  `DISM /Online /Cleanup-Image /RestoreHealth`, and reboot.
