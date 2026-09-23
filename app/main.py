from contextlib import asynccontextmanager
import asyncio

from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.openai.client import client
from app.twilio.voice import router as voice_router
from app.core.cloudflared_tunnel import ensure_cloudflared_tunnel, shutdown_cloudflared_tunnel
from app.core.config import APP_PORT, AUTO_CLOUDFLARED_FOR_WSS, PUBLIC_BASE_URL, STREAM_PUBLIC_BASE_URL
from app.core.public_webhook import (
    is_dev_tunnel_url,
    is_local_public_base,
    mark_public_webhook_ok,
    probe_public_health,
    probe_public_media_wss,
    public_webhook_help,
    trust_public_url_despite_self_probe_fail,
)
from app.openai.media_stream import router as media_router, warmup_openai_realtime
from app.api.prompt import router as prompt_router
from app.api.practice import router as practice_router


async def _log_public_webhook_status():
    # Wait until uvicorn is accepting connections (do not block the event loop with sync I/O).
    await asyncio.sleep(1.5)
    if not PUBLIC_BASE_URL:
        print(
            "⚠️ PUBLIC_BASE_URL is not set — outbound calls will fail until you add "
            "your dev tunnel / ngrok https URL to gym-ai-poc/.env"
        )
        return
    if is_dev_tunnel_url(PUBLIC_BASE_URL):
        print(
            "🌤️ Dev tunnel mode — HTTP callbacks:",
            PUBLIC_BASE_URL,
            "— Media Stream WSS warmed at startup.",
        )
        return
    probe = await asyncio.to_thread(probe_public_health, PUBLIC_BASE_URL)
    if probe.get("ok"):
        wss = await probe_public_media_wss(PUBLIC_BASE_URL)
        if wss.get("ok"):
            print("🌤️ PUBLIC_BASE_URL reachable (HTTP + WSS):", PUBLIC_BASE_URL)
        else:
            print(
                "⚠️ WSS self-probe failed (may be a dev-tunnel hairpin false alarm):",
                wss.get("error"),
                "— make-call will still try;",
                wss.get("url"),
            )
    elif trust_public_url_despite_self_probe_fail(PUBLIC_BASE_URL, PUBLIC_BASE_URL, probe):
        print(
            "🌤️ PUBLIC_BASE_URL self-probe timed out (common on dev tunnels);",
            "local server is up — Twilio/browser may still reach:",
            PUBLIC_BASE_URL,
        )
    else:
        print("⚠️ PUBLIC_BASE_URL not reachable:", public_webhook_help(PUBLIC_BASE_URL, probe))


def _needs_auto_cloudflared_wss() -> bool:
    return bool(
        AUTO_CLOUDFLARED_FOR_WSS
        and not STREAM_PUBLIC_BASE_URL
        and PUBLIC_BASE_URL
        and (is_dev_tunnel_url(PUBLIC_BASE_URL) or is_local_public_base(PUBLIC_BASE_URL))
    )


async def _startup_warmup():
    """Block until OpenAI + Twilio WSS tunnel are ready (no requests until this finishes)."""
    print("⏳ Gym AI starting — OpenAI Realtime + public tunnels…", flush=True)
    wss_warm = (
        ensure_cloudflared_tunnel(port=APP_PORT) if _needs_auto_cloudflared_wss() else None
    )
    await asyncio.gather(
        warmup_openai_realtime(),
        wss_warm if wss_warm is not None else asyncio.sleep(0),
    )
    if _needs_auto_cloudflared_wss():
        from app.core.cloudflared_tunnel import media_stream_wss_base

        cf = media_stream_wss_base()
        print("🌤️ Twilio HTTP callbacks (status/webhooks):", PUBLIC_BASE_URL, flush=True)
        if cf:
            print("🎧 Twilio Media Stream WSS:", cf, flush=True)
        else:
            print("❌ Media Stream WSS not ready — /make-call will return 503", flush=True)
    else:
        await _log_public_webhook_status()
    print("✅ Gym AI ready — accept calls from UI (use http://127.0.0.1:8000/make-call locally)", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup_warmup()
    try:
        yield
    finally:
        shutdown_cloudflared_tunnel()


from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware


class HttpOnlyCORSMiddleware(StarletteCORSMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def remember_tunnel_traffic(request: Request, call_next):
    """Opening /docs in the browser proves the tunnel works — remember for make-call."""
    path = request.url.path
    if path in {"/call-status", "/stream-status", "/incoming-call"}:
        print("📥 Twilio webhook:", request.method, path, request.client)
    if PUBLIC_BASE_URL:
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
        expected = urlparse(PUBLIC_BASE_URL).netloc
        if expected and host == expected:
            mark_public_webhook_ok(PUBLIC_BASE_URL)
    return await call_next(request)


app.add_middleware(
    HttpOnlyCORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Gym AI POC Running", "status": "ok"}


@app.get("/test-openai")
def test_openai():

    response = client.responses.create(
        model="gpt-5.5",
        input="Say Hello from OpenAI."
    )

    return {
        "response": response.output_text
    }


app.include_router(voice_router)
app.include_router(media_router)
app.include_router(prompt_router)
app.include_router(practice_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    from app.core.cloudflared_tunnel import media_stream_wss_base

    wss = media_stream_wss_base() or STREAM_PUBLIC_BASE_URL or None
    if _needs_auto_cloudflared_wss() and not wss:
        return JSONResponse(
            status_code=503,
            content={
                "status": "starting",
                "ready": False,
                "error": "Media Stream WSS tunnel (cloudflared) is not ready",
            },
        )
    return {
        "status": "ok",
        "ready": True,
        "public_http": PUBLIC_BASE_URL,
        "media_stream_wss_https": wss,
    }


@app.get("/health/public")
async def health_public():
    if not PUBLIC_BASE_URL:
        return {
            "status": "misconfigured",
            "public_base_url": None,
            "reachable": False,
            "error": "PUBLIC_BASE_URL is not set in .env",
        }
    if is_dev_tunnel_url(PUBLIC_BASE_URL):
        return {
            "status": "ok",
            "public_base_url": PUBLIC_BASE_URL,
            "reachable": True,
            "wss_ready": None,
            "note": "dev tunnel: HTTP/WSS self-probes skipped; confirm Twilio via Media stream connected logs",
        }
    probe = await asyncio.to_thread(probe_public_health, PUBLIC_BASE_URL)
    wss = await probe_public_media_wss(PUBLIC_BASE_URL) if probe.get("ok") else {"ok": False}
    http_ok = bool(probe.get("ok"))
    return {
        "status": "ok" if http_ok else "unreachable",
        "public_base_url": PUBLIC_BASE_URL,
        "reachable": http_ok,
        "wss_ready": bool(wss.get("ok")),
        "http_probe": probe,
        "wss_probe": wss,
    }
