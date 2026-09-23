"""Verify Twilio can reach this app via PUBLIC_BASE_URL (dev tunnel, ngrok, etc.)."""

from __future__ import annotations

import asyncio
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Dev tunnels often flake on outbound self-probes; trust recent Twilio success.
_last_public_ok: dict[str, float] = {}
_PUBLIC_OK_TTL_SEC = 180.0


def discover_ngrok_public_url() -> str | None:
    try:
        with urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    for tunnel in data.get("tunnels") or []:
        if tunnel.get("proto") == "https":
            public = (tunnel.get("public_url") or "").rstrip("/")
            if public:
                return public
    return None


def _health_body_looks_ok(body: str) -> bool:
    text = (body or "").lower()
    return '"status"' in text and "ok" in text


def probe_public_health(http_base: str, *, timeout: float = 12.0) -> dict:
    """
    GET {http_base}/health from the internet-facing URL Twilio will use.
    Returns {"ok": bool, "status_code": int|None, "error": str|None, "url": str}.
    """
    base = (http_base or "").rstrip("/")
    url = f"{base}/health"
    if not base:
        return {"ok": False, "status_code": None, "error": "PUBLIC_BASE_URL is empty", "url": url}

    req = Request(
        url,
        headers={
            # Dev tunnels often return 406 to minimal/API-style clients; browser-like headers work better.
            "User-Agent": "Mozilla/5.0 (compatible; gym-ai-poc-health-probe/1.0)",
            "Accept": "*/*",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            body = resp.read(512).decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read(512).decode("utf-8", errors="replace")
        except Exception:
            pass
        if _health_body_looks_ok(body):
            mark_public_webhook_ok(base)
            return {"ok": True, "status_code": exc.code, "error": None, "url": url}
        return {
            "ok": False,
            "status_code": exc.code,
            "error": f"HTTP {exc.code}",
            "url": url,
        }
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "status_code": None, "error": str(reason), "url": url}
    except Exception as exc:
        return {"ok": False, "status_code": None, "error": repr(exc), "url": url}

    if code != 200 and not _health_body_looks_ok(body):
        return {"ok": False, "status_code": code, "error": f"HTTP {code}", "url": url}

    if not _health_body_looks_ok(body):
        return {
            "ok": False,
            "status_code": code,
            "error": "Unexpected /health response body",
            "url": url,
        }

    mark_public_webhook_ok(base)
    return {"ok": True, "status_code": code, "error": None, "url": url}


def mark_public_webhook_ok(http_base: str) -> None:
    base = (http_base or "").rstrip("/")
    if base:
        _last_public_ok[base] = time.monotonic()


def _recent_public_ok(http_base: str) -> bool:
    base = (http_base or "").rstrip("/")
    seen = _last_public_ok.get(base)
    if not seen:
        return False
    return (time.monotonic() - seen) <= _PUBLIC_OK_TTL_SEC


def probe_local_health(port: int = 8000, *, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.getcode() == 200
    except Exception:
        return False


def probe_local_health_with_retries(
    port: int = 8000,
    *,
    attempts: int = 8,
    pause_sec: float = 0.35,
) -> bool:
    for _ in range(max(1, attempts)):
        if probe_local_health(port=port):
            return True
        time.sleep(pause_sec)
    return False


def is_dev_tunnel_url(http_base: str) -> bool:
    host = urlparse((http_base or "").strip()).netloc.lower()
    return "devtunnels.ms" in host


def is_local_public_base(http_base: str) -> bool:
    host = (urlparse((http_base or "").strip()).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def trust_public_url_despite_self_probe_fail(configured: str, base: str, probe: dict) -> bool:
    """
    Dev tunnels (e.g. devtunnels.ms) often time out when the app curls its own public URL,
    while browsers and Twilio reach the same host fine.
    """
    if probe.get("ok"):
        return False
    if not probe_local_health():
        return False
    configured = (configured or "").rstrip("/")
    base = (base or "").rstrip("/")
    if not configured or base != configured:
        return False
    return True


def probe_public_health_with_retries(
    http_base: str,
    *,
    attempts: int = 3,
    timeout: float = 6.0,
) -> dict:
    last = None
    for _ in range(max(1, attempts)):
        last = probe_public_health(http_base, timeout=timeout)
        if last.get("ok"):
            mark_public_webhook_ok(http_base)
            return last
        time.sleep(0.35)
    return last or {"ok": False, "error": "probe failed", "url": ""}


def public_http_and_ws_bases(http_base: str | None) -> tuple[str, str]:
    """HTTPS/HTTP base URL and matching ws/wss origin (no path)."""
    base = (http_base or "").strip().rstrip("/")
    parsed = urlparse(base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_base = f"{ws_scheme}://{parsed.netloc}"
    return base, ws_base


def http_to_ws_media_url(http_base: str) -> str:
    _, ws_base = public_http_and_ws_bases(http_base)
    return f"{ws_base}/media-stream"


async def probe_public_media_wss(http_base: str, *, timeout: float = 8.0) -> dict:
    """
    Best-effort WSS check to /media-stream. Dev tunnels may fail self-probes while Twilio still connects.
    """
    url = http_to_ws_media_url(http_base)
    if not (http_base or "").strip():
        return {"ok": False, "error": "PUBLIC_BASE_URL is empty", "url": url}

    import websockets

    async def _open_stream_socket() -> None:
        async with websockets.connect(
            url,
            open_timeout=timeout,
            close_timeout=1,
        ):
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(_open_stream_socket(), timeout=timeout + 2)
        return {"ok": True, "error": None, "url": url}
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "url": url}


async def resolve_media_stream_wss_url(http_base: str) -> tuple[str | None, str | None, str | None]:
    """
    Twilio Media Stream WebSocket URL.
    Dev tunnels often return 31901; auto-start cloudflared for WSS while HTTP callbacks stay on http_base.
    Returns (wss_url, https_base_for_wss, error_message).
    """
    from app.core.cloudflared_tunnel import ensure_cloudflared_tunnel
    from app.core.config import APP_PORT, AUTO_CLOUDFLARED_FOR_WSS, STREAM_PUBLIC_BASE_URL

    if STREAM_PUBLIC_BASE_URL:
        return (
            http_to_ws_media_url(STREAM_PUBLIC_BASE_URL),
            STREAM_PUBLIC_BASE_URL,
            None,
        )

    base = (http_base or "").rstrip("/")
    if not base:
        base = (PUBLIC_BASE_URL or "").rstrip("/")

    needs_public_wss = is_dev_tunnel_url(base) or is_local_public_base(base)
    if not needs_public_wss:
        return http_to_ws_media_url(base), None, None

    if not AUTO_CLOUDFLARED_FOR_WSS:
        return http_to_ws_media_url(base), None, None

    # Dev tunnels: never use devtunnels.ms for Twilio WSS (31901). Prefer ngrok or cloudflared.
    ngrok = discover_ngrok_public_url()
    if ngrok:
        print("🔗 Using ngrok for Twilio Media Stream WSS:", ngrok)
        return http_to_ws_media_url(ngrok), ngrok, None

    from app.core.cloudflared_tunnel import media_stream_wss_base

    cf = media_stream_wss_base() or await ensure_cloudflared_tunnel(port=APP_PORT)
    if cf:
        return http_to_ws_media_url(cf), cf, None

    err = (
        "Twilio cannot use localhost or a Microsoft dev tunnel for Media Stream WSS. "
        "Wait for cloudflared to start, or set STREAM_PUBLIC_BASE_URL to an https URL."
    )
    print("❌", err)
    return None, None, err


async def resolve_twilio_stream_base(configured: str) -> tuple[str | None, dict | None]:
    """
    Resolve PUBLIC_BASE_URL for outbound calls (HTTP reachability required).
    WSS probe is advisory unless STRICT_WSS_PROBE=1 in .env.
    """
    from app.core.cloudflared_tunnel import ensure_cloudflared_tunnel, media_stream_wss_base
    from app.core.config import APP_PORT, AUTO_CLOUDFLARED_FOR_WSS, STRICT_WSS_PROBE

    if is_local_public_base(configured) and AUTO_CLOUDFLARED_FOR_WSS:
        cf = media_stream_wss_base() or await ensure_cloudflared_tunnel(port=APP_PORT)
        if cf:
            print("🌤️ Local PUBLIC_BASE_URL — Twilio will use cloudflared:", cf)
            return cf, None
        return None, {
            "ok": False,
            "error": "localhost is not reachable by Twilio and cloudflared did not start",
            "url": configured,
        }

    base, err = await asyncio.to_thread(resolve_reachable_public_base, configured)
    if not base:
        return None, err

    if is_dev_tunnel_url(base) and not STRICT_WSS_PROBE:
        return base, None

    wss = await probe_public_media_wss(base)
    if wss.get("ok"):
        print("✅ WSS /media-stream self-probe OK:", wss.get("url"))
        return base, None

    print(
        "⚠️ WSS self-probe failed (calls will still be placed):",
        wss.get("url"),
        wss.get("error"),
    )
    if STRICT_WSS_PROBE:
        return None, wss
    return base, None


def resolve_reachable_public_base(configured: str) -> tuple[str | None, dict | None]:
    """
    Pick a public base URL Twilio can reach.
    Tries configured PUBLIC_BASE_URL, then local ngrok API if configured URL fails.
    """
    configured = (configured or "").rstrip("/")
    candidates: list[tuple[str, str]] = []
    if configured:
        candidates.append(("env", configured))
    ngrok = discover_ngrok_public_url()
    if ngrok and ngrok not in {c for _, c in candidates}:
        candidates.append(("ngrok", ngrok))

    if not candidates:
        return None, {
            "ok": False,
            "status_code": None,
            "error": "PUBLIC_BASE_URL is not set and ngrok is not running on :4040",
            "url": "",
        }

    # Dev tunnels: do not block make-call on self-HTTP/WSS probes (browser/Twilio may still work).
    if configured and is_dev_tunnel_url(configured):
        print("🌤️ Dev tunnel PUBLIC_BASE_URL (skip probes):", configured)
        return configured, None

    last_probe = None
    for source, base in candidates:
        probe = probe_public_health_with_retries(base)
        last_probe = probe
        if probe["ok"]:
            if source == "ngrok" and configured and base != configured:
                print(
                    "🔗 PUBLIC_BASE_URL unreachable; using ngrok instead:",
                    base,
                    "(update .env PUBLIC_BASE_URL)",
                )
            elif source == "ngrok" and not configured:
                print("🔗 Using ngrok public URL:", base)
            return base, None

        if _recent_public_ok(base) and probe_local_health():
            print(
                "🌤️ Public probe timed out but tunnel worked recently — placing call anyway:",
                base,
            )
            return base, None

        if trust_public_url_despite_self_probe_fail(configured, base, probe):
            print(
                "🌤️ Public self-probe failed but local :8000 is healthy — using PUBLIC_BASE_URL:",
                base,
                f"({probe.get('error')})",
            )
            return base, None

    return None, last_probe


def public_urls_for_request(forwarded_proto: str | None, forwarded_host: str | None) -> tuple[str, str] | None:
    """Build http/ws base URLs from reverse-proxy headers (ngrok, dev tunnels, etc.)."""
    host = (forwarded_host or "").split(",")[0].strip()
    if not host or host.split(":")[0] in {"127.0.0.1", "localhost"}:
        return None
    scheme = (forwarded_proto or "https").split(",")[0].strip() or "https"
    return public_http_and_ws_bases(f"{scheme}://{host}")


def public_webhook_help(configured: str, probe: dict | None) -> str:
    detail = (probe or {}).get("error") or "unknown error"
    url = (probe or {}).get("url") or configured or "(not set)"
    return (
        f"Twilio cannot reach your app at {url} ({detail}). "
        "Keep uvicorn on port 8000, ensure the dev tunnel port is Public, and set PUBLIC_BASE_URL "
        "to the same https URL as your working /docs page (no trailing slash). "
        "Twilio also needs wss://YOUR-HOST/media-stream — if calls connect but AI is silent, "
        "check uvicorn for 'Media stream connected' or 'CALL ENDED WITHOUT MEDIA STREAM'."
    )
