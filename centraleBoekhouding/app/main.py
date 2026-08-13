"""FastAPI centrale hub: immediate file sync, events, categories merge."""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import store

API_KEY = os.environ.get("CENTRALE_API_KEY", "").strip()

app = FastAPI(title="centraleBoekhouding", version="0.2")


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


class FilesPayload(BaseModel):
    categories: Any | None = None
    people: dict[str, Any] = Field(default_factory=dict)
    source: str = "local"


class FilePutPayload(BaseModel):
    path: str
    content: Any
    source: str = "local"
    client_revision: int | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.runtime import data_root, is_frozen

    return {
        "ok": True,
        "service": "centraleBoekhouding",
        "frozen": is_frozen(),
        "data_root": str(data_root()),
        **store.get_status(),
    }


@app.get("/api/status")
def api_status(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.get_status()


@app.get("/api/events")
def api_events(
    since_id: int = Query(default=0),
    viewer: str = Query(default="central"),
    workspace: str | None = Query(default=None),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.list_events(since_id=since_id, viewer=viewer, workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/start")
def api_local_session_start(
    workspace: str, _: None = Depends(require_api_key)
) -> dict[str, Any]:
    try:
        return store.local_session_start(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/end")
def api_local_session_end(
    workspace: str, _: None = Depends(require_api_key)
) -> dict[str, Any]:
    try:
        return store.local_session_end(workspace)
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
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/file")
def api_get_file(
    workspace: str,
    path: str = Query(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.read_file(workspace, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/file")
def api_put_file(
    workspace: str,
    body: FilePutPayload,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.put_file(
            workspace,
            body.path,
            body.content,
            source=body.source,
            client_revision=body.client_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/categories/rebuild")
def api_rebuild_categories(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.rebuild_merged_categories(trigger_source="central")


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
    main { width: min(36rem, 94vw); padding: 2rem; }
    h1 { font-size: 1.75rem; margin: 0 0 0.35rem; }
    p.lead { margin: 0 0 1.25rem; color: #444; line-height: 1.45; }
    .status { padding: 0.85rem 1rem; margin-bottom: 1rem; border-left: 4px solid #2a5a8c;
              background: rgba(255,255,255,0.75); }
    .notify-wrap { display: flex; flex-direction: column; gap: 0.5rem; min-height: 3rem; }
    .notify-btn {
      font: inherit; text-align: left; padding: 0.65rem 0.9rem;
      border: 1px solid #334155; background: #fff; color: #0f172a;
      box-shadow: 0 1px 2px rgba(0,0,0,0.08);
    }
    .meta { margin-top: 1.25rem; font-size: 0.85rem; color: #666; }
    .err { color: #a33; margin-top: 0.75rem; min-height: 1.2em; }
    label { display: block; margin-top: 0.75rem; font-size: 0.9rem; }
    input, textarea, select { width: 100%; font: inherit; margin-top: 0.25rem; }
    textarea { min-height: 8rem; font-family: Consolas, monospace; font-size: 0.85rem; }
    button.action { margin-top: 0.75rem; font: inherit; cursor: pointer;
                    padding: 0.55rem 1rem; border: 1px solid #2a5a8c; background: #2a5a8c; color: #fff; }
  </style>
</head>
<body>
  <main>
    <h1>Centrale boekhouding</h1>
    <p class="lead">Immediate sync hub. Local changes appear here as short-lived notifications. Central file writes use the form below (source=central).</p>
    <div class="status" id="status">Loading…</div>
    <div class="notify-wrap" id="notify" aria-live="polite"></div>
    <label>workspace
      <input id="ws" value="dkg"/>
    </label>
    <label>path (e.g. categories.json or person/data/personal_categories.json)
      <input id="path" value="categories.json"/>
    </label>
    <label>JSON content
      <textarea id="content">{}</textarea>
    </label>
    <button class="action" id="btnSave" type="button">Save as central</button>
    <p id="err" class="err"></p>
    <p class="meta" id="meta"></p>
  </main>
  <script>
    let sinceId = 0;
    const notifyEl = document.getElementById("notify");
    const statusEl = document.getElementById("status");
    const errEl = document.getElementById("err");
    const metaEl = document.getElementById("meta");

    async function api(method, path, body) {
      const opts = { method, headers: { "Accept": "application/json" } };
      if (body !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      if (!r.ok) throw new Error(await r.text() || r.statusText);
      return r.json();
    }

    function showNotify(displayPath) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "notify-btn";
      btn.textContent = displayPath;
      notifyEl.prepend(btn);
      setTimeout(() => btn.remove(), 15000);
    }

    async function refreshStatus() {
      const s = await api("GET", "/api/status");
      statusEl.textContent =
        "Sessions: " + ((s.local_sessions || []).join(", ") || "(none)") +
        " · workspaces: " + ((s.workspaces || []).join(", ") || "(none)");
      metaEl.textContent = "latest_event_id=" + (s.latest_event_id || 0);
    }

    async function pollEvents() {
      errEl.textContent = "";
      try {
        const data = await api("GET", `/api/events?viewer=central&since_id=${sinceId}`);
        for (const ev of (data.events || [])) {
          showNotify(ev.display_path || (ev.workspace + "/" + ev.file_path));
          sinceId = Math.max(sinceId, ev.id);
        }
        if (data.latest_id) sinceId = Math.max(sinceId, data.latest_id);
        await refreshStatus();
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    }

    document.getElementById("btnSave").onclick = async () => {
      errEl.textContent = "";
      try {
        const ws = document.getElementById("ws").value.trim();
        const path = document.getElementById("path").value.trim();
        const content = JSON.parse(document.getElementById("content").value);
        const res = await api("PUT", `/api/local/${encodeURIComponent(ws)}/file`, {
          path, content, source: "central"
        });
        metaEl.textContent = "saved " + (res.path || path) + " rev=" + (res.revision || "?");
        await pollEvents();
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    };

    pollEvents();
    setInterval(pollEvents, 1500);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def admin_page() -> str:
    return _ADMIN_HTML


def run() -> None:
    import logging

    import uvicorn

    class _MuteEventsPoll(logging.Filter):
        """Drop noisy GET /api/events access lines (UI/worker polls ~1s)."""

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return "GET /api/events" not in msg

    logging.getLogger("uvicorn.access").addFilter(_MuteEventsPoll())

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8400"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
