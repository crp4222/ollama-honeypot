import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import anyio
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from .audit import Audit, Quota
from .demo import demo_response
from .policy import CLOUD_MODEL, INFERENCE_PATHS, PUBLIC_MODEL, InvalidRequest, normalize

ROOT = Path(__file__).resolve().parent.parent


class ClosingStreamingResponse(StreamingResponse):
    """Close the upstream even if a client vanishes before the first body chunk."""
    def __init__(self, content, cleanup, **kwargs):
        super().__init__(content, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self.cleanup()


@dataclass
class Settings:
    mode: str = "demo"
    prompt_file: Path = ROOT / "config/system.txt"
    key_file: Path = ROOT / "private/ollama_api_key"
    data_dir: Path = ROOT / "captures"
    upstream: str = "https://ollama.com"
    max_body: int = 512 * 1024
    max_response: int = 8 * 1024 * 1024
    max_output: int = 2048
    max_concurrent: int = 2
    per_day: int = 30
    total: int = 100
    timeout: float = 120
    read_timeout: float = 15

    @classmethod
    def from_env(cls):
        mode = os.getenv("HONEYPOT_MODE", "demo")
        return cls(
            mode=mode,
            prompt_file=Path(os.getenv("SYSTEM_PROMPT_FILE", ROOT / "config/system.txt")),
            key_file=Path(os.getenv("OLLAMA_API_KEY_FILE", ROOT / "private/ollama_api_key")),
            data_dir=Path(os.getenv("CAPTURE_DIR", ROOT / "captures")),
            upstream=os.getenv("UPSTREAM_URL", "http://127.0.0.1:11434" if mode == "local" else "https://ollama.com"),
            max_output=int(os.getenv("MAX_OUTPUT_TOKENS", "2048")),
            max_concurrent=int(os.getenv("MAX_CONCURRENT", "2")),
            per_day=int(os.getenv("MAX_REQUESTS_PER_DAY", "30")),
            total=int(os.getenv("MAX_REQUESTS_TOTAL", "100")),
            timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "120")),
        )


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest("Les clés JSON dupliquées sont refusées.")
        result[key] = value
    return result


def reject_constant(value):
    raise InvalidRequest("Constante JSON invalide.")


def error(path, message, status, request_id):
    if path == "/v1/messages":
        data = {"type": "error", "error": {"type": "api_error", "message": message}}
    elif path.startswith("/v1/"):
        data = {"error": {"type": "api_error", "message": message}}
    else:
        data = {"error": message}
    return JSONResponse(data, status_code=status, headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})


def stream_error(path, message):
    if path == "/v1/messages":
        return ("event: error\ndata: " + json.dumps({"type": "error", "error": {"type": "api_error", "message": message}}) + "\n\n").encode()
    if path.startswith("/v1/"):
        return ("data: " + json.dumps({"error": {"message": message, "type": "api_error"}}) + "\n\n").encode()
    return (json.dumps({"error": message}) + "\n").encode()


