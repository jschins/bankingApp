"""FastAPI central lock + selected-file sync service."""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import store

API_KEY = os.environ.get("CENTRALE_API_KEY", "").strip()

app = FastAPI(title="centraleBoekhouding", version="0.1")


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


class FilesPayload(BaseModel):
    categories: Any | None = None
    people: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.runtime import data_root, is_frozen

    return {
        "ok": True,
        "service": "centraleBoekhouding",
        "frozen": is_frozen(),
        "data_root": str(data_root()),
        **store.get_lock_state(),
    }


@app.get("/api/lock")
def api_lock(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.get_lock_state()


@app.post("/api/central/login")
def api_central_login(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.central_login()


@app.post("/api/central/logout")
def api_central_logout(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.central_logout()


@app.post("/api/local/{workspace}/login")
def api_local_login(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    try:
        return store.local_login(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/logout")
def api_local_logout(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    try:
        return store.local_logout(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/files")
def api_get_files(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    try:
        return store.read_workspace_files(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/files")
def api_put_files(
    workspace: str,
    body: FilesPayload,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.write_workspace_files(
            workspace,
            {"categories": body.categories, "people": body.people},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Centrale boekhouding</title>
  <style>
    :root { font-family: Georgia, "Times New Roman", serif; color: #1a1a1a; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: linear-gradient(160deg, #e8eef5 0%, #f7f4ef 55%, #dde6f0 100%); }
    main { width: min(28rem, 92vw); padding: 2rem; }
    h1 { font-size: 1.75rem; margin: 0 0 0.35rem; letter-spacing: 0.02em; }
    p.lead { margin: 0 0 1.5rem; color: #444; line-height: 1.45; }
    .status { padding: 0.85rem 1rem; margin-bottom: 1.25rem; border-left: 4px solid #2a5a8c;
              background: rgba(255,255,255,0.7); }
    .status.on { border-color: #a33; background: #fde8e8; }
    .status strong { display: block; font-size: 1.05rem; margin-bottom: 0.25rem; }
    .row { display: flex; gap: 0.75rem; flex-wrap: wrap; }
    button { font: inherit; cursor: pointer; padding: 0.65rem 1.1rem; border: 1px solid #2a5a8c;
             background: #2a5a8c; color: #fff; }
    button.secondary { background: transparent; color: #2a5a8c; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    .err { color: #a33; margin-top: 1rem; min-height: 1.2em; }
    .meta { margin-top: 1.5rem; font-size: 0.85rem; color: #666; }
  </style>
</head>
<body>
  <main>
    <h1>Centrale boekhouding</h1>
    <p class="lead">Central administrator login controls the lock flag for local administrators.</p>
    <div id="status" class="status" aria-live="polite">
      <strong>Loading…</strong>
      <span></span>
    </div>
    <div class="row">
      <button id="btnLogin" type="button">Log in</button>
      <button id="btnLogout" type="button" class="secondary">Log out</button>
    </div>
    <p id="err" class="err"></p>
    <p class="meta" id="meta"></p>
  </main>
  <script>
    const statusEl = document.getElementById("status");
    const errEl = document.getElementById("err");
    const metaEl = document.getElementById("meta");
    const btnLogin = document.getElementById("btnLogin");
    const btnLogout = document.getElementById("btnLogout");

    async function api(method, path) {
      const r = await fetch(path, { method, headers: { "Accept": "application/json" } });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || r.statusText);
      }
      return r.json();
    }

    function render(state) {
      const on = !!state.central_admin_logged_in;
      statusEl.className = "status" + (on ? " on" : "");
      statusEl.querySelector("strong").textContent = on
        ? "Central administrator: LOGGED IN"
        : "Central administrator: logged out";
      const locals = (state.local_sessions || []).join(", ") || "(none)";
      statusEl.querySelector("span").textContent = "Local sessions: " + locals;
      btnLogin.disabled = on;
      btnLogout.disabled = !on;
      metaEl.textContent = "Lock flag central_admin_logged_in = " + on;
    }

    async function refresh() {
      errEl.textContent = "";
      try {
        render(await api("GET", "/api/lock"));
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    }

    btnLogin.onclick = async () => {
      errEl.textContent = "";
      try { render(await api("POST", "/api/central/login")); }
      catch (e) { errEl.textContent = String(e.message || e); }
    };
    btnLogout.onclick = async () => {
      errEl.textContent = "";
      try { render(await api("POST", "/api/central/logout")); }
      catch (e) { errEl.textContent = String(e.message || e); }
    };

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def admin_page() -> str:
    return _ADMIN_HTML


def run() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8400"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
