"""FastAPI app for `healthbot serve`. Binds 127.0.0.1 only.

Security model (local, single user):
- A random token is printed/opened once; GET /?token=... swaps it for an HttpOnly,
  SameSite=Strict cookie. Every other route requires that cookie.
- Host must be 127.0.0.1:PORT or localhost:PORT (DNS rebinding), and any non-GET request
  must carry a matching Origin (CSRF from other tabs).
- No upload endpoint. Files only enter via the native folder picker -> scrub_paths.
- Responses and logs carry neutral doc IDs and statuses only, never filenames or paths.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, StreamingResponse)
from markdown_it import MarkdownIt

from .. import records as rec
from ..cli import _Dirs, scrub_paths
from . import agent as agent_mod
from . import picker as picker_mod

STATIC = Path(__file__).parent / "static"
AUTH_COOKIE = "hb_auth"
SID_COOKIE = "hb_sid"
VIEWS = {"index": "INDEX.md", "lab-trends": "labs/lab-trends.md",
         "immunizations": "immunizations/immunizations.md"}
PROCESSED = {"ok", "needs_ocr", "encrypted", "leak", "error", "duplicate"}
IDENTITY_MISSING = ("identity.yaml is missing. Copy identity.example.yaml to identity.yaml "
                    "and fill it in with your own editor, then try again.")

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def render_markdown(text: str) -> str:
    """Raw HTML disabled: record bodies and chat text are untrusted. HTML comments are
    dropped (they would otherwise show up as escaped text)."""
    return _md.render(_COMMENT.sub("", text or ""))


def _log(msg: str):
    print(f"[healthbot serve] {msg}", flush=True)


def create_app(*, port: int, token: str, root: Path | None = None,
               choose_folder=None, session_factory=None) -> FastAPI:
    root = Path(root) if root else agent_mod.repo_root()
    dirs = _Dirs(root / "data")
    records_dir = dirs.data / "records"
    identity_path = root / "identity.yaml"
    choose_folder = choose_folder or picker_mod.choose_folder
    session_factory = session_factory or agent_mod.ChatSession
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    sessions: dict[str, agent_mod.ChatSession] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for s in sessions.values():
            await s.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def authed(request: Request) -> bool:
        c = request.cookies.get(AUTH_COOKIE, "")
        return bool(c) and secrets.compare_digest(c, token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return PlainTextResponse("bad host", status_code=400)
        if request.method not in ("GET", "HEAD") and \
                request.headers.get("origin", "") not in origins:
            return PlainTextResponse("bad origin", status_code=403)
        if request.url.path != "/" and not authed(request):
            return PlainTextResponse("unauthorized", status_code=401)
        return await call_next(request)

    def session_for(request: Request) -> agent_mod.ChatSession:
        sid = request.cookies.get(SID_COOKIE) or "default"
        if sid not in sessions:
            sessions[sid] = session_factory()
        return sessions[sid]

    # ------------------------------------------------------------ pages

    @app.get("/")
    async def index(request: Request):
        q = request.query_params.get("token")
        if q is not None:
            if not secrets.compare_digest(q, token):
                return PlainTextResponse("unauthorized", status_code=401)
            resp = RedirectResponse("/", status_code=303)
            opts = dict(httponly=True, samesite="strict", path="/")
            resp.set_cookie(AUTH_COOKIE, token, **opts)
            resp.set_cookie(SID_COOKIE, secrets.token_urlsafe(16), **opts)
            return resp
        if not authed(request):
            return HTMLResponse("<p>Open the link printed by <code>healthbot serve</code> in "
                                "your terminal.</p>", status_code=401)
        resp = FileResponse(STATIC / "index.html")
        if not request.cookies.get(SID_COOKIE):
            resp.set_cookie(SID_COOKIE, secrets.token_urlsafe(16), httponly=True,
                            samesite="strict", path="/")
        return resp

    @app.get("/static/{name}")
    async def static(name: str):
        if name not in {"app.js", "style.css"}:
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(STATIC / name)

    # ------------------------------------------------------------ status

    @app.get("/api/status")
    async def status():
        return {"identity_present": identity_path.is_file(),
                "identity_message": None if identity_path.is_file() else IDENTITY_MISSING,
                "api_key_warning": agent_mod.api_key_warning()}

    # ------------------------------------------------------------ pick + scrub

    @app.post("/api/pick")
    async def pick():
        if not identity_path.is_file():
            return JSONResponse({"error": "identity_missing", "message": IDENTITY_MISSING},
                                status_code=409)
        try:
            path = await run_in_threadpool(choose_folder)
        except picker_mod.PickerCancelled:
            return {"cancelled": True}
        except picker_mod.PickerError as e:
            return JSONResponse({"error": "picker", "message": str(e)}, status_code=500)
        try:
            from ..identity import load_identity
            identity = load_identity(identity_path)
        except Exception:
            return JSONResponse({"error": "identity_invalid",
                                 "message": "identity.yaml could not be parsed. Fix it in "
                                            "your editor and try again."}, status_code=409)
        try:
            outcomes = await run_in_threadpool(scrub_paths, [path], identity, dirs)
        except Exception:
            _log("scrub failed")
            return JSONResponse({"error": "scrub", "message": "Scrubbing failed."},
                                status_code=500)
        results = [{"doc_id": o.doc_id, "status": o.status, "counts": o.counts,
                    "findings": o.findings} for o in outcomes if o.status in PROCESSED]
        for r in results:
            _log(f"scrub {r['doc_id']} {r['status']}")
        return {"cancelled": False, "results": results,
                "skipped_non_pdf": sum(o.status == "non_pdf" for o in outcomes),
                "new_ok": [r["doc_id"] for r in results if r["status"] == "ok"]}

    # ------------------------------------------------------------ chat

    @app.post("/api/chat")
    async def chat(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        text = body.get("message") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "empty message"}, status_code=400)
        s = session_for(request)
        if s.busy:
            return JSONResponse({"error": "busy"}, status_code=409)
        await s.send(text.strip())
        return {"ok": True}

    @app.post("/api/chat/interrupt")
    async def interrupt(request: Request):
        await session_for(request).interrupt()
        return {"ok": True}

    @app.post("/api/chat/permission")
    async def permission(request: Request):
        """Answer a tool permission prompt (e.g. WebSearch), like the terminal's y/n."""
        try:
            body = await request.json()
        except Exception:
            body = None
        rid = body.get("id") if isinstance(body, dict) else None
        allow = body.get("allow") if isinstance(body, dict) else None
        if not isinstance(rid, str) or not isinstance(allow, bool):
            return JSONResponse({"error": "id and allow required"}, status_code=400)
        if not session_for(request).respond(rid, allow):
            return JSONResponse({"error": "no such prompt"}, status_code=404)
        return {"ok": True}

    @app.post("/api/chat/new")
    async def new_chat(request: Request):
        s = session_for(request)
        await s.close()
        await s.queue.put({"type": "reset"})
        return {"ok": True}

    @app.get("/api/chat/stream")
    async def stream(request: Request):
        s = session_for(request)

        async def gen():
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(s.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/api/render")
    async def render(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            return JSONResponse({"error": "text required"}, status_code=400)
        return {"html": render_markdown(text)}

    # ------------------------------------------------------------ records (no LLM)

    @app.get("/api/records")
    async def list_records():
        out = []
        for r in rec.load_records(records_dir):
            m = r.meta
            out.append({"id": m["id"], "date": rec._datestr(m["date"]), "type": m["type"],
                        "title": str(m["title"]), "provider": str(m["provider"]),
                        "source": str(m["source"]),
                        "tags": [str(t) for t in (m.get("tags") or [])]})
        return {"records": out, "views": sorted(VIEWS)}

    @app.get("/api/records/{record_id}")
    async def get_record(record_id: str):
        if not rec.ID_RE.match(record_id):
            return JSONResponse({"error": "bad id"}, status_code=400)
        for r in rec.load_records(records_dir):
            if r.meta["id"] == record_id:
                m = r.meta
                return {"id": record_id, "date": rec._datestr(m["date"]), "type": m["type"],
                        "title": str(m["title"]), "provider": str(m["provider"]),
                        "source": str(m["source"]), "html": render_markdown(r.body)}
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.get("/api/views/{name}")
    async def get_view(name: str):
        if name not in VIEWS:
            return JSONResponse({"error": "not found"}, status_code=404)
        p = records_dir / VIEWS[name]
        if not p.is_file():
            return {"name": name, "html": "<p><em>Not built yet. Ingest some records "
                                          "first.</em></p>"}
        return {"name": name, "html": render_markdown(p.read_text(encoding="utf-8"))}

    return app


def serve(port: int = 8765, open_browser: bool = True):
    import threading
    import webbrowser

    import uvicorn

    token = secrets.token_urlsafe(32)
    app = create_app(port=port, token=token)
    url = f"http://127.0.0.1:{port}/?token={token}"
    warn = agent_mod.api_key_warning()
    if warn:
        _log("WARNING: " + warn)
    _log(f"open {url}")
    _log("local, personal use only: do not share this URL or expose this port.")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    # access_log off: URLs contain the token and record ids.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