def create_app(settings=None, transport=None):
    cfg = settings or Settings.from_env()
    if cfg.mode not in {"demo", "cloud", "local"}:
        raise ValueError("HONEYPOT_MODE doit être demo, local ou cloud.")
    allowed = {"http://relay:8081", "http://127.0.0.1:11434"} if cfg.mode == "local" else {"https://ollama.com", "http://relay:8081"}
    if cfg.upstream not in allowed:
        raise ValueError("Destination interdite pour ce mode.")
    forced_model = PUBLIC_MODEL if cfg.mode == "local" else CLOUD_MODEL
    if min(cfg.max_output, cfg.max_concurrent, cfg.timeout) <= 0:
        raise ValueError("Les limites doivent être strictement positives.")
    if min(cfg.per_day, cfg.total) < 0:
        raise ValueError("Les quotas doivent être positifs ou nuls (0 = illimité).")

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        prompt = cfg.prompt_file.read_text(encoding="utf-8").strip()
        if not prompt or len(prompt.encode()) > 64 * 1024:
            raise ValueError("Le prompt système doit contenir entre 1 et 65536 octets.")
        headers = {"Accept-Encoding": "identity"}
        if cfg.mode == "cloud":
            key = cfg.key_file.read_text().strip()
            if not key or any(c.isspace() for c in key):
                raise ValueError("Clé Ollama absente ou invalide.")
            headers["Authorization"] = f"Bearer {key}"
        app.state.prompt = prompt
        app.state.audit = Audit(cfg.data_dir)
        app.state.quota = Quota(cfg.data_dir, cfg.per_day, cfg.total)
        app.state.slots = asyncio.Semaphore(cfg.max_concurrent)
        app.state.enabled = True
        app.state.audit.event("startup", None, mode=cfg.mode, model=forced_model,
                              max_requests_per_day=cfg.per_day, max_requests_total=cfg.total,
                              system_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        selected_transport = transport or (httpx.MockTransport(demo_response) if cfg.mode == "demo" else None)
        async with httpx.AsyncClient(
            base_url=cfg.upstream, headers=headers,
            transport=selected_transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(cfg.timeout, connect=10),
            limits=httpx.Limits(max_connections=cfg.max_concurrent, max_keepalive_connections=cfg.max_concurrent),
        ) as client:
            app.state.client = client
            try:
                yield
            finally:
                app.state.quota.close()

    def record(app, kind, request_id, **fields):
        try:
            app.state.audit.event(kind, request_id, **fields)
            return True
        except OSError:
            # Fail closed: never continue unobserved if the capture disk fails.
            app.state.enabled = False
            return False

    async def forward(request, path, payload, request_id):
        app = request.app
        if not app.state.enabled:
            return error(path, "Capture indisponible : inférence suspendue.", 503, request_id)
        try:
            await asyncio.wait_for(app.state.slots.acquire(), 0.05)
        except TimeoutError:
            record(app, "blocked", request_id, reason="concurrency_limit")
            return error(path, "Trop de requêtes simultanées.", 429, request_id)
        try:
            permitted = app.state.quota.reserve()
        except Exception:
            app.state.slots.release()
            record(app, "blocked", request_id, reason="quota_storage_failure")
            return error(path, "Compteur indisponible : inférence suspendue.", 503, request_id)
        if not permitted:
            app.state.slots.release()
            record(app, "blocked", request_id, reason="quota_limit")
            return error(path, "Quota du laboratoire atteint.", 429, request_id)
        if not record(app, "upstream_request", request_id, mode=cfg.mode,
                      destination=cfg.upstream + path, body=payload):
            app.state.slots.release()
            return error(path, "Capture indisponible.", 503, request_id)
        started = time.monotonic()
        try:
            outgoing = app.state.client.build_request("POST", path, json=payload,
                                                       headers={"anthropic-version": "2023-06-01"})
            async with asyncio.timeout(cfg.timeout):
                upstream = await app.state.client.send(outgoing, stream=True)
        except (httpx.HTTPError, TimeoutError):
            app.state.slots.release()
            record(app, "end", request_id, complete=False, reason="upstream_unreachable")
            return error(path, "Ollama Cloud indisponible.", 502, request_id)
        except BaseException:
            app.state.slots.release()
            raise
        if not record(app, "upstream_response", request_id, status=upstream.status_code,
                      content_type=upstream.headers.get("content-type", "")):
            await upstream.aclose()
            app.state.slots.release()
            return error(path, "Capture indisponible.", 503, request_id)

        released = False

        async def release_upstream():
            nonlocal released
            if released:
                return
            released = True
            try:
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
            finally:
                app.state.slots.release()

        # Consume errors privately. Never expose upstream credentials, redirects or account details.
        if not 200 <= upstream.status_code < 300:
            try:
                size = 0
                async with asyncio.timeout(max(0.001, cfg.timeout - (time.monotonic() - started))):
                    async for chunk in upstream.aiter_bytes():
                        part = chunk[:max(0, cfg.max_response - size)]
                        if part:
                            record(app, "upstream_chunk", request_id, data_b64=base64.b64encode(part).decode())
                        size += len(chunk)
                        if size > cfg.max_response:
                            break
            except (httpx.HTTPError, TimeoutError):
                pass
            finally:
                await release_upstream()
                record(app, "end", request_id, complete=False, reason="upstream_error", upstream_status=upstream.status_code)
            status = 429 if upstream.status_code == 429 else 502
            return error(path, "Erreur Ollama Cloud ; consulter la capture locale.", status, request_id)

        if not payload["stream"]:
            data = bytearray()
            complete = False
            reason = "client_disconnected"
            try:
                async with asyncio.timeout(max(0.001, cfg.timeout - (time.monotonic() - started))):
                    async for chunk in upstream.aiter_bytes():
                        if len(data) + len(chunk) > cfg.max_response:
                            reason = "response_size_limit"
                            return error(path, "Réponse trop volumineuse.", 502, request_id)
                        if not record(app, "upstream_chunk", request_id, data_b64=base64.b64encode(chunk).decode()):
                            reason = "capture_unavailable"
                            return error(path, "Capture indisponible.", 503, request_id)
                        data.extend(chunk)
                complete, reason = True, "upstream_eof"
                return Response(bytes(data), status_code=upstream.status_code, headers={"Content-Type": upstream.headers.get("content-type", "application/json"),
                                                       "X-Request-ID": request_id, "Cache-Control": "no-store"})
            except (httpx.HTTPError, TimeoutError):
                reason = "upstream_timeout_or_disconnect"
                return error(path, "Réponse interrompue : délai dépassé ou connexion perdue.", 502, request_id)
            finally:
                await release_upstream()
                record(app, "end", request_id, complete=complete, reason=reason, response_bytes=len(data),
                       duration_seconds=round(time.monotonic() - started, 3))

        async def chunks():
            size = 0
            complete = False
            reason = "client_disconnected"
            try:
                async with asyncio.timeout(max(0.001, cfg.timeout - (time.monotonic() - started))):
                    async for chunk in upstream.aiter_bytes():
                        size += len(chunk)
                        if size > cfg.max_response:
                            reason = "response_size_limit"
                            if payload["stream"]:
                                yield stream_error(path, "Réponse interrompue : taille maximale atteinte.")
                            return
                        if not record(app, "upstream_chunk", request_id, data_b64=base64.b64encode(chunk).decode()):
                            reason = "capture_unavailable"
                            if payload["stream"]:
                                yield stream_error(path, "Capture indisponible : réponse interrompue.")
                            return
                        yield chunk
                complete, reason = True, "upstream_eof"
            except (httpx.HTTPError, TimeoutError):
                reason = "upstream_timeout_or_disconnect"
                if payload["stream"]:
                    yield stream_error(path, "Réponse interrompue : délai dépassé ou connexion perdue.")
            finally:
                await release_upstream()
                record(app, "end", request_id, complete=complete, reason=reason, response_bytes=size,
                       duration_seconds=round(time.monotonic() - started, 3))

        content_type = upstream.headers.get("content-type", "application/json")
        return ClosingStreamingResponse(chunks(), cleanup=release_upstream, status_code=upstream.status_code, headers={
            "Content-Type": content_type, "X-Request-ID": request_id,
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })

    async def handle(request: Request):
        path = request.url.path
        rid = uuid.uuid4().hex
        raw = bytearray()
        truncated = False
        try:
            async with asyncio.timeout(cfg.read_timeout):
                async for chunk in request.stream():
                    remaining = cfg.max_body - len(raw)
                    raw.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
        except (TimeoutError, ClientDisconnect):
            record(request.app, "blocked", rid, path=path, reason="incomplete_request_body")
            return error(path, "Corps de requête incomplet.", 408, rid)
        client = request.client.host if request.client else "unknown"
        # Only the Docker edge uses this flag; it overwrites X-Real-IP, never appends.
        if os.getenv("TRUST_EDGE_IP") == "1":
            client = request.headers.get("x-real-ip", client)
        saved = record(request.app, "incoming", rid, method=request.method, path=path, client=client,
                       headers={k: request.headers[k][:512] for k in ("content-type", "user-agent", "anthropic-version") if k in request.headers},
                       body=bytes(raw).decode("utf-8", errors="replace"), truncated=truncated)
        if not saved:
            return error(path, "Capture indisponible.", 503, rid)
        if truncated:
            record(request.app, "blocked", rid, reason="request_size_limit")
            return error(path, "Requête trop volumineuse.", 413, rid)
        # No arbitrary proxy paths, query strings, management or admin endpoints.
        if request.url.query:
            record(request.app, "blocked", rid, reason="query_parameters")
            return error(path, "Paramètres d'URL refusés.", 400, rid)
        metadata = {"name": PUBLIC_MODEL, "model": PUBLIC_MODEL, "size": 0,
                    "digest": hashlib.sha256(CLOUD_MODEL.encode()).hexdigest(),
                    "modified_at": "2026-09-11T00:00:00Z", "details": {"family": "kimi", "format": "cloud"}}
        if request.method in {"GET", "HEAD"}:
            if path == "/":
                return PlainTextResponse("Ollama is running", headers={"X-Request-ID": rid})
            if path == "/api/tags":
                return JSONResponse({"models": [metadata]})
            if path == "/api/ps":
                return JSONResponse({"models": []})
            if path == "/api/version":
                return JSONResponse({"version": "0.0.0", "gateway": "observation-lab"})
            if path == "/v1/models":
                return JSONResponse({"object": "list", "data": [{"id": PUBLIC_MODEL, "object": "model", "created": 0, "owned_by": "ollama"}]})
        if request.method != "POST" or path not in INFERENCE_PATHS | {"/api/show"}:
            record(request.app, "blocked", rid, reason="route_not_allowed")
            return error(path, "Route non disponible.", 404, rid)
        try:
            body = json.loads(raw, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
            if not isinstance(body, dict):
                raise InvalidRequest("Un objet JSON est requis.")
            if path == "/api/show":
                return JSONResponse({"details": metadata["details"], "capabilities": ["completion", "tools"],
                                     "model_info": {"general.name": PUBLIC_MODEL}})
            payload, policy = normalize(path, body, request.app.state.prompt, cfg.max_output, model=forced_model)
        except (ValueError, TypeError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, InvalidRequest) else "JSON ou structure invalide."
            record(request.app, "blocked", rid, reason="invalid_request", detail=message)
            return error(path, message, 400, rid)
        record(request.app, "policy", rid, **policy)
        return await forward(request, path, payload, rid)

    app = Starlette(lifespan=lifespan, routes=[Route("/{path:path}", handle, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"])])
    app.state.settings = cfg
    return app
